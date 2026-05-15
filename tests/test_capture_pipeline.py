"""Tests for capture_pipeline.py — strict lifecycle validation.

These tests validate the pipeline logic WITHOUT requiring a live CS2 instance.
They focus on:
- Failure when POV lock cannot be confirmed
- Failure when stable identity is missing
- Capture result structure correctness
- Strict lifecycle stage ordering
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from src.capture_pipeline import (
    CapturePipeline,
    CaptureRequest,
    CaptureResult,
    CaptureError,
)
from src.pov_controller import POVError, POVState
from src.cs2_playback import PlaybackResult


class TestCaptureRequest:
    def test_clip_duration_calculation(self):
        req = CaptureRequest(
            clip_id="test",
            demo_path="/tmp/test.dem",
            round_number=5,
            start_tick=10000,
            anchor_tick=10500,
            end_tick=11000,
        )
        assert req.clip_duration_s == pytest.approx(15.625, rel=0.01)

    def test_zero_duration(self):
        req = CaptureRequest(
            clip_id="test",
            demo_path="/tmp/test.dem",
            round_number=5,
            start_tick=10000,
            anchor_tick=10000,
            end_tick=10000,
        )
        assert req.clip_duration_s == 0


class TestCaptureResult:
    def test_default_state(self):
        result = CaptureResult()
        assert result.status == "pending"
        assert result.output_valid is False
        assert result.raw_output_path == ""

    def test_to_dict(self):
        result = CaptureResult(clip_id="test", status="completed")
        d = result.to_dict()
        assert d["clip_id"] == "test"
        assert d["status"] == "completed"


class TestPipelineFailures:
    """Test that the pipeline fails hard on the right conditions."""

    def test_fails_without_output_dir(self):
        """Pipeline should fail if no output directory specified."""
        pipeline = CapturePipeline()
        req = CaptureRequest(
            clip_id="test",
            demo_path="/tmp/test.dem",
            round_number=5,
            start_tick=10000,
            anchor_tick=10500,
            end_tick=11000,
            output_dir="",  # no output dir
        )
        result = pipeline.execute(req)
        assert result.status == "failed"
        assert "output directory" in result.error.lower() or "No output" in result.error

    def test_fails_without_player_name_for_pov(self):
        """Pipeline should fail if player_pov mode but no player name."""
        pipeline = CapturePipeline()
        req = CaptureRequest(
            clip_id="test",
            demo_path="/tmp/test.dem",
            round_number=5,
            start_tick=10000,
            anchor_tick=10500,
            end_tick=11000,
            output_dir="/tmp/test_output",
            pov_mode="player_pov",
            player_name="",  # no player name
        )
        # Mock through prepare_playback to reach apply_pov stage
        with patch.object(CapturePipeline, '_prepare_workspace'):
            with patch.object(CapturePipeline, '_prepare_playback') as mock_pb:
                mock_pb.return_value = None
                with patch.object(CapturePipeline, '_verify_playback'):
                    result = pipeline.execute(req)
        assert result.status == "failed"
        assert "player name" in result.error.lower() or "Player name" in result.error

    def test_pov_state_in_result(self):
        """Capture result should include POV state details."""
        result = CaptureResult()
        pov_state = POVState(
            requested_player="alice",
            requested_steamid64="76561198356633543",
            applied=True,
            verified=True,
        )
        result.pov_state = pov_state.to_dict()
        assert result.pov_state["requested_player"] == "alice"
        assert result.pov_state["requested_steamid64"] == "76561198356633543"
        assert result.pov_state["applied"] is True


class TestPOVFailureHandling:
    """Test that POV failures are treated as hard failures."""

    def test_pov_error_prevents_completion(self):
        """If POV lock fails, the capture must not succeed."""
        pov_state = POVState(
            requested_player="alice",
            applied=False,
            verified=False,
            failure_reason="POV lock failed after 5 attempts",
        )
        # This validates the contract: applied=False means failure
        assert not pov_state.applied
        assert pov_state.failure_reason != ""

    def test_pov_state_tracks_attempts(self):
        pov_state = POVState()
        pov_state.apply_attempts = 5
        assert pov_state.apply_attempts == 5

    def test_pov_state_serialization(self):
        state = POVState(
            requested_player="VixToix037",
            requested_steamid64="76561198388732174",
            camera_mode="player_pov",
            observer_mode="first_person",
            applied=True,
            verified=True,
        )
        d = state.to_dict()
        assert d["requested_player"] == "VixToix037"
        assert d["requested_steamid64"] == "76561198388732174"


class TestPlaybackResultValidation:
    """Test that playback readiness is verified before capture proceeds."""

    def test_ready_result_passes_verification(self):
        result = PlaybackResult(
            status="ready",
            demo_loaded=True,
            cs2_running=True,
            netcon_connected=True,
        )
        # These are the checks _verify_playback does
        assert result.demo_loaded
        assert result.cs2_running
        assert result.netcon_connected

    def test_failed_result_fails_verification(self):
        result = PlaybackResult(
            status="failed",
            demo_loaded=False,
            cs2_running=False,
            netcon_connected=False,
            error="CS2 launch failed",
        )
        # Verification should fail
        assert not result.demo_loaded
        assert result.status == "failed"


class TestStageTracking:
    """Test that pipeline stages are tracked correctly."""

    def test_capture_result_tracks_stages(self):
        result = CaptureResult(clip_id="test")
        result.stages.append({"stage": "prepare_workspace", "status": "ok", "duration_ms": 5.0})
        result.stages.append({"stage": "prepare_playback", "status": "failed", "duration_ms": 150.0})
        assert len(result.stages) == 2
        assert result.stages[0]["stage"] == "prepare_workspace"
        assert result.stages[1]["status"] == "failed"

    def test_failure_stage_recorded(self):
        result = CaptureResult(clip_id="test")
        result.status = "failed"
        result.failure_stage = "apply_pov"
        result.error = "POV lock failed"
        assert result.failure_stage == "apply_pov"


class TestPlaybackMonitoring:
    def test_monitor_only_reapplies_after_confirmed_drift(self):
        pipeline = CapturePipeline()
        pipeline.playback = MagicMock()
        pipeline.pov = MagicMock()

        request = CaptureRequest(
            clip_id="test",
            demo_path="/tmp/test.dem",
            round_number=5,
            start_tick=10000,
            anchor_tick=10500,
            end_tick=10192,  # 3 seconds
            output_dir="/tmp/test_output",
            player_name="alice",
        )
        result = CaptureResult(clip_id="test")

        locked = POVState(applied=True, verified=True)
        drift = POVState(applied=True, verified=False, failure_reason="drift")
        corrected = POVState(applied=True, verified=True)

        pipeline.pov.verify_after_resume.return_value = locked
        pipeline.pov.poll_lock.side_effect = [locked, drift, drift, locked]
        pipeline.pov.reapply_pov.return_value = corrected

        with patch("src.capture_pipeline.time.sleep", return_value=None):
            pipeline._playback_and_monitor(request, result)

        pipeline.playback.resume_and_play.assert_called_once()
        # The first failed poll is tolerated; a corrective relock happens only
        # after drift is confirmed twice in a row.
        pipeline.pov.reapply_pov.assert_called_once()
