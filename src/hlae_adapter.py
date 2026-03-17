"""
hlae_adapter.py
Optional HLAE-enabled game-control backend.

This module keeps HLAE-specific launch/config logic isolated behind the
same game-control interface used by the render pipeline.
"""

from __future__ import annotations

import logging
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.cs2_config import build_cs2_config, get_hlae_launch_args
from src.cs2_controller import CS2Controller, CS2Status
from src.game_control import (
    CS2GameControlService,
    CS2RuntimeOptions,
    LaunchFailure,
    RenderPlaybackSession,
)

logger = logging.getLogger(__name__)


class HLAEConfigurationError(LaunchFailure):
    code = "hlae_configuration_error"


class HLAELaunchFailure(LaunchFailure):
    code = "hlae_launch_failure"


@dataclass(frozen=True)
class HLAEOptions:
    enabled: bool
    hlae_exe: str
    launch_template: str
    hlae_args: str
    config_dir: str | None
    hook_dll: str | None

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any] | None = None) -> "HLAEOptions":
        cfg = build_cs2_config(mapping or {})
        return cls(
            enabled=str(cfg.get("control_backend") or "plain").strip().lower() == "hlae",
            hlae_exe=str(cfg.get("hlae_exe") or "").strip(),
            launch_template=str(cfg.get("hlae_launch_template") or "").strip(),
            hlae_args=str(cfg.get("hlae_args") or "").strip(),
            config_dir=str(cfg.get("hlae_config_dir") or "").strip() or None,
            hook_dll=str(cfg.get("hlae_hook_dll") or "").strip() or None,
        )

    def validate(self) -> None:
        if not self.enabled:
            return
        if not self.hlae_exe:
            raise HLAEConfigurationError("HLAE backend requested but HLAE_EXE is not configured")
        if not Path(self.hlae_exe).is_file():
            raise HLAEConfigurationError(f"HLAE executable not found: {self.hlae_exe}")
        if not self.launch_template:
            raise HLAEConfigurationError(
                "HLAE backend requested but HLAE_LAUNCH_TEMPLATE is not configured"
            )
        if self.config_dir and not Path(self.config_dir).is_dir():
            raise HLAEConfigurationError(f"HLAE config dir does not exist: {self.config_dir}")
        if self.hook_dll and not Path(self.hook_dll).is_file():
            raise HLAEConfigurationError(f"HLAE hook DLL does not exist: {self.hook_dll}")


class HLAEGameControlService(CS2GameControlService):
    def __init__(
        self,
        runtime_options: CS2RuntimeOptions | None = None,
        *,
        hlae_options: HLAEOptions | None = None,
        controller: CS2Controller | None = None,
        config: dict[str, Any] | None = None,
    ):
        runtime_options = runtime_options or CS2RuntimeOptions.from_mapping(config or {})
        super().__init__(runtime_options=runtime_options, controller=controller)
        self.hlae_options = hlae_options or HLAEOptions.from_mapping(config or runtime_options.to_controller_config())
        self._launch_config = build_cs2_config(config or runtime_options.to_controller_config())
        self.hlae_options.validate()

    def _ensure_runtime(self, *, skip_launch: bool, session: RenderPlaybackSession) -> None:
        launch_command = get_hlae_launch_args(self._launch_config)
        session.steps.append({
            "step": "launch_plan",
            "backend": "hlae",
            "launch_command": launch_command,
            "skip_launch": skip_launch,
        })

        if skip_launch:
            state = self.controller.check_status()
            session.runtime_started = state.cs2_status == CS2Status.RUNNING
            session.process_id = state.pid
            session.steps.append({
                "step": "runtime_state",
                "backend": "hlae",
                "cs2_status": state.cs2_status.value,
                "pid": state.pid,
                "warnings": list(state.warnings),
                "error": state.error,
            })
            if not session.runtime_started:
                raise HLAELaunchFailure(
                    state.error or "HLAE backend requested with skip_launch, but CS2 is not running",
                    detail={"backend": "hlae", "cs2_status": state.cs2_status.value},
                )
            return

        # If CS2 is already running, reuse it.
        current = self.controller.check_status()
        if current.cs2_status == CS2Status.RUNNING:
            session.runtime_started = True
            session.process_id = current.pid
            session.steps.append({
                "step": "runtime_state",
                "backend": "hlae",
                "cs2_status": current.cs2_status.value,
                "pid": current.pid,
                "warnings": list(current.warnings),
                "error": current.error,
                "reused_running_process": True,
            })
            return

        cwd = str(Path(self.hlae_options.hlae_exe).parent)
        logger.info("Launching HLAE-backed CS2 runtime: %s", launch_command)
        try:
            subprocess.Popen(
                launch_command,
                cwd=cwd,
                close_fds=True,
            )
        except Exception as exc:
            raise HLAELaunchFailure(
                f"Failed to launch HLAE runtime: {exc}",
                detail={"launch_command": launch_command, "cwd": cwd},
            ) from exc

        deadline = time.monotonic() + float(self.runtime_options.launch_timeout)
        last_state = current
        while time.monotonic() < deadline:
            time.sleep(2.0)
            last_state = self.controller.check_status()
            if last_state.cs2_status == CS2Status.RUNNING:
                session.runtime_started = True
                session.process_id = last_state.pid
                session.steps.append({
                    "step": "runtime_state",
                    "backend": "hlae",
                    "cs2_status": last_state.cs2_status.value,
                    "pid": last_state.pid,
                    "warnings": list(last_state.warnings),
                    "error": last_state.error,
                    "hlae_launch_confirmed": True,
                })
                return

        raise HLAELaunchFailure(
            "HLAE launch command finished but CS2 runtime was not detected before timeout",
            detail={
                "backend": "hlae",
                "launch_timeout": self.runtime_options.launch_timeout,
                "last_cs2_status": last_state.cs2_status.value,
                "launch_command": launch_command,
            },
        )
