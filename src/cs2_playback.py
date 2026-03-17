"""
cs2_playback.py
High-level demo playback preparation and orchestration.

Consumes clip plan timing data and camera strategies from the existing
clip pipeline, and delegates the concrete CS2 orchestration to the
dedicated game-control service for a target clip window.

This module is the bridge between the clip planning system and the
render worker's game-control abstraction. Recording (OBS / game
capture) is layered on top of playback results from this module.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from src.cs2_controller import CS2Controller
from src.game_control import (
    CS2RuntimeOptions,
    CameraSelection,
    CommandChannelFailure,
    DemoLoadFailure,
    DemoSeekFailure,
    LaunchFailure,
    POVSelectionFailure,
    RenderPlaybackRequest,
    build_game_control_service,
    GameControlPort,
)
from src.render_modes import (
    CAMERA_MODES,
    OBSERVER_MODES,
    HUD_MODES,
    get_camera_strategy,
)

logger = logging.getLogger(__name__)


# ── Playback job status ──────────────────────────────────────────────────────

class PlaybackStatus(str, Enum):
    PENDING = "pending"
    VALIDATING = "validating"
    LAUNCHING_CS2 = "launching_cs2"
    CONNECTING = "connecting"
    LOADING_DEMO = "loading_demo"
    SEEKING = "seeking"
    APPLYING_CAMERA = "applying_camera"
    READY = "ready"
    PARTIALLY_READY = "partially_ready"
    BLOCKED = "blocked"
    FAILED = "failed"


# ── Playback job result ──────────────────────────────────────────────────────

@dataclass
class PlaybackResult:
    """Structured result of a playback preparation attempt."""
    job_id: str = ""
    status: PlaybackStatus = PlaybackStatus.PENDING
    demo_path: str = ""
    demo_found: bool = False
    round_number: int = 0
    start_tick: int = 0
    anchor_tick: int = 0
    end_tick: int = 0
    round_start_tick: int = 0
    requested_seek_tick: int = 0
    prepared_tick: int = 0

    # Demo staging and playback command path
    staged_demo_path: str = ""
    playdemo_path: str = ""
    replay_name: str = ""
    staging_reused: bool = False

    # Session contract details
    command_ready: bool = False
    camera_strategy_requested: str = ""
    camera_strategy_applied: str = ""
    session_status: str = "pending"
    load_confirmed: bool = False
    failure_code: str | None = None

    # Camera strategy
    requested_camera: dict = field(default_factory=dict)
    applied_camera: dict = field(default_factory=dict)
    camera_fully_applied: bool = False

    # CS2 state
    cs2_running: bool = False
    cs2_pid: int | None = None
    netcon_connected: bool = False

    # Timing
    started_at: str = ""
    completed_at: str = ""
    duration_ms: float = 0.0

    # Step results (detailed trace)
    steps: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "status": self.status.value,
            "demo_path": self.demo_path,
            "demo_found": self.demo_found,
            "round_number": self.round_number,
            "start_tick": self.start_tick,
            "anchor_tick": self.anchor_tick,
            "end_tick": self.end_tick,
            "round_start_tick": self.round_start_tick,
            "requested_seek_tick": self.requested_seek_tick,
            "prepared_tick": self.prepared_tick,
            "staged_demo_path": self.staged_demo_path,
            "playdemo_path": self.playdemo_path,
            "replay_name": self.replay_name,
            "staging_reused": self.staging_reused,
            "command_ready": self.command_ready,
            "camera_strategy_requested": self.camera_strategy_requested,
            "camera_strategy_applied": self.camera_strategy_applied,
            "session_status": self.session_status,
            "load_confirmed": self.load_confirmed,
            "failure_code": self.failure_code,
            "requested_camera": self.requested_camera,
            "applied_camera": self.applied_camera,
            "camera_fully_applied": self.camera_fully_applied,
            "cs2_running": self.cs2_running,
            "cs2_pid": self.cs2_pid,
            "netcon_connected": self.netcon_connected,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_ms": self.duration_ms,
            "steps": list(self.steps),
            "warnings": list(self.warnings),
            "error": self.error,
        }


# ── Playback input ───────────────────────────────────────────────────────────

def build_playback_job(
    demo_path: str,
    clip_plan: dict,
    *,
    camera_overrides: dict | None = None,
) -> dict:
    """Build a structured playback job from a clip plan.

    Extracts timing, camera strategy, and POV data from the clip plan
    and packages it for the playback preparation flow.
    """
    job_id = f"pb_{uuid.uuid4().hex[:12]}"

    # Timing from clip plan
    round_number = int(clip_plan.get("round_number") or 0)
    start_tick = int(clip_plan.get("start_tick") or 0)
    anchor_tick = int(clip_plan.get("anchor_tick") or start_tick)
    end_tick = int(clip_plan.get("end_tick") or anchor_tick)

    # Camera strategy from ingame_capture metadata or highlight type
    ingame_meta = (
        (clip_plan.get("metadata") or {})
        .get("planning_profile", {})
        .get("ingame_capture", {})
    )

    highlight_type = (clip_plan.get("metadata") or {}).get("source_highlight_type", "")
    if ingame_meta:
        camera_mode = ingame_meta.get("camera_mode", "observer_auto")
        observer_mode = ingame_meta.get("observer_mode", "first_person")
        hud_mode = ingame_meta.get("hud_mode", "default")
    elif highlight_type:
        strategy = get_camera_strategy(highlight_type)
        camera_mode = strategy["camera_mode"]
        observer_mode = strategy["observer_mode"]
        hud_mode = strategy["hud_mode"]
    else:
        camera_mode = "observer_auto"
        observer_mode = "first_person"
        hud_mode = "default"

    # Apply overrides
    overrides = camera_overrides or {}
    camera_mode = overrides.get("camera_mode", camera_mode)
    observer_mode = overrides.get("observer_mode", observer_mode)
    hud_mode = overrides.get("hud_mode", hud_mode)

    pov_player = clip_plan.get("pov_player") or clip_plan.get("primary_player")
    pov_player_steamid64 = clip_plan.get("pov_player_steamid64")

    # Round start tick (from clip plan metadata if available)
    round_bounds = (clip_plan.get("metadata") or {}).get("round_bounds", {})
    round_start_tick = int(round_bounds.get("start_tick") or start_tick)

    return {
        "job_id": job_id,
        "demo_path": demo_path,
        "round_number": round_number,
        "start_tick": start_tick,
        "anchor_tick": anchor_tick,
        "end_tick": end_tick,
        "round_start_tick": round_start_tick,
        "camera_mode": camera_mode,
        "observer_mode": observer_mode,
        "hud_mode": hud_mode,
        "pov_player": pov_player,
        "pov_player_steamid64": pov_player_steamid64,
        "clip_plan_id": str(clip_plan.get("clip_plan_id") or ""),
        "source_highlight_id": str(clip_plan.get("source_highlight_id") or ""),
    }


def validate_playback_job(job: dict) -> list[str]:
    """Validate a playback job before execution. Returns warning strings."""
    warnings: list[str] = []

    if not job.get("demo_path"):
        warnings.append("demo_path is empty")
    elif not Path(job["demo_path"]).is_file():
        warnings.append(f"demo file not found: {job['demo_path']}")

    if not job.get("round_number") or int(job.get("round_number", 0)) < 1:
        warnings.append("round_number is missing or invalid")

    start = int(job.get("start_tick", 0))
    anchor = int(job.get("anchor_tick", 0))
    end = int(job.get("end_tick", 0))
    if start <= 0 or anchor <= 0 or end <= 0:
        warnings.append("tick values must be positive")
    elif not (start <= anchor <= end):
        warnings.append(f"invalid tick window: start={start} anchor={anchor} end={end}")

    camera_mode = job.get("camera_mode", "")
    if camera_mode and camera_mode not in CAMERA_MODES:
        warnings.append(f"unknown camera_mode: {camera_mode}")

    observer_mode = job.get("observer_mode", "")
    if observer_mode and observer_mode not in OBSERVER_MODES:
        warnings.append(f"unknown observer_mode: {observer_mode}")

    hud_mode = job.get("hud_mode", "")
    if hud_mode and hud_mode not in HUD_MODES:
        warnings.append(f"unknown hud_mode: {hud_mode}")

    if camera_mode == "player_pov" and not job.get("pov_player"):
        warnings.append("camera_mode is player_pov but no pov_player specified")

    return warnings


# ── Playback preparation engine ──────────────────────────────────────────────

def prepare_playback(
    job: dict,
    *,
    control_service: GameControlPort | None = None,
    controller: CS2Controller | None = None,
    config: dict | None = None,
    skip_launch: bool = False,
) -> PlaybackResult:
    """Execute a full playback preparation sequence through the dedicated game-control service."""
    t0 = time.monotonic()
    result = PlaybackResult(
        job_id=job.get("job_id", f"pb_{uuid.uuid4().hex[:12]}"),
        started_at=datetime.now(timezone.utc).isoformat(),
        demo_path=job.get("demo_path", ""),
        round_number=int(job.get("round_number", 0)),
        start_tick=int(job.get("start_tick", 0)),
        anchor_tick=int(job.get("anchor_tick", 0)),
        end_tick=int(job.get("end_tick", 0)),
        round_start_tick=int(job.get("round_start_tick", 0)),
        requested_camera={
            "camera_mode": job.get("camera_mode"),
            "observer_mode": job.get("observer_mode"),
            "hud_mode": job.get("hud_mode"),
            "pov_player": job.get("pov_player"),
            "pov_player_steamid64": job.get("pov_player_steamid64"),
        },
        camera_strategy_requested=(
            f"{job.get('camera_mode', 'observer_auto')}|"
            f"{job.get('observer_mode', 'first_person')}|"
            f"{job.get('hud_mode', 'default')}"
        ),
    )

    runtime_source = config or (controller.config if controller is not None else {})
    runtime_options = CS2RuntimeOptions.from_mapping(runtime_source)
    service = control_service or build_game_control_service(
        config=runtime_source,
        controller=controller,
    )

    try:
        result.status = PlaybackStatus.VALIDATING
        job_warnings = validate_playback_job(job)
        result.steps.append({"step": "validate", "warnings": job_warnings})
        if job_warnings:
            result.warnings.extend(job_warnings)

        demo_path = job.get("demo_path", "")
        if demo_path and Path(demo_path).is_file():
            result.demo_found = True
        else:
            result.demo_found = False
            result.status = PlaybackStatus.FAILED
            result.failure_code = "demo_not_accessible"
            result.error = f"Demo file not found: {demo_path}"
            result.steps.append({"step": "demo_check", "found": False, "path": demo_path})
            return _finalize(result, t0)

        result.steps.append({"step": "demo_check", "found": True, "path": demo_path})

        request = RenderPlaybackRequest(
            job_id=result.job_id,
            demo_path=demo_path,
            round_number=result.round_number,
            start_tick=result.start_tick,
            anchor_tick=result.anchor_tick,
            end_tick=result.end_tick,
            round_start_tick=int(job.get("round_start_tick", 0)),
            pre_roll_ticks=max(0, int(job.get("pre_roll_ticks", 0))),
            worker_tag=str(job.get("worker_tag") or os.environ.get("CS2_WORKER_TAG") or "default"),
            camera=CameraSelection(
                camera_mode=str(job.get("camera_mode", "observer_auto")),
                observer_mode=str(job.get("observer_mode", "first_person")),
                hud_mode=str(job.get("hud_mode", "default")),
                pov_player=job.get("pov_player"),
                pov_player_steamid64=job.get("pov_player_steamid64"),
            ),
        )

        session = service.prepare_render_playback(request, skip_launch=skip_launch)
        result.cs2_running = session.runtime_started
        result.cs2_pid = session.process_id
        result.netcon_connected = session.command_channel_ready
        result.command_ready = session.command_channel_ready
        result.load_confirmed = session.load_confirmed
        result.staged_demo_path = session.staged_demo_path
        result.playdemo_path = session.playdemo_path
        result.replay_name = session.replay_name
        result.prepared_tick = session.prepared_tick
        result.requested_seek_tick = session.requested_seek_tick
        result.steps.extend(session.steps)
        result.warnings.extend(session.warnings)
        result.applied_camera = session.applied_camera
        result.camera_strategy_applied = (
            f"{result.applied_camera.get('camera_mode')}|"
            f"{result.applied_camera.get('observer_mode')}|"
            f"{result.applied_camera.get('hud_mode')}"
        )
        result.camera_fully_applied = bool(session.applied_camera) and not session.warnings

        if result.warnings:
            result.status = PlaybackStatus.PARTIALLY_READY
        else:
            result.status = PlaybackStatus.READY
        result.session_status = result.status.value
        return _finalize(result, t0)

    except CommandChannelFailure as exc:
        result.status = PlaybackStatus.BLOCKED
        result.failure_code = exc.code
        result.error = str(exc)
        result.warnings.append("Ensure CS2 launch options include -usercon -netconport and reconnect.")
        return _finalize(result, t0)
    except LaunchFailure as exc:
        result.status = PlaybackStatus.FAILED
        result.failure_code = exc.code
        result.error = str(exc)
        return _finalize(result, t0)
    except DemoLoadFailure as exc:
        result.status = PlaybackStatus.FAILED
        result.failure_code = exc.code
        result.error = str(exc)
        return _finalize(result, t0)
    except DemoSeekFailure as exc:
        result.status = PlaybackStatus.FAILED
        result.failure_code = exc.code
        result.error = str(exc)
        return _finalize(result, t0)
    except POVSelectionFailure as exc:
        result.status = PlaybackStatus.FAILED
        result.failure_code = exc.code
        result.error = str(exc)
        return _finalize(result, t0)
    except Exception as exc:
        result.status = PlaybackStatus.FAILED
        result.failure_code = result.failure_code or "unexpected_exception"
        result.error = str(exc)
        logger.error("Playback preparation failed: %s", exc, exc_info=True)
        return _finalize(result, t0)
    finally:
        if control_service is None:
            service.close()

def _finalize(result: PlaybackResult, t0: float) -> PlaybackResult:
    """Fill in timing fields and return."""
    result.session_status = result.status.value
    result.completed_at = datetime.now(timezone.utc).isoformat()
    result.duration_ms = round((time.monotonic() - t0) * 1000, 1)
    return result


# ── Convenience: prepare from clip plan directly ──────────────────────────────

def prepare_clip_playback(
    demo_path: str,
    clip_plan: dict,
    *,
    config: dict | None = None,
    camera_overrides: dict | None = None,
    skip_launch: bool = False,
) -> dict:
    """End-to-end convenience: build job from clip plan → prepare playback.

    Returns the PlaybackResult as a dict.
    """
    job = build_playback_job(demo_path, clip_plan, camera_overrides=camera_overrides)
    result = prepare_playback(job, config=config, skip_launch=skip_launch)
    return result.to_dict()
