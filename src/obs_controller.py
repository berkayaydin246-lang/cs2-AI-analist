"""
obs_controller.py
OBS Studio recording control via WebSocket v5 protocol.

Uses obsws-python to automate OBS for in-game clip capture:
  - Connect / disconnect from OBS WebSocket server
  - Configure recording output (path, filename format)
  - Start / stop recording
  - Poll recording status
  - Locate the resulting output file
  - Return structured capture results

Prerequisites:
  - OBS Studio 28+ with WebSocket Server enabled (Tools → obs-websocket Settings)
  - obs-websocket v5 protocol (built into OBS 28+)
  - obsws-python package installed

Configuration:
  - OBS_WS_HOST (default: localhost)
  - OBS_WS_PORT (default: 4455)
  - OBS_WS_PASSWORD (default: empty)
  - OBS_OUTPUT_DIR (default: outputs/generated/clips)
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ── Status types ──────────────────────────────────────────────────────────────

class OBSStatus(str, Enum):
    NOT_INSTALLED = "not_installed"
    NOT_CONNECTED = "not_connected"
    CONNECTED = "connected"
    CONNECT_FAILED = "connect_failed"
    RECORDING = "recording"
    IDLE = "idle"


class CaptureStatus(str, Enum):
    PENDING = "pending"
    RECORDING = "recording"
    COMPLETED = "completed"
    FAILED = "failed"
    NO_OUTPUT = "no_output"


@dataclass
class CaptureResult:
    """Structured result of a recording capture attempt."""
    capture_id: str = ""
    status: CaptureStatus = CaptureStatus.PENDING
    output_path: str | None = None
    output_size_bytes: int = 0
    duration_s: float = 0.0
    obs_connected: bool = False
    recording_started: bool = False
    recording_stopped: bool = False
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    steps: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "capture_id": self.capture_id,
            "status": self.status.value,
            "output_path": self.output_path,
            "output_size_bytes": self.output_size_bytes,
            "duration_s": self.duration_s,
            "obs_connected": self.obs_connected,
            "recording_started": self.recording_started,
            "recording_stopped": self.recording_stopped,
            "warnings": list(self.warnings),
            "error": self.error,
            "steps": list(self.steps),
        }


# ── OBS config ────────────────────────────────────────────────────────────────

_DEFAULT_OBS_HOST = "localhost"
_DEFAULT_OBS_PORT = 4455
_DEFAULT_OBS_PASSWORD = ""
_DEFAULT_CONNECT_TIMEOUT = 10
_DEFAULT_RECORDING_SETTLE = 1.5  # seconds after start/stop to let OBS settle


def build_obs_config(overrides: dict | None = None) -> dict:
    """Build OBS connection/capture config dict.

    Resolution: overrides → env vars (OBS_*) → defaults.
    """
    overrides = overrides or {}

    def _env(key: str, default: str | None = None) -> str | None:
        return os.environ.get(f"OBS_{key.upper()}", default)

    def _env_int(key: str, default: int) -> int:
        raw = _env(key)
        if raw is not None:
            try:
                return int(raw)
            except ValueError:
                pass
        return default

    return {
        "host": overrides.get("host") or _env("WS_HOST", _DEFAULT_OBS_HOST),
        "port": overrides.get("port") or _env_int("WS_PORT", _DEFAULT_OBS_PORT),
        "password": overrides.get("password") or _env("WS_PASSWORD", _DEFAULT_OBS_PASSWORD),
        "connect_timeout": overrides.get("connect_timeout") or _env_int("CONNECT_TIMEOUT", _DEFAULT_CONNECT_TIMEOUT),
        "output_dir": overrides.get("output_dir") or _env("OUTPUT_DIR"),
        "recording_settle": overrides.get("recording_settle") or float(_env("RECORDING_SETTLE", str(_DEFAULT_RECORDING_SETTLE))),
    }


def validate_obs_config(cfg: dict) -> list[dict]:
    """Validate OBS config. Returns diagnostic entries."""
    diag: list[dict] = []

    host = cfg.get("host", "")
    port = cfg.get("port", 0)
    if not host:
        diag.append({"level": "error", "field": "host", "message": "OBS WebSocket host is empty"})
    else:
        diag.append({"level": "ok", "field": "host", "message": f"OBS host: {host}"})

    if not isinstance(port, int) or port < 1 or port > 65535:
        diag.append({"level": "error", "field": "port", "message": f"Invalid OBS WebSocket port: {port}"})
    else:
        diag.append({"level": "ok", "field": "port", "message": f"OBS port: {port}"})

    output_dir = cfg.get("output_dir")
    if output_dir and not Path(output_dir).is_dir():
        diag.append({"level": "warning", "field": "output_dir", "message": f"OBS output dir does not exist: {output_dir}"})

    return diag


# ── Controller ────────────────────────────────────────────────────────────────

class OBSController:
    """Control OBS Studio recording via WebSocket v5.

    Usage:
        obs = OBSController()
        obs.connect()
        obs.set_output_path("C:/clips")
        obs.start_recording()
        time.sleep(duration)
        obs.stop_recording()
        output_file = obs.get_last_recording_path()
        obs.disconnect()
    """

    def __init__(self, config: dict | None = None):
        self._cfg = config or build_obs_config()
        self._client: Any = None  # obsws.ReqClient when connected
        self._connected = False
        self._last_output_path: str | None = None

    @property
    def config(self) -> dict:
        return dict(self._cfg)

    @property
    def connected(self) -> bool:
        return self._connected

    # ── Connection ────────────────────────────────────────────────────────

    def connect(self) -> OBSStatus:
        """Connect to OBS WebSocket server."""
        try:
            import obsws_python as obsws
        except ImportError:
            logger.error("obsws-python is not installed. Run: pip install obsws-python")
            return OBSStatus.NOT_INSTALLED

        host = self._cfg.get("host", _DEFAULT_OBS_HOST)
        port = self._cfg.get("port", _DEFAULT_OBS_PORT)
        password = self._cfg.get("password", _DEFAULT_OBS_PASSWORD)
        timeout = self._cfg.get("connect_timeout", _DEFAULT_CONNECT_TIMEOUT)

        try:
            self._client = obsws.ReqClient(
                host=host,
                port=port,
                password=password if password else None,
                timeout=timeout,
            )
            self._connected = True
            logger.info("Connected to OBS WebSocket at %s:%d", host, port)
            return OBSStatus.CONNECTED

        except Exception as exc:
            self._connected = False
            self._client = None
            logger.warning("OBS connection failed (%s:%d): %s", host, port, exc)
            return OBSStatus.CONNECT_FAILED

    def disconnect(self):
        """Disconnect from OBS WebSocket."""
        if self._client:
            try:
                self._client.base_client.ws.close()
            except Exception:
                pass
        self._client = None
        self._connected = False

    def connect_with_retry(
        self, max_attempts: int = 3, delay_s: float = 2.0
    ) -> OBSStatus:
        """Attempt to connect to OBS with retries.

        Retries on CONNECT_FAILED up to max_attempts times.
        Returns the final connection status.
        """
        last_status = OBSStatus.NOT_CONNECTED
        for attempt in range(1, max_attempts + 1):
            last_status = self.connect()
            if last_status == OBSStatus.CONNECTED:
                return last_status
            if last_status == OBSStatus.NOT_INSTALLED:
                return last_status  # no point retrying
            if attempt < max_attempts:
                logger.info(
                    "OBS connect attempt %d/%d failed, retrying in %.1fs...",
                    attempt, max_attempts, delay_s,
                )
                time.sleep(delay_s)
        return last_status

    def check_status(self) -> OBSStatus:
        """Check current OBS connection and recording state."""
        if not self._connected or not self._client:
            return OBSStatus.NOT_CONNECTED

        try:
            status = self._client.get_record_status()
            if status.output_active:
                return OBSStatus.RECORDING
            return OBSStatus.IDLE
        except Exception:
            self._connected = False
            return OBSStatus.NOT_CONNECTED

    def get_diagnostics(self) -> dict:
        """Return full diagnostic info."""
        status = self.check_status()
        config_diag = validate_obs_config(self._cfg)

        diag: dict[str, Any] = {
            "obs_status": status.value,
            "connected": self._connected,
            "config_validation": config_diag,
        }

        if self._connected and self._client:
            try:
                version = self._client.get_version()
                diag["obs_version"] = version.obs_version
                diag["obs_ws_version"] = version.obs_web_socket_version
                diag["platform"] = version.platform_description
            except Exception:
                diag["obs_version"] = "unknown"

            try:
                rec_dir = self._client.get_record_directory()
                diag["recording_directory"] = rec_dir.record_directory
            except Exception:
                diag["recording_directory"] = "unknown"

        return diag

    # ── Recording output configuration ────────────────────────────────────

    def set_output_directory(self, directory: str) -> dict:
        """Set OBS recording output directory.

        Returns {"success": bool, "directory": str, "error": str|None}
        """
        result: dict[str, Any] = {"success": False, "directory": directory, "error": None}
        if not self._connected or not self._client:
            result["error"] = "Not connected to OBS"
            return result

        try:
            abs_dir = str(Path(directory).resolve())
            Path(abs_dir).mkdir(parents=True, exist_ok=True)
            self._client.set_record_directory(abs_dir)
            result["success"] = True
            result["directory"] = abs_dir
            logger.info("OBS recording directory set to: %s", abs_dir)
        except Exception as exc:
            result["error"] = f"Failed to set recording directory: {exc}"
            logger.error("set_record_directory failed: %s", exc)
        return result

    def set_filename_format(self, format_string: str) -> dict:
        """Set OBS output filename format via profile settings.

        OBS uses a format string like: clip_%CCYY%MM%DD_%hh%mm%ss
        Returns {"success": bool, "format": str, "error": str|None}

        Note: This sets the FilenameFormatting property in OBS output settings.
        """
        result: dict[str, Any] = {"success": False, "format": format_string, "error": None}
        if not self._connected or not self._client:
            result["error"] = "Not connected to OBS"
            return result

        try:
            self._client.set_profile_parameter("Output", "FilenameFormatting", format_string)
            result["success"] = True
        except Exception as exc:
            result["error"] = f"Failed to set filename format: {exc}"
            logger.warning("set_filename_format failed: %s (non-critical)", exc)
        return result

    # ── Recording control ─────────────────────────────────────────────────

    def start_recording(self) -> dict:
        """Start OBS recording.

        Returns {"success": bool, "error": str|None}
        """
        result: dict[str, Any] = {"success": False, "error": None}
        if not self._connected or not self._client:
            result["error"] = "Not connected to OBS"
            return result

        try:
            # Check if already recording
            status = self._client.get_record_status()
            if status.output_active:
                result["success"] = True
                result["warning"] = "Already recording"
                return result

            self._client.start_record()
            settle = self._cfg.get("recording_settle", _DEFAULT_RECORDING_SETTLE)
            time.sleep(settle)
            result["success"] = True
            logger.info("OBS recording started")
        except Exception as exc:
            result["error"] = f"Failed to start recording: {exc}"
            logger.error("start_recording failed: %s", exc)
        return result

    def stop_recording(self) -> dict:
        """Stop OBS recording and return the output file path.

        Returns {"success": bool, "output_path": str|None, "error": str|None}
        """
        result: dict[str, Any] = {"success": False, "output_path": None, "error": None}
        if not self._connected or not self._client:
            result["error"] = "Not connected to OBS"
            return result

        try:
            # Check if OBS is actually recording before trying to stop
            try:
                status = self._client.get_record_status()
                if not status.output_active:
                    logger.warning("OBS is not recording — nothing to stop")
                    result["success"] = True
                    result["warning"] = "recording_already_stopped"
                    # Try to find the last output file
                    found = self.get_last_recording_path()
                    if found:
                        result["output_path"] = found
                    return result
            except Exception:
                pass  # If status check fails, try to stop anyway

            resp = self._client.stop_record()
            output_path = getattr(resp, "output_path", None)
            settle = self._cfg.get("recording_settle", _DEFAULT_RECORDING_SETTLE)
            time.sleep(settle)

            if output_path:
                self._last_output_path = str(output_path)
                result["output_path"] = str(output_path)
            else:
                # Try to find it from recording directory
                result["warning"] = "OBS did not return output_path; try get_last_recording_path()"

            result["success"] = True
            logger.info("OBS recording stopped. Output: %s", output_path)
        except Exception as exc:
            # OBS may return 501 if recording already stopped — treat as success
            exc_str = str(exc).lower()
            if "501" in exc_str or "not recording" in exc_str:
                logger.warning("OBS stop_record returned 501 (not recording) — treating as already stopped")
                result["success"] = True
                result["warning"] = "recording_already_stopped_501"
                found = self.get_last_recording_path()
                if found:
                    result["output_path"] = found
            else:
                result["error"] = f"Failed to stop recording: {exc}"
                logger.error("stop_recording failed: %s", exc)
        return result

    def get_recording_status(self) -> dict:
        """Get current recording status.

        Returns {"active": bool, "duration_s": float, "bytes": int, "error": str|None}
        """
        result: dict[str, Any] = {"active": False, "duration_s": 0.0, "bytes": 0, "error": None}
        if not self._connected or not self._client:
            result["error"] = "Not connected to OBS"
            return result

        try:
            status = self._client.get_record_status()
            result["active"] = bool(status.output_active)
            # output_duration comes as a string like "HH:MM:SS.mmm"
            raw_duration = getattr(status, "output_duration", None)
            if isinstance(raw_duration, (int, float)):
                result["duration_s"] = float(raw_duration)
            elif isinstance(raw_duration, str) and ":" in raw_duration:
                parts = raw_duration.split(":")
                try:
                    h, m, s = float(parts[0]), float(parts[1]), float(parts[2])
                    result["duration_s"] = h * 3600 + m * 60 + s
                except (ValueError, IndexError):
                    pass
            result["bytes"] = int(getattr(status, "output_bytes", 0))
        except Exception as exc:
            result["error"] = f"Failed to get recording status: {exc}"
        return result

    def get_last_recording_path(self) -> str | None:
        """Return the path of the last recording file if known."""
        if self._last_output_path and Path(self._last_output_path).is_file():
            return self._last_output_path

        # Fallback: check the recording directory for the most recent file
        rec_dir = None
        if self._connected and self._client:
            try:
                resp = self._client.get_record_directory()
                rec_dir = resp.record_directory
            except Exception:
                pass

        if not rec_dir:
            rec_dir = self._cfg.get("output_dir")

        if rec_dir and Path(rec_dir).is_dir():
            # Prefer MP4 (common OBS output), then MKV, then FLV
            for ext in ("*.mp4", "*.mkv", "*.flv"):
                video_files = sorted(
                    Path(rec_dir).glob(ext),
                    key=lambda f: f.stat().st_mtime,
                    reverse=True,
                )
                if video_files:
                    self._last_output_path = str(video_files[0])
                    return self._last_output_path

        return None


# ── High-level capture flow ───────────────────────────────────────────────────

def record_clip_window(
    duration_s: float,
    output_dir: str,
    clip_id: str,
    *,
    obs_config: dict | None = None,
    pre_roll_s: float = 0.5,
    post_roll_s: float = 0.5,
    on_recording_started: callable | None = None,
) -> CaptureResult:
    """Execute a timed recording capture.

    Steps:
      1. Connect to OBS
      2. Set output directory
      3. Start recording
      4. (optional) call on_recording_started callback (e.g. resume demo)
      5. Wait for duration + pre/post roll
      6. Stop recording
      7. Locate and validate output file

    Args:
        duration_s: Expected clip duration in seconds
        output_dir: Directory to save the recording
        clip_id: Identifier for this capture (used in logging)
        obs_config: Optional OBS config overrides
        pre_roll_s: Extra seconds to record before nominal start
        post_roll_s: Extra seconds to record after nominal end
        on_recording_started: Optional callback invoked after OBS starts recording
                              but before the clip timer begins (e.g. to resume demo)
    """
    result = CaptureResult(capture_id=clip_id)
    obs = OBSController(obs_config)

    def _find_recent_clip_output(
        search_dir: str,
        clip_prefix: str,
        started_at: float,
    ) -> str | None:
        root = Path(search_dir)
        if not root.is_dir():
            return None
        candidates: list[Path] = []
        for ext in ("*.mp4", "*.mkv", "*.flv"):
            candidates.extend(root.glob(ext))
        if not candidates:
            return None

        # Prefer files with the clip prefix and created after this capture started.
        preferred = [
            p for p in candidates
            if p.name.startswith(f"{clip_prefix}_") and p.stat().st_mtime >= (started_at - 2.0)
        ]
        if preferred:
            preferred.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            return str(preferred[0])

        recent = [p for p in candidates if p.stat().st_mtime >= (started_at - 2.0)]
        if recent:
            recent.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            return str(recent[0])

        return None

    try:
        # ── Step 1: Connect (with retry) ──────────────────────────────────
        conn_status = obs.connect_with_retry(max_attempts=3, delay_s=2.0)
        result.obs_connected = conn_status == OBSStatus.CONNECTED
        result.steps.append({"step": "connect", "status": conn_status.value})

        if not result.obs_connected:
            result.status = CaptureStatus.FAILED
            result.error = f"Cannot connect to OBS after retries: {conn_status.value}"
            return result

        # ── Step 2: Ensure not already recording ─────────────────────────
        pre_status = obs.check_status()
        if pre_status == OBSStatus.RECORDING:
            # Stop any existing recording before starting ours
            logger.warning("OBS already recording — stopping existing recording first")
            obs.stop_recording()
            time.sleep(1.0)
            result.warnings.append("stopped_existing_recording")

        # ── Step 3: Set output directory (HARD FAILURE if this fails) ─────
        dir_result = obs.set_output_directory(output_dir)
        result.steps.append({"step": "set_output_dir", **dir_result})
        if not dir_result["success"]:
            result.status = CaptureStatus.FAILED
            result.error = (
                f"OBS output directory could not be set: {dir_result.get('error')}. "
                "Recording would save to wrong location. Check OBS settings."
            )
            return result

        # Set a filename format with the clip_id prefix
        fmt_result = obs.set_filename_format(f"{clip_id}_%hh%mm%ss")
        result.steps.append({"step": "set_filename_format", **fmt_result})
        if not fmt_result["success"]:
            result.warnings.append("Could not set filename format (non-critical)")

        # ── Step 4: Start recording ───────────────────────────────────────
        result.status = CaptureStatus.RECORDING
        capture_started_at = time.time()
        start_result = obs.start_recording()
        result.recording_started = start_result.get("success", False)
        result.steps.append({"step": "start_recording", **start_result})

        if not result.recording_started:
            result.status = CaptureStatus.FAILED
            result.error = f"Failed to start recording: {start_result.get('error')}"
            return result

        # ── Step 4b: Invoke callback (e.g. resume demo) ──────────────────
        if on_recording_started:
            try:
                on_recording_started()
                result.steps.append({"step": "on_recording_started_callback", "success": True})
            except Exception as cb_exc:
                # Callback failed — abort recording immediately so we don't
                # capture a frozen frame for the full clip duration.
                logger.error("on_recording_started callback raised — aborting OBS recording: %s", cb_exc)
                result.steps.append({"step": "on_recording_started_callback",
                                     "success": False, "error": str(cb_exc)})
                abort_result = obs.stop_recording()
                result.steps.append({"step": "abort_recording_after_callback_failure", **abort_result})
                result.status = CaptureStatus.FAILED
                result.error = (
                    f"Capture aborted: demo resume callback failed — {cb_exc}. "
                    "Check that CS2 is running and the demo is loaded."
                )
                result.warnings.append("recording_aborted_due_to_callback_failure")
                return result

        # ── Step 5: Wait with periodic health polling ─────────────────────
        total_wait = pre_roll_s + duration_s + post_roll_s
        logger.info("Recording %s for %.1fs (%.1f + %.1f + %.1f)",
                     clip_id, total_wait, pre_roll_s, duration_s, post_roll_s)

        poll_interval = min(2.0, total_wait)
        elapsed = 0.0
        recording_healthy = True
        while elapsed < total_wait:
            chunk = min(poll_interval, total_wait - elapsed)
            time.sleep(chunk)
            elapsed += chunk
            # Poll OBS to verify it's still recording
            try:
                rec_status = obs.get_recording_status()
                if not rec_status.get("active", False) and elapsed < total_wait - 0.5:
                    logger.warning("OBS stopped recording unexpectedly at %.1fs/%.1fs",
                                   elapsed, total_wait)
                    recording_healthy = False
                    result.warnings.append(
                        f"recording_stopped_early_at_{elapsed:.1f}s"
                    )
                    break
            except Exception:
                # Polling failure is non-fatal — continue waiting
                pass

        result.duration_s = round(elapsed, 2)
        result.steps.append({
            "step": "recording_wait", "total_s": total_wait,
            "actual_elapsed_s": round(elapsed, 2),
            "pre_roll_s": pre_roll_s, "duration_s": duration_s,
            "post_roll_s": post_roll_s, "healthy": recording_healthy,
        })

        # ── Step 6: Handle early stop or stop recording ───────────────────
        if not recording_healthy:
            # Recording stopped early — this is the PRIMARY failure.
            # Do NOT try StopRecord (it would give 501 on already-stopped).
            result.status = CaptureStatus.FAILED
            result.error = (
                f"OBS recording stopped unexpectedly at {elapsed:.1f}s "
                f"(expected {total_wait:.1f}s). This typically means the "
                "output directory is invalid or OBS encountered a write error. "
                "Check OBS recording path settings."
            )
            result.recording_stopped = True  # it already stopped on its own
            result.steps.append({
                "step": "recording_early_stop",
                "elapsed_s": round(elapsed, 2),
                "expected_s": round(total_wait, 2),
            })
            # Still try to find whatever partial output was written
            found = obs.get_last_recording_path()
            if found and Path(found).is_file():
                result.output_path = found
                result.output_size_bytes = Path(found).stat().st_size
            return result

        stop_result = obs.stop_recording()
        result.recording_stopped = stop_result.get("success", False)
        result.steps.append({"step": "stop_recording", **stop_result})

        if not result.recording_stopped:
            result.status = CaptureStatus.FAILED
            result.error = f"Failed to stop recording: {stop_result.get('error')}"
            return result

        # ── Step 7: Locate output file (with retry) ──────────────────────
        output_path = stop_result.get("output_path")

        # Retry file search — OBS may still be finalizing or remuxing.
        # MKV→MP4 remux (OBS auto-remux option) can take several extra seconds.
        max_file_retries = 8
        file_retry_delay = 1.5
        for attempt in range(max_file_retries):
            if output_path and Path(output_path).is_file():
                break

            preferred = _find_recent_clip_output(output_dir, clip_id, capture_started_at)
            if preferred and Path(preferred).is_file():
                output_path = preferred
                break

            # Fall back to directory scan (mp4, mkv, flv)
            found = obs.get_last_recording_path()
            if found and Path(found).is_file():
                output_path = found
                break
            logger.debug(
                "[OBS] Output file not yet visible (attempt %d/%d), waiting %.1fs...",
                attempt + 1, max_file_retries, file_retry_delay,
            )
            if attempt < max_file_retries - 1:
                time.sleep(file_retry_delay)

        if output_path and Path(output_path).is_file():
            file_size = Path(output_path).stat().st_size
            result.output_path = output_path
            result.output_size_bytes = file_size
            result.status = CaptureStatus.COMPLETED
            result.steps.append({"step": "output_found", "path": output_path,
                                 "size_bytes": file_size})
            logger.info("Capture complete: %s (%d bytes)", output_path, file_size)
            if file_size < 10_240:
                result.warnings.append(
                    f"output_suspiciously_small: {file_size} bytes — "
                    "recording may have been empty or interrupted"
                )
        else:
            result.status = CaptureStatus.NO_OUTPUT
            result.error = (
                f"Recording stopped but output file was not found after "
                f"{max_file_retries} retries ({max_file_retries * file_retry_delay:.0f}s). "
                "Check OBS recording output directory and container format settings."
            )
            result.warnings.append(
                "OBS may have saved the file to its default directory instead of the "
                f"configured path ({output_dir}). Check OBS output settings."
            )
            result.steps.append({"step": "output_missing", "expected_dir": output_dir,
                                 "obs_returned_path": stop_result.get("output_path"),
                                 "search_attempts": max_file_retries,
                                 "hint": "Ensure OBS output is set to MP4/MKV and not FLV or remote stream"})

        return result

    except Exception as exc:
        result.status = CaptureStatus.FAILED
        result.error = str(exc)
        logger.error("Capture failed for %s: %s", clip_id, exc, exc_info=True)
        return result

    finally:
        obs.disconnect()
