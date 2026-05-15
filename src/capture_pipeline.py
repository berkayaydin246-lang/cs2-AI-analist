"""
capture_pipeline.py
Capture pipeline orchestrator.

Executes the strict lifecycle for in-game clip capture:
1. Prepare workspace
2. Prepare playback
3. Verify playback readiness
4. Apply POV and verify it
5. Start capture
6. Resume playback
7. Monitor capture health
8. Stop capture
9. Validate raw output
10. Post-process
11. Register clip only if valid

Wrong POV, camera drift, or invalid output => hard failure.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from src.cs2_config import CS2Config, load_config
from src.cs2_controller import CS2Controller, CS2ControlError
from src.cs2_playback import PlaybackController, PlaybackRequest, PlaybackResult, PlaybackError
from src.obs_controller import OBSController, OBSError
from src.pov_controller import POVController, POVError, POVState

log = logging.getLogger(__name__)

TICK_RATE = 64


class CaptureError(Exception):
    """Raised when capture fails irrecoverably."""


@dataclass
class CaptureRequest:
    """Full request for a single clip capture."""
    clip_id: str
    demo_path: str
    round_number: int
    start_tick: int
    anchor_tick: int
    end_tick: int
    round_start_tick: int = 0
    round_end_tick: int = 0
    freeze_end_tick: int = 0
    player_name: str = ""
    player_steamid64: str = ""
    pov_mode: str = "player_pov"     # player_pov, freecam
    camera_mode: str = "first_person"
    output_dir: str = ""

    @property
    def clip_duration_s(self) -> float:
        return max(0, (self.end_tick - self.start_tick)) / TICK_RATE


@dataclass
class CaptureResult:
    """Full result of a capture attempt."""
    clip_id: str = ""
    status: str = "pending"  # pending, recording, completed, failed

    # Stage tracking
    current_stage: str = ""
    stages: list[dict] = field(default_factory=list)
    stage_timings: dict[str, float] = field(default_factory=dict)

    # Sub-results
    playback_result: dict | None = None
    pov_state: dict | None = None
    recording_state: dict | None = None

    # Output
    raw_output_path: str = ""
    raw_output_size_bytes: int = 0
    output_valid: bool = False

    # Timing
    started_at: float = 0.0
    completed_at: float = 0.0
    total_duration_ms: float = 0.0

    # Errors
    error: str = ""
    failure_stage: str = ""
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["total_duration_ms"] = round(self.total_duration_ms, 1)
        return d


@dataclass
class CapturePipeline:
    """Orchestrates the full capture lifecycle."""
    config: CS2Config | None = None
    cs2: CS2Controller | None = None
    playback: PlaybackController | None = None
    pov: POVController | None = None
    obs: OBSController | None = None

    def __post_init__(self):
        if self.config is None:
            self.config = load_config()
        if self.cs2 is None:
            self.cs2 = CS2Controller(config=self.config)
        if self.playback is None:
            self.playback = PlaybackController(config=self.config, cs2=self.cs2)
        if self.pov is None:
            self.pov = POVController(cs2=self.cs2)
        if self.obs is None:
            self.obs = OBSController(
                host=self.config.obs_ws_host,
                port=self.config.obs_ws_port,
                password=self.config.obs_ws_password,
            )

    def execute(self, request: CaptureRequest) -> CaptureResult:
        """Execute the full capture pipeline.

        This is the main entry point. It runs the strict 11-step lifecycle.
        Each step either succeeds or the pipeline fails hard.
        """
        result = CaptureResult(
            clip_id=request.clip_id,
            started_at=time.time(),
        )

        try:
            # 1. Prepare workspace
            self._stage(result, "prepare_workspace",
                        lambda: self._prepare_workspace(request, result))

            # 2. Prepare playback
            self._stage(result, "prepare_playback",
                        lambda: self._prepare_playback(request, result))

            # 3. Verify playback readiness
            self._stage(result, "verify_playback",
                        lambda: self._verify_playback(result))

            # 4. Apply POV and verify
            self._stage(result, "apply_pov",
                        lambda: self._apply_pov(request, result))

            # 5. Connect OBS
            self._stage(result, "connect_obs",
                        lambda: self._connect_obs(request, result))

            # 6. Start capture
            self._stage(result, "start_capture",
                        lambda: self._start_capture(request, result))

            # 7. Resume playback + monitor
            self._stage(result, "playback_and_monitor",
                        lambda: self._playback_and_monitor(request, result))

            # 8. Stop capture
            self._stage(result, "stop_capture",
                        lambda: self._stop_capture(request, result))

            # 9. Validate raw output
            self._stage(result, "validate_output",
                        lambda: self._validate_output(request, result))

            result.status = "completed"

        except CaptureError as e:
            result.status = "failed"
            result.error = str(e)
            result.failure_stage = result.current_stage
            log.error(f"Capture failed at stage {result.current_stage}: {e}")

            # Emergency: stop recording if it was started
            self._emergency_stop_recording(result)

        except Exception as e:
            result.status = "failed"
            result.error = f"Unexpected error: {e}"
            result.failure_stage = result.current_stage
            log.exception(f"Unexpected capture error at stage {result.current_stage}")
            self._emergency_stop_recording(result)

        finally:
            result.completed_at = time.time()
            result.total_duration_ms = (result.completed_at - result.started_at) * 1000

            # Stop playback
            try:
                if self.playback:
                    self.playback.stop_playback()
            except Exception:
                pass

        return result

    def _stage(self, result: CaptureResult, name: str, fn) -> None:
        """Execute a pipeline stage with timing."""
        result.current_stage = name
        stage_start = time.time()
        log.info(f"[capture] Stage: {name}")

        try:
            fn()
            stage_duration = (time.time() - stage_start) * 1000
            result.stages.append({
                "stage": name,
                "status": "ok",
                "duration_ms": round(stage_duration, 1),
            })
            result.stage_timings[name] = round(stage_duration, 1)
        except CaptureError:
            stage_duration = (time.time() - stage_start) * 1000
            result.stages.append({
                "stage": name,
                "status": "failed",
                "duration_ms": round(stage_duration, 1),
            })
            result.stage_timings[name] = round(stage_duration, 1)
            raise

    def _prepare_workspace(self, request: CaptureRequest, result: CaptureResult) -> None:
        """Create output directories."""
        if not request.output_dir:
            raise CaptureError("No output directory specified")
        Path(request.output_dir).mkdir(parents=True, exist_ok=True)
        raw_dir = Path(request.output_dir) / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        log.info(f"Workspace prepared: {request.output_dir}")

    def _prepare_playback(self, request: CaptureRequest, result: CaptureResult) -> None:
        """Load demo and seek to start tick."""
        pb_request = PlaybackRequest(
            demo_path=request.demo_path,
            round_number=request.round_number,
            start_tick=request.start_tick,
            anchor_tick=request.anchor_tick,
            end_tick=request.end_tick,
            round_start_tick=request.round_start_tick,
            round_end_tick=request.round_end_tick,
            freeze_end_tick=request.freeze_end_tick,
        )
        pb_result = self.playback.prepare(pb_request)
        result.playback_result = pb_result.to_dict()

        if pb_result.status != "ready":
            raise CaptureError(f"Playback preparation failed: {pb_result.error}")

    def _verify_playback(self, result: CaptureResult) -> None:
        """Verify playback is in the expected state."""
        pb = result.playback_result or {}
        if not pb.get("demo_loaded"):
            raise CaptureError("Demo not loaded after preparation")
        if not pb.get("cs2_running"):
            raise CaptureError("CS2 not running after preparation")
        if not pb.get("netcon_connected"):
            raise CaptureError("Netcon not connected after preparation")

    def _apply_pov(self, request: CaptureRequest, result: CaptureResult) -> None:
        """Apply player POV lock — hard failure if not achieved."""
        if request.pov_mode == "freecam":
            pov_state = self.pov.apply_pov(
                player_name=request.player_name,
                player_steamid64=request.player_steamid64,
                camera_mode="freecam",
            )
        else:
            if not request.player_name:
                raise CaptureError("Player name required for player_pov mode")

            try:
                pov_state = self.pov.apply_pov(
                    player_name=request.player_name,
                    player_steamid64=request.player_steamid64,
                    camera_mode="player_pov",
                    observer_mode=request.camera_mode,
                )
            except POVError as e:
                raise CaptureError(f"POV lock failed: {e}")

            if not pov_state.applied or not pov_state.verified:
                raise CaptureError(
                    f"POV lock not verified for {request.player_name!r}: "
                    f"{pov_state.failure_reason}"
                )

        result.pov_state = pov_state.to_dict()

    def _connect_obs(self, request: CaptureRequest, result: CaptureResult) -> None:
        """Connect to OBS and configure output."""
        if not self.obs.available:
            raise CaptureError("OBS WebSocket library not available")
        try:
            self.obs.connect()
        except OBSError as e:
            raise CaptureError(f"OBS connection failed: {e}")

        raw_dir = str(Path(request.output_dir) / "raw")
        self.obs.set_output_directory(raw_dir)

    def _start_capture(self, request: CaptureRequest, result: CaptureResult) -> None:
        """Prepare POV for recording and start OBS capture."""
        # Final POV preparation
        try:
            self.pov.prepare_for_recording()
        except POVError as e:
            raise CaptureError(f"Pre-recording POV preparation failed: {e}")

        # Start recording
        try:
            self.obs.start_recording()
            result.recording_state = self.obs.state.to_dict()
        except OBSError as e:
            raise CaptureError(f"OBS recording start failed: {e}")

        # Brief settle before resume
        time.sleep(0.5)

    def _playback_and_monitor(self, request: CaptureRequest, result: CaptureResult) -> None:
        """Resume playback smoothly and only correct POV if drift is detected."""
        # Resume playback
        self.playback.resume_and_play(request.clip_duration_s)

        # Verify after resume, and only re-apply if the camera actually drifted.
        try:
            pov_state = self.pov.verify_after_resume(settle_s=0.35)
        except POVError as e:
            raise CaptureError(f"POV drift detected after resume: {e}")
        if not pov_state.applied or not pov_state.verified:
            raise CaptureError(
                "POV drift detected after resume: "
                + (pov_state.failure_reason or "verification failed")
            )

        # Wait for clip duration
        clip_duration = request.clip_duration_s
        log.info(f"Recording for {clip_duration:.1f}s...")

        # Monitor in short intervals. Do lightweight probes only, and correct
        # the POV only when drift is actually detected twice in a row. This
        # avoids visible stutter caused by reacting to a single weak probe.
        elapsed = 0.0
        consecutive_drift_failures = 0
        corrective_relocks = 0
        while elapsed < clip_duration:
            sleep_time = min(1.0, clip_duration - elapsed)
            time.sleep(sleep_time)
            elapsed += sleep_time

            try:
                pov_state = self.pov.poll_lock()
            except POVError as e:
                raise CaptureError(f"POV verification failed during capture: {e}")

            if pov_state.applied and pov_state.verified:
                consecutive_drift_failures = 0
                continue

            consecutive_drift_failures += 1
            if consecutive_drift_failures < 2:
                continue

            if corrective_relocks >= 1:
                raise CaptureError(
                    "POV drift detected repeatedly during capture: "
                    + (pov_state.failure_reason or "verification failed")
                )

            if not pov_state.applied or not pov_state.verified:
                result.warnings.append(
                    "POV drift detected during capture; attempting a single corrective re-lock."
                )
                try:
                    corrected = self.pov.reapply_pov()
                except POVError as e:
                    raise CaptureError(f"POV drift detected during capture: {e}")
                if not corrected.applied or not corrected.verified:
                    raise CaptureError(
                        "POV drift detected during capture: "
                        + (corrected.failure_reason or "verification failed")
                    )
                corrective_relocks += 1
                consecutive_drift_failures = 0

        # Extra buffer after clip ends
        time.sleep(0.5)

    def _stop_capture(self, request: CaptureRequest, result: CaptureResult) -> None:
        """Stop OBS recording and pause playback."""
        # Pause demo
        try:
            self.playback.stop_playback()
        except Exception:
            pass

        # Stop recording
        try:
            output_path = self.obs.stop_recording()
            result.recording_state = self.obs.state.to_dict()

            if output_path:
                result.raw_output_path = output_path
            else:
                # Wait for file to appear
                raw_dir = str(Path(request.output_dir) / "raw")
                recording_start = self.obs.state.started_at
                found = self.obs.wait_for_recording_file(
                    raw_dir, started_after=recording_start - 5,
                    timeout_s=15, min_size_bytes=1024,
                )
                if found:
                    result.raw_output_path = str(found)
                else:
                    raise CaptureError("No recording file found after stop")

        except OBSError as e:
            raise CaptureError(f"OBS recording stop failed: {e}")

    def _validate_output(self, request: CaptureRequest, result: CaptureResult) -> None:
        """Validate the raw recording output."""
        if not result.raw_output_path:
            raise CaptureError("No raw output path available")

        raw_path = Path(result.raw_output_path)
        if not raw_path.exists():
            raise CaptureError(f"Raw output file not found: {raw_path}")

        size = raw_path.stat().st_size
        result.raw_output_size_bytes = size

        min_bytes = self.config.postprocess_minimum_output_bytes
        if size < min_bytes:
            raise CaptureError(
                f"Raw output too small ({size} bytes < {min_bytes} minimum). "
                "Recording may have failed."
            )

        result.output_valid = True
        log.info(f"Raw output validated: {raw_path} ({size} bytes)")

    def _emergency_stop_recording(self, result: CaptureResult) -> None:
        """Emergency stop recording if pipeline fails mid-capture."""
        if self.obs and self.obs.state.recording:
            try:
                self.obs.stop_recording()
                log.info("Emergency: OBS recording stopped")
            except Exception as e:
                log.error(f"Emergency stop failed: {e}")

    def cleanup(self) -> None:
        """Clean up all resources."""
        if self.obs:
            self.obs.disconnect()
        if self.playback:
            self.playback.cleanup()
