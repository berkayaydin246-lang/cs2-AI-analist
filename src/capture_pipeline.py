"""
capture_pipeline.py
Environment validation, job status model, and output validation for
the local in-game clip capture pipeline.

Provides:
  - RenderJobStatus: granular stage enum for render job observability
  - RenderJob: structured state tracker with per-stage timing
  - EnvironmentCheck: pre-flight readiness assessment
  - validate_capture_environment(): full environment validation
  - validate_output_file(): post-capture artifact validation
"""

from __future__ import annotations

import logging
import os
import platform
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ── Job status model ──────────────────────────────────────────────────────────

class RenderJobStatus(str, Enum):
    """Granular status states for an in-game render job.

    These make the render flow observable and debuggable.
    """
    QUEUED = "queued"
    VALIDATING_ENVIRONMENT = "validating_environment"
    ENVIRONMENT_BLOCKED = "environment_blocked"
    LAUNCHING_CS2 = "launching_cs2"
    LOADING_DEMO = "loading_demo"
    PREPARING_PLAYBACK = "preparing_playback"
    SEEKING_TARGET = "seeking_target"
    CONFIGURING_CAMERA = "configuring_camera"
    STARTING_CAPTURE = "starting_capture"
    RECORDING = "recording"
    STOPPING_CAPTURE = "stopping_capture"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# ── Render job tracker ────────────────────────────────────────────────────────

@dataclass
class RenderJob:
    """Tracks the full state of an in-game render job.

    Records status transitions, per-stage timing, and structured diagnostics
    so that any failure can be diagnosed quickly.
    """
    job_id: str = ""
    clip_id: str = ""
    demo_id: str = ""
    status: RenderJobStatus = RenderJobStatus.QUEUED
    started_at: str = ""
    completed_at: str = ""
    total_duration_ms: float = 0.0

    # Per-stage timing (stage_name → duration_ms)
    stage_timings: dict[str, float] = field(default_factory=dict)

    # Ordered log of stage transitions
    stages: list[dict] = field(default_factory=list)

    # Accumulated warnings and the final error (if any)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None

    # Sub-results
    environment_check: dict | None = None
    playback_result: dict | None = None
    capture_result: dict | None = None
    output_validation: dict | None = None

    def _t0(self) -> float:
        """Return monotonic timestamp for stage timing."""
        return time.monotonic()

    def enter_stage(self, status: RenderJobStatus, detail: str | None = None) -> float:
        """Record a stage transition and return a monotonic start time."""
        self.status = status
        entry: dict[str, Any] = {
            "stage": status.value,
            "entered_at": datetime.now(timezone.utc).isoformat(),
        }
        if detail:
            entry["detail"] = detail
        self.stages.append(entry)
        logger.info("[render_job %s] → %s%s", self.job_id, status.value,
                     f" ({detail})" if detail else "")
        return time.monotonic()

    def exit_stage(self, status: RenderJobStatus, t0: float,
                   result: str = "ok", detail: str | None = None) -> None:
        """Record stage completion with timing."""
        elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
        self.stage_timings[status.value] = elapsed_ms
        # Update the last stage entry
        if self.stages and self.stages[-1]["stage"] == status.value:
            self.stages[-1]["duration_ms"] = elapsed_ms
            self.stages[-1]["result"] = result
            if detail:
                self.stages[-1]["detail"] = detail

    def fail(self, error: str, stage: RenderJobStatus | None = None) -> None:
        """Mark the job as failed."""
        self.status = RenderJobStatus.FAILED
        self.error = error
        if stage:
            self.stages.append({
                "stage": stage.value,
                "result": "failed",
                "error": error,
                "at": datetime.now(timezone.utc).isoformat(),
            })
        logger.error("[render_job %s] FAILED: %s", self.job_id, error)

    def finalize(self, job_t0: float) -> None:
        """Fill in completion times."""
        self.completed_at = datetime.now(timezone.utc).isoformat()
        self.total_duration_ms = round((time.monotonic() - job_t0) * 1000, 1)

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "clip_id": self.clip_id,
            "demo_id": self.demo_id,
            "status": self.status.value,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "total_duration_ms": self.total_duration_ms,
            "stage_timings": dict(self.stage_timings),
            "stages": list(self.stages),
            "warnings": list(self.warnings),
            "error": self.error,
            "environment_check": self.environment_check,
            "playback_result": self.playback_result,
            "capture_result": self.capture_result,
            "output_validation": self.output_validation,
        }


# ── Environment validation ────────────────────────────────────────────────────

class Readiness(str, Enum):
    READY = "ready"
    PARTIALLY_READY = "partially_ready"
    BLOCKED = "blocked"


@dataclass
class EnvironmentCheck:
    """Structured assessment of local capture readiness."""
    readiness: Readiness = Readiness.BLOCKED
    checks: list[dict] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    # Individual verdicts
    platform_ok: bool = False
    cs2_exe_found: bool = False
    cs2_running: bool = False
    obs_reachable: bool = False
    demo_exists: bool = False
    output_writable: bool = False

    @property
    def ready_for_capture(self) -> bool:
        """True when there are no blockers and the core capture requirements
        (platform, CS2 exe, OBS reachable, output writable) are all met."""
        if self.blockers:
            return False
        return (
            self.platform_ok
            and self.cs2_exe_found
            and self.obs_reachable
            and self.output_writable
        )

    def to_dict(self) -> dict:
        return {
            "readiness": self.readiness.value,
            "ready_for_capture": self.ready_for_capture,
            "checks": list(self.checks),
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
            "platform_ok": self.platform_ok,
            "cs2_exe_found": self.cs2_exe_found,
            "cs2_running": self.cs2_running,
            "obs_reachable": self.obs_reachable,
            "demo_exists": self.demo_exists,
            "output_writable": self.output_writable,
        }


def validate_capture_environment(
    *,
    demo_path: str | None = None,
    output_dir: str | None = None,
    cs2_config: dict | None = None,
    obs_config: dict | None = None,
    check_obs_connection: bool = True,
    check_cs2_process: bool = True,
) -> EnvironmentCheck:
    """Run a comprehensive pre-flight check for in-game capture.

    Returns a structured EnvironmentCheck with readiness level,
    individual check results, blockers, and warnings.
    """
    result = EnvironmentCheck()

    # ── 1. Platform check ─────────────────────────────────────────────────
    is_windows = sys.platform == "win32"
    result.platform_ok = is_windows
    if is_windows:
        result.checks.append({
            "check": "platform",
            "status": "ok",
            "detail": f"Windows ({platform.version()})",
        })
    else:
        result.checks.append({
            "check": "platform",
            "status": "error",
            "detail": f"Unsupported platform: {sys.platform}. In-game capture requires Windows.",
        })
        result.blockers.append("In-game capture requires Windows")

    # ── 2. CS2 executable ─────────────────────────────────────────────────
    from src.cs2_config import build_cs2_config

    cs2_cfg = cs2_config or build_cs2_config()
    cs2_exe = cs2_cfg.get("cs2_exe")

    if cs2_exe and Path(cs2_exe).is_file():
        result.cs2_exe_found = True
        result.checks.append({
            "check": "cs2_exe",
            "status": "ok",
            "detail": cs2_exe,
        })
    elif cs2_exe:
        result.checks.append({
            "check": "cs2_exe",
            "status": "error",
            "detail": f"CS2 executable not found at: {cs2_exe}",
        })
        result.blockers.append(f"CS2 executable not found: {cs2_exe}")
    else:
        result.checks.append({
            "check": "cs2_exe",
            "status": "error",
            "detail": "CS2 executable path not configured. Set CS2_EXE env var.",
        })
        result.blockers.append("CS2 executable path not configured")

    # ── 3. CS2 process running ────────────────────────────────────────────
    if check_cs2_process and is_windows:
        from src.cs2_controller import CS2Controller
        from src.cs2_controller import CS2Status, NetconStatus

        ctrl = CS2Controller(cs2_cfg)
        try:
            state = ctrl.check_status()

            if state.cs2_status == CS2Status.RUNNING:
                result.cs2_running = True
                result.checks.append({
                    "check": "cs2_process",
                    "status": "ok",
                    "detail": f"CS2 running (PID {state.pid})",
                })
            else:
                result.checks.append({
                    "check": "cs2_process",
                    "status": "info",
                    "detail": "CS2 not currently running (will be launched on demand)",
                })
                result.warnings.append("CS2 not running — will attempt launch during render")

            if state.cs2_status == CS2Status.RUNNING:
                netcon_status = ctrl.connect_netcon()
                if netcon_status == NetconStatus.CONNECTED:
                    result.checks.append({
                        "check": "netcon_connection",
                        "status": "ok",
                        "detail": f"Connected to CS2 netcon on port {cs2_cfg.get('netcon_port', 2121)}",
                    })
                else:
                    result.checks.append({
                        "check": "netcon_connection",
                        "status": "error",
                        "detail": (
                            "CS2 is running, but netcon command channel is unavailable."
                        ),
                    })
                    result.blockers.append(
                        "CS2 netcon command channel unavailable"
                    )
        finally:
            ctrl.close()
    elif not is_windows:
        result.checks.append({
            "check": "cs2_process",
            "status": "skipped",
            "detail": "Skipped: not Windows",
        })

    # ── 4. OBS reachable ──────────────────────────────────────────────────
    if check_obs_connection:
        from src.obs_controller import OBSController, OBSStatus, build_obs_config

        obs_cfg = obs_config or build_obs_config()
        obs = OBSController(obs_cfg)
        try:
            conn_status = obs.connect()
            if conn_status == OBSStatus.CONNECTED:
                result.obs_reachable = True
                diag = obs.get_diagnostics()
                result.checks.append({
                    "check": "obs_connection",
                    "status": "ok",
                    "detail": f"OBS connected (v{diag.get('obs_version', '?')})",
                    "obs_version": diag.get("obs_version"),
                    "recording_directory": diag.get("recording_directory"),
                })
            elif conn_status == OBSStatus.NOT_INSTALLED:
                result.checks.append({
                    "check": "obs_connection",
                    "status": "error",
                    "detail": "obsws-python package not installed",
                })
                result.blockers.append("obsws-python not installed: pip install obsws-python")
            else:
                result.checks.append({
                    "check": "obs_connection",
                    "status": "error",
                    "detail": f"Cannot connect to OBS WebSocket ({obs_cfg.get('host')}:{obs_cfg.get('port')})",
                })
                result.blockers.append(
                    "OBS not reachable. Ensure OBS Studio 28+ is running "
                    "with WebSocket Server enabled (Tools → obs-websocket Settings)"
                )
        except Exception as exc:
            result.checks.append({
                "check": "obs_connection",
                "status": "error",
                "detail": f"OBS check failed: {exc}",
            })
            result.blockers.append(f"OBS connection check error: {exc}")
        finally:
            obs.disconnect()

    # ── 5. Demo file ──────────────────────────────────────────────────────
    if demo_path:
        if Path(demo_path).is_file():
            size_mb = Path(demo_path).stat().st_size / (1024 * 1024)
            result.demo_exists = True
            result.checks.append({
                "check": "demo_file",
                "status": "ok",
                "detail": f"{demo_path} ({size_mb:.1f} MB)",
            })
        else:
            result.checks.append({
                "check": "demo_file",
                "status": "error",
                "detail": f"Demo file not found: {demo_path}",
            })
            result.blockers.append(f"Demo file not found: {demo_path}")
    else:
        result.checks.append({
            "check": "demo_file",
            "status": "skipped",
            "detail": "No demo path provided for check",
        })

    # ── 6. Output directory ───────────────────────────────────────────────
    if output_dir:
        out_path = Path(output_dir)
        try:
            out_path.mkdir(parents=True, exist_ok=True)
            # Test write permission
            test_file = out_path / ".write_test"
            test_file.write_text("ok", encoding="utf-8")
            test_file.unlink()
            result.output_writable = True
            result.checks.append({
                "check": "output_dir",
                "status": "ok",
                "detail": str(out_path),
            })
        except Exception as exc:
            result.checks.append({
                "check": "output_dir",
                "status": "error",
                "detail": f"Output directory not writable: {output_dir} ({exc})",
            })
            result.blockers.append(f"Output directory not writable: {output_dir}")
    else:
        result.checks.append({
            "check": "output_dir",
            "status": "skipped",
            "detail": "No output directory provided for check",
        })

    # ── 7. Required config values ─────────────────────────────────────────
    netcon_port = cs2_cfg.get("netcon_port", 0)
    if isinstance(netcon_port, int) and 1 <= netcon_port <= 65535:
        result.checks.append({
            "check": "netcon_port",
            "status": "ok",
            "detail": f"Port {netcon_port}",
        })
    else:
        result.checks.append({
            "check": "netcon_port",
            "status": "warning",
            "detail": f"Invalid netcon port: {netcon_port}",
        })
        result.warnings.append(f"Invalid netcon port configuration: {netcon_port}")

    # ── Compute overall readiness ─────────────────────────────────────────
    if result.blockers:
        result.readiness = Readiness.BLOCKED
    elif result.warnings:
        result.readiness = Readiness.PARTIALLY_READY
    else:
        result.readiness = Readiness.READY

    return result


# ── Output file validation ────────────────────────────────────────────────────

# Minimum acceptable file size for a captured video (bytes)
MIN_OUTPUT_SIZE_BYTES = 10_240  # 10 KB — anything less is surely broken

# A clip shorter than this is treated as a capture failure, not just a warning
MIN_OUTPUT_DURATION_HARD_S = 1.0

# A clip shorter than this triggers a warning but is still considered valid
MIN_OUTPUT_DURATION_S = 0.5

# Minimum viable decoded frame count for a clip artifact.
MIN_OUTPUT_FRAME_COUNT = 10

# If expected duration is known, require at least this ratio.
MIN_EXPECTED_DURATION_RATIO = 0.45


@dataclass
class OutputValidation:
    """Structured result of post-capture output validation."""
    valid: bool = False
    file_exists: bool = False
    file_size_bytes: int = 0
    file_size_ok: bool = False
    duration_s: float | None = None
    duration_ok: bool | None = None
    frame_count: int | None = None
    codec: str | None = None
    resolution: str | None = None
    warnings: list[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "valid": self.valid,
            "file_exists": self.file_exists,
            "file_size_bytes": self.file_size_bytes,
            "file_size_ok": self.file_size_ok,
            "duration_s": self.duration_s,
            "duration_ok": self.duration_ok,
            "frame_count": self.frame_count,
            "codec": self.codec,
            "resolution": self.resolution,
            "warnings": list(self.warnings),
            "error": self.error,
        }


def validate_output_file(
    file_path: str | Path,
    expected_duration_s: float | None = None,
) -> OutputValidation:
    """Validate a captured video file after recording.

    Checks:
      1. File exists
      2. File size above minimum threshold
      3. Video can be opened by OpenCV
      4. Frame count and duration are reasonable
      5. Duration roughly matches expected (if provided)
    """
    result = OutputValidation()
    path = Path(file_path)

    # 1. Existence
    if not path.is_file():
        result.error = f"Output file does not exist: {path}"
        return result
    result.file_exists = True

    # 2. File size
    result.file_size_bytes = path.stat().st_size
    if result.file_size_bytes < MIN_OUTPUT_SIZE_BYTES:
        result.error = (
            f"Output file too small: {result.file_size_bytes} bytes "
            f"(minimum: {MIN_OUTPUT_SIZE_BYTES})"
        )
        return result
    result.file_size_ok = True

    # 3. Video probe via OpenCV
    try:
        import cv2

        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            result.error = "Output file cannot be opened as video"
            result.warnings.append("cv2_open_failed")
            return result

        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fourcc_int = int(cap.get(cv2.CAP_PROP_FOURCC))
        cap.release()

        result.frame_count = frame_count
        result.resolution = f"{width}x{height}"

        # Decode fourcc
        try:
            result.codec = "".join(chr((fourcc_int >> (8 * i)) & 0xFF) for i in range(4))
        except Exception:
            result.codec = "unknown"

        # Duration
        if fps > 0 and frame_count > 0:
            result.duration_s = round(frame_count / fps, 2)
            if result.duration_s < MIN_OUTPUT_DURATION_HARD_S:
                result.error = (
                    f"Output too short: {result.duration_s:.2f}s "
                    f"(minimum acceptable: {MIN_OUTPUT_DURATION_HARD_S}s). "
                    "Recording may have been aborted immediately after starting."
                )
                return result
            result.duration_ok = result.duration_s >= MIN_OUTPUT_DURATION_S
        elif frame_count == 0:
            result.error = "Output contains zero decoded frames"
            result.duration_ok = False
            return result
        else:
            result.error = "Output has invalid FPS metadata"
            result.duration_ok = False
            return result

        if frame_count < MIN_OUTPUT_FRAME_COUNT:
            result.error = (
                f"Output has too few frames: {frame_count} "
                f"(minimum: {MIN_OUTPUT_FRAME_COUNT})"
            )
            return result

        # Duration match check
        if expected_duration_s and result.duration_s:
            ratio = result.duration_s / expected_duration_s
            if ratio < MIN_EXPECTED_DURATION_RATIO:
                result.error = (
                    f"duration_too_short: {result.duration_s:.1f}s vs expected {expected_duration_s:.1f}s"
                )
                return result
            elif ratio > 3.0:
                result.warnings.append(
                    f"duration_too_long: {result.duration_s:.1f}s vs expected {expected_duration_s:.1f}s"
                )

    except ImportError:
        result.warnings.append("cv2_not_available — video probe skipped")
    except Exception as exc:
        result.warnings.append(f"cv2_probe_error: {exc}")

    # Final verdict
    if result.file_size_ok and not result.error:
        result.valid = bool(result.duration_ok)
    
    return result
