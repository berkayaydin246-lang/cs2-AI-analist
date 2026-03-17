"""
ingame_capture.py
End-to-end in-game clip capture renderer (hardened).

Orchestrates:
  1. Environment validation (CS2, OBS, demo, output dir)
  2. CS2 demo playback preparation (via cs2_playback)
  3. OBS recording capture (via obs_controller)
  4. Output file relocation and validation
  5. Integration with clip_store

Uses RenderJob from capture_pipeline for stage-level tracking,
per-stage timing, and structured diagnostics.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2

from src.capture_pipeline import (
    Readiness,
    RenderJob,
    RenderJobStatus,
    validate_capture_environment,
    validate_output_file,
)
from src.capture_execution import (
    CaptureExecutionRequest,
    CaptureExecutionService,
    CaptureExecutionStatus,
    OBSCaptureBackend,
    promote_raw_capture_to_final,
)
from src.cs2_config import build_cs2_config
from src.cs2_controller import CS2Controller
from src.game_control import build_game_control_service
from src.cs2_playback import (
    PlaybackStatus,
    build_playback_job,
    prepare_playback,
)
from src.obs_controller import build_obs_config
from src.render_modes import RENDER_MODE_INGAME_CAPTURE
from src.utils import safe_slug, generated_url, atomic_json_write

logger = logging.getLogger(__name__)
CLIP_ARTIFACT_SCHEMA_VERSION = 1

# Tick rate for CS2 demos (ticks per second)
CS2_TICK_RATE = 64

# OBS starts recording before we resume demo playback, so capture-side
# pre-roll should stay at zero. The clip plan's start_tick already includes
# the planning window expansion we want to show.
DEFAULT_CAPTURE_PRE_ROLL_S = 0.0
DEFAULT_CAPTURE_POST_ROLL_S = 1.5

def render_ingame_clip(
    demo_path: str,
    demo_id: str,
    clip_plan: dict,
    output_root: str | Path,
    *,
    target_settings: dict | None = None,
    cs2_config: dict | None = None,
    obs_config: dict | None = None,
    camera_overrides: dict | None = None,
    skip_cs2_launch: bool = False,
) -> dict:
    """Execute a full in-game clip capture with hardened stage tracking.

    Returns:
        Artifact dict compatible with clip_store.register_clip().
        Always includes 'render_job' with full stage diagnostics.
    """
    job_t0 = time.monotonic()
    cs2_cfg = cs2_config or build_cs2_config()
    skip_cs2_launch = bool(skip_cs2_launch or cs2_cfg.get("skip_launch"))
    obs_cfg = obs_config or build_obs_config()

    clip_plan_id = str(clip_plan.get("clip_plan_id") or "")
    clip_id = f"clip_{safe_slug(clip_plan_id)}_{uuid.uuid4().hex[:8]}"
    clip_dir = Path(output_root) / "clips" / safe_slug(demo_id) / safe_slug(clip_id)
    clip_dir.mkdir(parents=True, exist_ok=True)
    video_path = clip_dir / "clip.mp4"
    meta_path = clip_dir / "artifact.json"

    # Timing from clip plan
    start_tick = int(clip_plan.get("start_tick") or 0)
    anchor_tick = int(clip_plan.get("anchor_tick") or start_tick)
    end_tick = int(clip_plan.get("end_tick") or anchor_tick)
    clip_duration_ticks = max(1, end_tick - start_tick)
    clip_duration_s = clip_duration_ticks / CS2_TICK_RATE

    # Initialize render job tracker
    job = RenderJob(
        job_id=f"rj_{uuid.uuid4().hex[:12]}",
        clip_id=clip_id,
        demo_id=demo_id,
        started_at=datetime.now(timezone.utc).isoformat(),
    )

    # Build the artifact shell
    artifact: dict[str, Any] = {
        "artifact_schema_version": CLIP_ARTIFACT_SCHEMA_VERSION,
        "clip_id": clip_id,
        "clip_plan_id": clip_plan_id,
        "source_highlight_id": str(clip_plan.get("source_highlight_id") or ""),
        "demo_id": demo_id,
        "asset_dir": str(clip_dir),
        "status": "failed",
        "output_path": str(video_path),
        "output_url": generated_url(video_path),
        "thumbnail_path": None,
        "thumbnail_url": None,
        "duration_s": 0.0,
        "frame_count": 0,
        "render_mode": RENDER_MODE_INGAME_CAPTURE,
        "pov_mode": str(clip_plan.get("pov_mode") or "auto"),
        "pov_player": clip_plan.get("pov_player"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "warnings": [],
        "error": None,
        "job": {
            "demo_path": demo_path,
            "render_mode": RENDER_MODE_INGAME_CAPTURE,
            "round_number": int(clip_plan.get("round_number") or 0),
            "start_tick": start_tick,
            "anchor_tick": anchor_tick,
            "end_tick": end_tick,
            "clip_duration_s": round(clip_duration_s, 2),
            "camera_mode": None,
            "observer_mode": None,
            "hud_mode": None,
            "postprocess_settings": dict((target_settings or {}).get("postprocess") or {}),
        },
        "metadata_path": str(meta_path),
        "playback_result": None,
        "capture_result": None,
        "capture_execution": None,
        "render_job": None,
        "output_validation": None,
        "artifacts": {
            "base_dir": str(clip_dir),
            "video": {"path": str(video_path), "url": generated_url(video_path), "kind": "video/mp4"},
            "thumbnail": {"path": None, "url": None, "kind": "image/jpeg"},
            "metadata": {"path": str(meta_path), "url": generated_url(meta_path), "kind": "application/json"},
        },
    }

    ctrl = CS2Controller(cs2_cfg)
    control_service = build_game_control_service(
        config=cs2_cfg,
        controller=ctrl,
    )
    capture_backend = OBSCaptureBackend(obs_cfg)
    capture_executor = CaptureExecutionService(
        game_control=control_service,
        backend=capture_backend,
        logger_=logger,
    )

    try:
        # ── Stage 1: Environment validation ───────────────────────────────
        stage_t0 = job.enter_stage(RenderJobStatus.VALIDATING_ENVIRONMENT)

        env_check = validate_capture_environment(
            demo_path=demo_path,
            output_dir=str(clip_dir),
            cs2_config=cs2_cfg,
            obs_config=obs_cfg,
            check_obs_connection=True,
            check_cs2_process=True,
        )
        job.environment_check = env_check.to_dict()

        if env_check.readiness == Readiness.BLOCKED:
            job.exit_stage(RenderJobStatus.VALIDATING_ENVIRONMENT, stage_t0,
                           result="blocked")
            job.fail(
                f"Environment blocked: {'; '.join(env_check.blockers)}",
                stage=RenderJobStatus.ENVIRONMENT_BLOCKED,
            )
            artifact["error"] = f"Environment not ready: {'; '.join(env_check.blockers)}"
            artifact["warnings"].extend(env_check.warnings)
            job.finalize(job_t0)
            artifact["render_job"] = job.to_dict()
            _save_meta(meta_path, artifact)
            return artifact

        if env_check.warnings:
            artifact["warnings"].extend(env_check.warnings)
            job.warnings.extend(env_check.warnings)

        job.exit_stage(RenderJobStatus.VALIDATING_ENVIRONMENT, stage_t0,
                       result=env_check.readiness.value)

        # ── Stage 2: Prepare CS2 playback ─────────────────────────────────
        stage_t0 = job.enter_stage(RenderJobStatus.PREPARING_PLAYBACK)

        playback_job = build_playback_job(
            demo_path, clip_plan, camera_overrides=camera_overrides
        )
        playback_result = prepare_playback(
            playback_job,
            control_service=control_service,
            config=cs2_cfg,
            skip_launch=skip_cs2_launch,
        )
        pb_dict = playback_result.to_dict()
        artifact["playback_result"] = pb_dict
        job.playback_result = pb_dict

        # Record applied camera in the job
        artifact["job"]["camera_mode"] = pb_dict.get("applied_camera", {}).get("camera_mode")
        artifact["job"]["observer_mode"] = pb_dict.get("applied_camera", {}).get("observer_mode")
        artifact["job"]["hud_mode"] = pb_dict.get("applied_camera", {}).get("hud_mode")

        if playback_result.status not in (PlaybackStatus.READY, PlaybackStatus.PARTIALLY_READY):
            job.exit_stage(RenderJobStatus.PREPARING_PLAYBACK, stage_t0,
                           result=playback_result.status.value, detail=playback_result.error)
            job.fail(f"Playback preparation failed: {playback_result.error}")
            artifact["error"] = f"Playback preparation failed: {playback_result.error}"
            artifact["warnings"].append("playback_failed")
            artifact["warnings"].extend(playback_result.warnings)
            job.finalize(job_t0)
            artifact["render_job"] = job.to_dict()
            _save_meta(meta_path, artifact)
            return artifact

        if not playback_result.load_confirmed:
            job.exit_stage(RenderJobStatus.PREPARING_PLAYBACK, stage_t0,
                           result="blocked", detail="demo_load_not_confirmed")
            job.fail("Playback preparation blocked: demo load not confirmed")
            artifact["error"] = "Playback preparation blocked: demo load not confirmed"
            artifact["warnings"].append("playback_blocked_demo_load_unconfirmed")
            artifact["warnings"].extend(playback_result.warnings)
            job.finalize(job_t0)
            artifact["render_job"] = job.to_dict()
            _save_meta(meta_path, artifact)
            return artifact

        if not playback_result.command_ready:
            job.exit_stage(RenderJobStatus.PREPARING_PLAYBACK, stage_t0,
                           result="blocked", detail="command_channel_not_ready")
            job.fail("Playback preparation blocked: command channel is not ready")
            artifact["error"] = "Playback preparation blocked: command channel is not ready"
            artifact["warnings"].append("playback_blocked_command_not_ready")
            artifact["warnings"].extend(playback_result.warnings)
            job.finalize(job_t0)
            artifact["render_job"] = job.to_dict()
            _save_meta(meta_path, artifact)
            return artifact

        if int(playback_result.prepared_tick or 0) <= 0:
            job.exit_stage(RenderJobStatus.PREPARING_PLAYBACK, stage_t0,
                           result="blocked", detail="prepared_tick_invalid")
            job.fail("Playback preparation blocked: prepared tick is invalid")
            artifact["error"] = "Playback preparation blocked: prepared tick is invalid"
            artifact["warnings"].append("playback_blocked_prepared_tick_invalid")
            artifact["warnings"].extend(playback_result.warnings)
            job.finalize(job_t0)
            artifact["render_job"] = job.to_dict()
            _save_meta(meta_path, artifact)
            return artifact

        if not playback_result.netcon_connected:
            job.exit_stage(RenderJobStatus.PREPARING_PLAYBACK, stage_t0,
                           result="blocked", detail="netcon_not_connected")
            job.fail("Playback preparation blocked: netcon command channel is required for in-game capture")
            artifact["error"] = "Playback preparation blocked: netcon command channel is required"
            artifact["warnings"].append("playback_blocked_netcon_required")
            artifact["warnings"].extend(playback_result.warnings)
            job.finalize(job_t0)
            artifact["render_job"] = job.to_dict()
            _save_meta(meta_path, artifact)
            return artifact

        if playback_result.warnings:
            artifact["warnings"].extend(playback_result.warnings)

        job.exit_stage(RenderJobStatus.PREPARING_PLAYBACK, stage_t0,
                       result=playback_result.status.value)

        # ── Stage 3: Pre-recording CS2 alive check ─────────────────────────
        # CS2 can crash during the (potentially long) playback prep stage.
        # Verify it is still running before we connect to OBS and start
        # recording, so we don't burn OBS recording time on a dead process.
        quick_state = ctrl.check_status()
        if quick_state.cs2_status.value != "running":
            job.exit_stage(RenderJobStatus.PREPARING_PLAYBACK, stage_t0,
                           result="cs2_died_during_prep")
            job.fail(
                "CS2 process died between playback preparation and recording. "
                "Restart CS2 and retry."
            )
            artifact["error"] = "CS2 stopped before recording could begin"
            job.finalize(job_t0)
            artifact["render_job"] = job.to_dict()
            _save_meta(meta_path, artifact)
            return artifact

        # ── Stage 3: Execute real capture via dedicated execution layer ───
        stage_t0 = job.enter_stage(
            RenderJobStatus.STARTING_CAPTURE,
            detail=f"{clip_duration_s:.1f}s clip + pre/post roll (record-then-resume)",
        )

        capture_request = CaptureExecutionRequest(
            job_id=job.job_id,
            clip_id=clip_id,
            demo_id=demo_id,
            duration_s=clip_duration_s,
            workspace_root=str(clip_dir),
            render_mode=RENDER_MODE_INGAME_CAPTURE,
            pre_roll_s=DEFAULT_CAPTURE_PRE_ROLL_S,
            post_roll_s=DEFAULT_CAPTURE_POST_ROLL_S,
            resume_settle_s=1.0,
            minimum_output_bytes=1024,
            metadata={
                "clip_plan_id": clip_plan_id,
                "round_number": int(clip_plan.get("round_number") or 0),
                "start_tick": start_tick,
                "end_tick": end_tick,
                "prepared_tick": int(playback_result.prepared_tick or 0),
                "pov_player": clip_plan.get("pov_player"),
            },
        )
        execution_result = capture_executor.execute(capture_request)
        exec_dict = execution_result.to_dict()
        artifact["capture_execution"] = exec_dict

        cap_dict = execution_result.capture_result or {}
        artifact["capture_result"] = cap_dict
        job.capture_result = {
            "execution": exec_dict,
            "backend_capture": cap_dict,
        }

        job.exit_stage(
            RenderJobStatus.STARTING_CAPTURE,
            stage_t0,
            result=execution_result.status.value,
            detail=execution_result.failure_code,
        )

        # ── Stage 6: Process and validate captured file ───────────────────
        stage_t0 = job.enter_stage(RenderJobStatus.FINALIZING)

        if execution_result.status != CaptureExecutionStatus.COMPLETED:
            job.exit_stage(RenderJobStatus.FINALIZING, stage_t0,
                           result="capture_failed")
            job.fail(f"Capture failed: {execution_result.error}")
            artifact["error"] = f"Capture failed: {execution_result.error}"
            artifact["warnings"].append("capture_failed")
            artifact["warnings"].extend(execution_result.warnings)
            job.finalize(job_t0)
            artifact["render_job"] = job.to_dict()
            _save_meta(meta_path, artifact)
            return artifact

        captured_path = execution_result.raw_output_path
        if not captured_path or not Path(captured_path).is_file():
            job.exit_stage(RenderJobStatus.FINALIZING, stage_t0,
                           result="output_missing")
            job.fail("Capture completed but output file not found")
            artifact["error"] = "Capture completed but output file not found"
            artifact["warnings"].append("output_missing")
            job.finalize(job_t0)
            artifact["render_job"] = job.to_dict()
            _save_meta(meta_path, artifact)
            return artifact

        if execution_result.warnings:
            artifact["warnings"].extend(execution_result.warnings)

        # Move/copy the OBS output to our clip directory if needed
        promote_raw_capture_to_final(captured_path, video_path)

        if not video_path.is_file():
            job.exit_stage(RenderJobStatus.FINALIZING, stage_t0,
                           result="move_failed")
            job.fail(f"Output file not at expected path after move: {video_path}")
            artifact["error"] = f"Output file not at expected path: {video_path}"
            job.finalize(job_t0)
            artifact["render_job"] = job.to_dict()
            _save_meta(meta_path, artifact)
            return artifact

        # ── Output validation ─────────────────────────────────────────────
        expected_total_s = clip_duration_s + DEFAULT_CAPTURE_PRE_ROLL_S + DEFAULT_CAPTURE_POST_ROLL_S
        output_val = validate_output_file(video_path, expected_duration_s=expected_total_s)
        artifact["output_validation"] = output_val.to_dict()
        job.output_validation = output_val.to_dict()

        if not output_val.valid:
            job.exit_stage(RenderJobStatus.FINALIZING, stage_t0,
                           result="output_invalid")
            job.fail(f"Output validation failed: {output_val.error}")
            artifact["error"] = f"Output validation failed: {output_val.error}"
            artifact["warnings"].extend(output_val.warnings)
            job.finalize(job_t0)
            artifact["render_job"] = job.to_dict()
            _save_meta(meta_path, artifact)
            return artifact

        if output_val.warnings:
            artifact["warnings"].extend(output_val.warnings)

        # ── Build successful artifact ─────────────────────────────────────
        artifact.update({
            "status": "completed",
            "duration_s": output_val.duration_s or round(expected_total_s, 2),
            "frame_count": output_val.frame_count or 0,
            "output_path": str(video_path),
            "output_url": generated_url(video_path),
        })

        # Thumbnail: extract a frame at ~25% through the clip
        thumb_path = clip_dir / "thumbnail.jpg"
        try:
            cap_thumb = cv2.VideoCapture(str(video_path))
            total_frames = int(cap_thumb.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
            target_frame = max(0, total_frames // 4)
            cap_thumb.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
            ret, frame = cap_thumb.read()
            cap_thumb.release()
            if ret and frame is not None:
                cv2.imwrite(str(thumb_path), frame)
                artifact["thumbnail_path"] = str(thumb_path)
                artifact["thumbnail_url"] = generated_url(thumb_path)
                artifact["artifacts"]["thumbnail"]["path"] = str(thumb_path)
                artifact["artifacts"]["thumbnail"]["url"] = generated_url(thumb_path)
            else:
                artifact["warnings"].append("thumbnail_extraction_failed: no frame read")
        except Exception as _thumb_exc:
            artifact["warnings"].append(f"thumbnail_extraction_failed: {_thumb_exc}")

        job.exit_stage(RenderJobStatus.FINALIZING, stage_t0, result="ok")
        job.status = RenderJobStatus.COMPLETED
        job.finalize(job_t0)
        artifact["render_job"] = job.to_dict()

        _save_meta(meta_path, artifact)
        logger.info(
            "[ingame_capture] Clip %s completed in %.0fms",
            clip_id, job.total_duration_ms,
        )
        return artifact

    except Exception as exc:
        artifact["status"] = "failed"
        artifact["error"] = str(exc)
        artifact["warnings"].append("unexpected_error")
        job.fail(str(exc))
        job.finalize(job_t0)
        artifact["render_job"] = job.to_dict()
        logger.error("[ingame_capture] Failed: %s", exc, exc_info=True)
        _save_meta(meta_path, artifact)
        return artifact

    finally:
        try:
            ctrl.demo_pause()
        except Exception:
            pass
        try:
            ctrl.demo_timescale(1.0)
        except Exception:
            pass
        ctrl.close()


def _save_meta(meta_path: Path, artifact: dict) -> None:
    """Write artifact metadata to JSON file."""
    try:
        atomic_json_write(meta_path, artifact)
    except Exception as exc:
        logger.error("Failed to write metadata to %s: %s", meta_path, exc)
