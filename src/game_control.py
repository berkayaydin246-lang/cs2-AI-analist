"""
game_control.py
Dedicated CS2 game-control abstraction for render workers.

This module isolates:
  - machine/runtime configuration
  - process launch management
  - demo playback control
  - POV / observer camera selection

from the higher-level render pipeline.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from src.cs2_config import build_cs2_config, get_cs2_launch_args
from src.cs2_controller import CS2Controller, CS2Status, NetconStatus

logger = logging.getLogger(__name__)


class GameControlError(RuntimeError):
    code = "game_control_error"

    def __init__(self, message: str, *, detail: dict[str, Any] | None = None):
        super().__init__(message)
        self.detail = detail or {}


class LaunchFailure(GameControlError):
    code = "launch_failure"


class DemoLoadFailure(GameControlError):
    code = "demo_load_failure"


class DemoSeekFailure(GameControlError):
    code = "demo_seek_failure"


class POVSelectionFailure(GameControlError):
    code = "pov_selection_failure"


class CommandChannelFailure(GameControlError):
    code = "command_channel_failure"


@dataclass(frozen=True)
class CS2RuntimeOptions:
    control_backend: str
    cs2_exe: str
    steam_path: str | None
    launch_options: str
    skip_launch: bool
    netcon_port: int
    fullscreen: bool
    width: int
    height: int
    launch_timeout: int
    demo_load_timeout: int
    seek_timeout: int
    command_delay: float
    demo_ready_settle_s: float
    post_seek_settle_s: float
    use_coarse_round_seek: bool
    use_netcon: bool
    allow_ui_fallback: bool
    demo_dir: str | None = None
    demo_stage_subdir: str = "replays/cs2coach"
    process_name: str = "cs2.exe"
    window_title: str = "Counter-Strike 2"

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any] | None = None) -> "CS2RuntimeOptions":
        cfg = build_cs2_config(mapping or {})
        return cls(
            control_backend=str(cfg.get("control_backend") or "plain").strip().lower(),
            cs2_exe=str(cfg.get("cs2_exe") or ""),
            steam_path=str(cfg.get("steam_path") or "") or None,
            launch_options=str(cfg.get("launch_options") or ""),
            skip_launch=bool(cfg.get("skip_launch", False)),
            netcon_port=int(cfg.get("netcon_port") or 2121),
            fullscreen=bool(cfg.get("fullscreen")),
            width=int(cfg.get("width") or 1920),
            height=int(cfg.get("height") or 1080),
            launch_timeout=int(cfg.get("launch_timeout") or 45),
            demo_load_timeout=int(cfg.get("demo_load_timeout") or 30),
            seek_timeout=int(cfg.get("seek_timeout") or 15),
            command_delay=float(cfg.get("command_delay") or 0.3),
            demo_ready_settle_s=float(cfg.get("demo_ready_settle_s") or 8.0),
            post_seek_settle_s=float(cfg.get("post_seek_settle_s") or 4.0),
            use_coarse_round_seek=bool(cfg.get("use_coarse_round_seek", False)),
            use_netcon=bool(cfg.get("use_netcon", True)),
            allow_ui_fallback=bool(cfg.get("allow_ui_fallback", False)),
            demo_dir=str(cfg.get("demo_dir") or "") or None,
            demo_stage_subdir=str(cfg.get("demo_stage_subdir") or "replays/cs2coach"),
            process_name=str(cfg.get("process_name") or "cs2.exe"),
            window_title=str(cfg.get("window_title") or "Counter-Strike 2"),
        )

    def to_controller_config(self) -> dict[str, Any]:
        return asdict(self)

    def build_launch_command(self, demo_path: str | None = None) -> list[str]:
        return get_cs2_launch_args(self.to_controller_config(), demo_path=demo_path)


@dataclass(frozen=True)
class CameraSelection:
    camera_mode: str = "observer_auto"
    observer_mode: str = "first_person"
    hud_mode: str = "default"
    pov_player: str | None = None
    pov_player_steamid64: str | None = None


@dataclass(frozen=True)
class RenderPlaybackRequest:
    job_id: str
    demo_path: str
    round_number: int
    start_tick: int
    anchor_tick: int
    end_tick: int
    round_start_tick: int = 0
    pre_roll_ticks: int = 0
    worker_tag: str = "default"
    camera: CameraSelection = field(default_factory=CameraSelection)


@dataclass
class RenderPlaybackSession:
    job_id: str
    demo_path: str
    staged_demo_path: str = ""
    playdemo_path: str = ""
    replay_name: str = ""
    process_id: int | None = None
    runtime_started: bool = False
    command_channel_ready: bool = False
    load_confirmed: bool = False
    prepared_tick: int = 0
    requested_seek_tick: int = 0
    steps: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    applied_camera: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "demo_path": self.demo_path,
            "staged_demo_path": self.staged_demo_path,
            "playdemo_path": self.playdemo_path,
            "replay_name": self.replay_name,
            "process_id": self.process_id,
            "runtime_started": self.runtime_started,
            "command_channel_ready": self.command_channel_ready,
            "load_confirmed": self.load_confirmed,
            "prepared_tick": self.prepared_tick,
            "requested_seek_tick": self.requested_seek_tick,
            "steps": list(self.steps),
            "warnings": list(self.warnings),
            "applied_camera": dict(self.applied_camera),
        }


class GameControlPort(Protocol):
    runtime_options: CS2RuntimeOptions

    def prepare_render_playback(self, request: RenderPlaybackRequest, *, skip_launch: bool = False) -> RenderPlaybackSession:
        ...

    def pause_demo(self) -> dict[str, Any]:
        ...

    def resume_demo(self) -> dict[str, Any]:
        ...

    def set_demo_timescale(self, value: float) -> dict[str, Any]:
        ...

    def reapply_last_camera(self) -> dict[str, Any]:
        ...

    def close(self) -> None:
        ...


class CS2GameControlService:
    def __init__(
        self,
        runtime_options: CS2RuntimeOptions | None = None,
        *,
        controller: CS2Controller | None = None,
    ):
        self.runtime_options = runtime_options or CS2RuntimeOptions.from_mapping()
        self._controller = controller or CS2Controller(self.runtime_options.to_controller_config())
        self._last_camera_request: CameraSelection | None = None

    @property
    def controller(self) -> CS2Controller:
        return self._controller

    def prepare_render_playback(self, request: RenderPlaybackRequest, *, skip_launch: bool = False) -> RenderPlaybackSession:
        session = RenderPlaybackSession(job_id=request.job_id, demo_path=request.demo_path)
        logger.info(
            "Preparing CS2 render playback job=%s round=%s ticks=%s-%s-%s pov=%s",
            request.job_id,
            request.round_number,
            request.start_tick,
            request.anchor_tick,
            request.end_tick,
            request.camera.pov_player,
        )

        self._validate_request(request)
        self._ensure_runtime(skip_launch=skip_launch, session=session)
        self._ensure_command_channel(session=session, skip_launch=skip_launch)
        self._load_demo(request, session=session)
        self._seek_demo(request, session=session)
        self._apply_camera(request, session=session)
        return session

    def pause_demo(self) -> dict[str, Any]:
        return self.controller.demo_pause()

    def resume_demo(self) -> dict[str, Any]:
        return self.controller.demo_resume()

    def set_demo_timescale(self, value: float) -> dict[str, Any]:
        return self.controller.demo_timescale(value)

    def close(self) -> None:
        self.controller.close()

    def reapply_last_camera(self) -> dict[str, Any]:
        camera = self._last_camera_request
        if camera is None:
            return {
                "success": False,
                "error": "no_camera_request_recorded",
            }
        return self.controller.apply_camera_strategy(
            camera_mode=camera.camera_mode,
            observer_mode=camera.observer_mode,
            hud_mode=camera.hud_mode,
            pov_player=camera.pov_player,
        )

    def _validate_request(self, request: RenderPlaybackRequest) -> None:
        if not request.demo_path or not Path(request.demo_path).is_file():
            raise DemoLoadFailure(
                f"Demo file not found: {request.demo_path}",
                detail={"demo_path": request.demo_path},
            )
        exe = self.runtime_options.cs2_exe
        if not exe or not Path(exe).is_file():
            raise LaunchFailure(
                f"CS2 executable not found: {exe or 'unset'}",
                detail={"cs2_exe": exe},
            )
        if request.start_tick <= 0 or request.anchor_tick <= 0 or request.end_tick <= 0:
            raise DemoSeekFailure(
                "Playback tick range is invalid",
                detail={"start_tick": request.start_tick, "anchor_tick": request.anchor_tick, "end_tick": request.end_tick},
            )
        if not (request.start_tick <= request.anchor_tick <= request.end_tick):
            raise DemoSeekFailure(
                "Playback tick order is invalid",
                detail={"start_tick": request.start_tick, "anchor_tick": request.anchor_tick, "end_tick": request.end_tick},
            )

    def _ensure_runtime(self, *, skip_launch: bool, session: RenderPlaybackSession) -> None:
        session.steps.append({
            "step": "launch_plan",
            "launch_command": self.runtime_options.build_launch_command(),
            "skip_launch": skip_launch,
        })
        if skip_launch:
            state = self.controller.check_status()
        else:
            state = self.controller.ensure_running()
        session.runtime_started = state.cs2_status == CS2Status.RUNNING
        session.process_id = state.pid
        session.steps.append({
            "step": "runtime_state",
            "cs2_status": state.cs2_status.value,
            "pid": state.pid,
            "warnings": list(state.warnings),
            "error": state.error,
        })
        if not session.runtime_started:
            raise LaunchFailure(
                state.error or "Failed to launch CS2 runtime",
                detail={"cs2_status": state.cs2_status.value, "pid": state.pid, "warnings": list(state.warnings)},
            )

    def _ensure_command_channel(self, *, session: RenderPlaybackSession, skip_launch: bool) -> None:
        netcon_status = self.controller.connect_netcon()
        session.command_channel_ready = netcon_status == NetconStatus.CONNECTED
        session.steps.append({
            "step": "connect_netcon",
            "netcon_status": netcon_status.value,
            "port": self.runtime_options.netcon_port,
        })

        if session.command_channel_ready:
            return

        if not skip_launch:
            logger.warning("Netcon unavailable, restarting CS2 with configured launch profile")
            relaunch_state = self.controller.restart_with_netcon()
            session.process_id = relaunch_state.pid
            session.runtime_started = relaunch_state.cs2_status == CS2Status.RUNNING
            session.steps.append({
                "step": "relaunch_with_netcon",
                "cs2_status": relaunch_state.cs2_status.value,
                "pid": relaunch_state.pid,
                "error": relaunch_state.error,
            })
            if session.runtime_started:
                time.sleep(10.0)
                reconnect = self.controller.connect_netcon()
                session.command_channel_ready = reconnect == NetconStatus.CONNECTED
                session.steps.append({
                    "step": "reconnect_netcon",
                    "netcon_status": reconnect.value,
                    "port": self.runtime_options.netcon_port,
                })

        if not session.command_channel_ready:
            raise CommandChannelFailure(
                "Netcon command channel unavailable for CS2 render control",
                detail={"netcon_port": self.runtime_options.netcon_port},
            )

    def _load_demo(self, request: RenderPlaybackRequest, *, session: RenderPlaybackSession) -> None:
        load_result = self.controller.load_demo(request.demo_path, worker_tag=request.worker_tag)
        session.staged_demo_path = str(load_result.get("staged_path") or "")
        session.playdemo_path = str(load_result.get("playdemo_path") or "")
        session.replay_name = str(load_result.get("replay_name") or "")
        session.steps.append({"step": "load_demo", **load_result})

        if not load_result.get("success"):
            raise DemoLoadFailure(
                str(load_result.get("message") or load_result.get("error") or "Demo load failed"),
                detail=load_result,
            )

        loaded = self._wait_for_demo_load(timeout_s=self.runtime_options.demo_load_timeout)
        session.load_confirmed = loaded
        session.steps.append({
            "step": "wait_for_demo_load",
            "loaded": loaded,
            "timeout_s": self.runtime_options.demo_load_timeout,
        })
        if not loaded:
            # Check if CS2 is still alive — crash during load is a common
            # cause (e.g. "FATAL ERROR: CopyNewEntity: invalid class index")
            # when a demo was recorded on a different CS2 build.
            post_state = self.controller.check_status()
            if post_state.cs2_status != CS2Status.RUNNING:
                raise DemoLoadFailure(
                    "CS2 crashed during demo load. This usually means the demo "
                    "was recorded on a different CS2 version (entity class mismatch). "
                    "Try with a demo recorded on the current CS2 build.",
                    detail={
                        "playdemo_path": session.playdemo_path,
                        "cs2_status": post_state.cs2_status.value,
                        "cause": "cs2_crash_during_load",
                    },
                )
            raise DemoLoadFailure(
                f"Demo load not confirmed after {self.runtime_options.demo_load_timeout}s",
                detail={"playdemo_path": session.playdemo_path},
            )

        if self.runtime_options.demo_ready_settle_s > 0:
            session.steps.append({
                "step": "demo_ready_settle",
                "seconds": self.runtime_options.demo_ready_settle_s,
            })
            time.sleep(float(self.runtime_options.demo_ready_settle_s))

    def _seek_demo(self, request: RenderPlaybackRequest, *, session: RenderPlaybackSession) -> None:
        target_tick = int(request.start_tick)
        seek_tick = max(0, target_tick)
        if request.round_start_tick > 0:
            seek_tick = max(int(request.round_start_tick), seek_tick)
        session.requested_seek_tick = seek_tick

        if self.runtime_options.use_coarse_round_seek and request.round_start_tick > 0 and request.round_start_tick < seek_tick:
            coarse = self.controller.demo_goto_tick(int(request.round_start_tick))
            session.steps.append({"step": "seek_round_start", "tick": int(request.round_start_tick), **coarse})
            if not coarse.get("success"):
                raise DemoSeekFailure(
                    str(coarse.get("error") or "Failed to seek to round start"),
                    detail=coarse,
                )
            time.sleep(3.0)

        fine = self.controller.demo_goto_tick(seek_tick)
        session.steps.append({"step": "seek_target", "tick": seek_tick, **fine})
        if not fine.get("success"):
            raise DemoSeekFailure(
                str(fine.get("error") or "Failed to seek to target tick"),
                detail=fine,
            )

        time.sleep(float(self.runtime_options.post_seek_settle_s))
        state = self.controller.check_status()
        session.steps.append({
            "step": "post_seek_runtime_check",
            "cs2_status": state.cs2_status.value,
            "pid": state.pid,
        })
        if state.cs2_status != CS2Status.RUNNING:
            raise DemoSeekFailure(
                "CS2 process is no longer running after seek. This strongly suggests "
                "demo_gototick/playback-state instability for this demo/runtime combination.",
                detail={"cs2_status": state.cs2_status.value, "pid": state.pid},
            )

        pause = self.controller.demo_pause()
        session.steps.append({"step": "pause_demo", **pause})
        if not pause.get("success"):
            raise DemoSeekFailure(
                str(pause.get("error") or "Failed to pause demo after seek"),
                detail=pause,
            )
        session.prepared_tick = seek_tick

    def _apply_camera(self, request: RenderPlaybackRequest, *, session: RenderPlaybackSession) -> None:
        self._last_camera_request = request.camera
        if request.camera.pov_player_steamid64:
            logger.info(
                "Camera target: player=%s steamid64=%s mode=%s",
                request.camera.pov_player,
                request.camera.pov_player_steamid64,
                request.camera.camera_mode,
            )

        # Camera commands (especially spec_player_by_name) work more reliably
        # when the demo is playing.  demo_gototick + demo_pause leaves CS2 in
        # a frozen state where spectator commands may silently fail.
        # Briefly resume → apply camera → re-pause.
        is_player_pov = request.camera.camera_mode == "player_pov" and request.camera.pov_player
        if is_player_pov:
            logger.info("Briefly resuming demo for camera application (player_pov)")
            self.controller.demo_resume()
            time.sleep(0.5)

        cam_result = self.controller.apply_camera_strategy(
            camera_mode=request.camera.camera_mode,
            observer_mode=request.camera.observer_mode,
            hud_mode=request.camera.hud_mode,
            pov_player=request.camera.pov_player,
        )

        if is_player_pov:
            # Re-pause after camera is applied.  The demo advanced by ~1-2s
            # during camera setup, which is within the clip's pre-roll window.
            time.sleep(0.3)
            pause = self.controller.demo_pause()
            session.steps.append({"step": "repause_after_camera", **pause})

        session.applied_camera = dict(cam_result.get("applied") or {})
        warnings = list(cam_result.get("warnings") or [])
        session.warnings.extend(warnings)
        session.steps.append({"step": "apply_camera", **cam_result})
        if not cam_result.get("success", False):
            raise POVSelectionFailure(
                "Failed to apply POV / camera strategy",
                detail=cam_result,
            )

    def _wait_for_demo_load(self, *, timeout_s: int) -> bool:
        waited = 0.0
        time.sleep(3.0)
        waited += 3.0
        poll_interval = 2.0
        while waited < float(timeout_s):
            # Check if CS2 crashed during demo load (e.g. invalid class index)
            state = self.controller.check_status()
            if state.cs2_status != CS2Status.RUNNING:
                logger.error(
                    "CS2 process died during demo load (possible engine error "
                    "like 'invalid class index'). The demo may be incompatible "
                    "with the current CS2 version."
                )
                return False
            if self.controller.is_demo_playing():
                return True
            time.sleep(poll_interval)
            waited += poll_interval
        return False


def build_game_control_service(
    *,
    config: dict[str, Any] | None = None,
    controller: CS2Controller | None = None,
) -> GameControlPort:
    runtime_options = CS2RuntimeOptions.from_mapping(config or {})
    if runtime_options.control_backend == "hlae":
        from src.hlae_adapter import HLAEGameControlService

        return HLAEGameControlService(
            runtime_options=runtime_options,
            controller=controller,
            config=config or runtime_options.to_controller_config(),
        )
    return CS2GameControlService(
        runtime_options=runtime_options,
        controller=controller,
    )
