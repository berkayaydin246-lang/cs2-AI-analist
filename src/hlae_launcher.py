"""
hlae_launcher.py
Launches a fresh HLAE + CS2 process for a single render job.
"""
from __future__ import annotations

import csv
import logging
import os
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.cs2_config import CS2Config
from src.cs2_controller import CS2Controller, CS2ControlError
from src.hlae_commands import build_cs2_command_line, build_custom_loader_launch
from src.render_recipe import RenderRecipe

log = logging.getLogger(__name__)


class HLAELaunchError(Exception):
    """Raised when HLAE/CS2 launch or shutdown fails."""


@dataclass
class HLAELaunchSession:
    command: list[str]
    process: subprocess.Popen | None = None
    stdout_log_path: str = ""
    log_handle: Any | None = None


@dataclass
class HLAELauncher:
    config: CS2Config
    session: HLAELaunchSession | None = field(default=None, init=False)

    def validate(self) -> None:
        if not self.config.hlae_exe or not Path(self.config.hlae_exe).exists():
            raise HLAELaunchError(f"HLAE executable not found: {self.config.hlae_exe}")
        if not self.config.cs2_exe or not Path(self.config.cs2_exe).exists():
            raise HLAELaunchError(f"CS2 executable not found: {self.config.cs2_exe}")
        if not self.config.hlae_hook_dll or not Path(self.config.hlae_hook_dll).exists():
            raise HLAELaunchError(f"HLAE hook DLL not found: {self.config.hlae_hook_dll}")

    def build_command(self, recipe: RenderRecipe) -> list[str]:
        self.validate()
        command_line = build_cs2_command_line(
            recipe,
            netcon_port=self.config.netcon_port,
            extra_launch_options=self.config.cs2_launch_options,
        )
        if self.config.hlae_launch_template:
            formatted = self.config.hlae_launch_template.format(
                hlae_exe=self.config.hlae_exe,
                cs2_exe=self.config.cs2_exe,
                hlae_hook_dll=self.config.hlae_hook_dll,
                cs2_cmdline=command_line,
                cs2_args=command_line,
                netcon_port=self.config.netcon_port,
                hlae_config_dir=self.config.hlae_config_dir,
            )
            return shlex.split(formatted, posix=False)
        return build_custom_loader_launch(
            hlae_exe=self.config.hlae_exe,
            cs2_exe=self.config.cs2_exe,
            hook_dll=self.config.hlae_hook_dll,
            command_line=command_line,
        )

    @staticmethod
    def _list_processes(image_name: str) -> list[dict[str, str]]:
        try:
            proc = subprocess.run(
                ["tasklist", "/FO", "CSV", "/NH", "/FI", f"IMAGENAME eq {image_name}"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except Exception:
            return []
        rows: list[dict[str, str]] = []
        for line in (proc.stdout or "").splitlines():
            line = line.strip()
            if not line or line.startswith("INFO:"):
                continue
            try:
                parsed = next(csv.reader([line]))
            except Exception:
                continue
            if len(parsed) < 2:
                continue
            rows.append({"image": parsed[0], "pid": parsed[1]})
        return rows

    def find_stale_processes(self) -> list[dict[str, str]]:
        stale: list[dict[str, str]] = []
        stale.extend(self._list_processes("HLAE.exe"))
        stale.extend(self._list_processes("cs2.exe"))
        return stale

    def cleanup_stale_processes(self) -> list[dict[str, str]]:
        stale = self.find_stale_processes()
        cleaned: list[dict[str, str]] = []
        for proc in stale:
            pid = proc.get("pid", "").strip('"')
            if not pid.isdigit():
                continue
            try:
                subprocess.run(
                    ["taskkill", "/PID", pid, "/T", "/F"],
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                )
                cleaned.append(proc)
            except Exception:
                continue
        return cleaned

    def launch(self, recipe: RenderRecipe, logs_dir: Path) -> HLAELaunchSession:
        logs_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = logs_dir / "hlae_stdout.log"
        command = self.build_command(recipe)
        log.info("Launching HLAE render session: %s", subprocess.list2cmdline(command))
        stdout_handle = open(stdout_path, "a", encoding="utf-8", buffering=1)
        env = os.environ.copy()
        if self.config.hlae_config_dir:
            env["USRLOCALCSGO"] = self.config.hlae_config_dir
        try:
            process = subprocess.Popen(
                command,
                stdout=stdout_handle,
                stderr=subprocess.STDOUT,
                cwd=str(Path(self.config.hlae_exe).parent),
                env=env,
            )
        except OSError as e:
            stdout_handle.close()
            raise HLAELaunchError(f"Failed to launch HLAE: {e}")
        self.session = HLAELaunchSession(
            command=command,
            process=process,
            stdout_log_path=str(stdout_path),
            log_handle=stdout_handle,
        )
        return self.session

    def wait_for_netcon(self, cs2: CS2Controller, timeout_s: float) -> None:
        if self.session is None or self.session.process is None:
            raise HLAELaunchError("No active HLAE session")
        deadline = time.time() + timeout_s
        last_error = ""
        while time.time() < deadline:
            if self.session.process.poll() is not None:
                raise HLAELaunchError(
                    f"HLAE/CS2 exited before netcon was ready (exit={self.session.process.returncode})"
                )
            try:
                cs2.ensure_netcon()
                return
            except CS2ControlError as e:
                last_error = str(e)
                time.sleep(0.5)
        raise HLAELaunchError(f"Timed out waiting for netcon readiness: {last_error}")

    def shutdown(self, grace_s: float = 5.0) -> None:
        session = self.session
        if session is None or session.process is None:
            return
        proc = session.process
        if proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=grace_s)
            except Exception:
                try:
                    subprocess.run(
                        ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                except Exception as e:
                    raise HLAELaunchError(f"Failed to terminate HLAE/CS2 tree: {e}")
        if session.log_handle is not None:
            try:
                session.log_handle.close()
            except Exception:
                pass
        self.session = None
