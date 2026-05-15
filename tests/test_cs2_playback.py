"""Tests for cs2_playback.py probe and readiness behavior."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src.cs2_playback import PlaybackController, PlaybackError
from src.cs2_config import load_config
from src.cs2_controller import CS2Controller, CS2ControlError


def _make_controller():
    config = load_config()
    cs2 = MagicMock(spec=CS2Controller)
    return PlaybackController(config=config, cs2=cs2), cs2


def test_probe_playback_channel_accepts_valid_probe_values():
    playback, cs2 = _make_controller()
    cs2.exec_command.side_effect = [
        '"demo_timescale" = "1"',
        '"spec_mode" = "4"',
        '"spec_autodirector" = "0"',
        '"cl_spec_auto_observer" = "0"',
    ]

    result = playback._probe_playback_channel()

    assert result["ok"] is True
    assert len(result["details"]) == 4


def test_probe_playback_channel_rejects_empty_probe_output():
    playback, cs2 = _make_controller()
    cs2.exec_command.side_effect = ['"demo_timescale" = "1"', ""]

    result = playback._probe_playback_channel(commands=["demo_timescale", "spec_mode"])

    assert result["ok"] is False
    assert result["failed_command"] == "spec_mode"


def test_probe_playback_channel_rejects_controller_errors():
    playback, cs2 = _make_controller()
    cs2.exec_command.side_effect = CS2ControlError("netcon lost")

    result = playback._probe_playback_channel()

    assert result["ok"] is False
    assert "netcon lost" in result["error"]


def test_wait_for_stable_probe_requires_consecutive_successes():
    playback, _ = _make_controller()

    with patch.object(
        playback,
        "_probe_playback_channel_with_options",
        side_effect=[
            {"ok": False, "details": []},
            {"ok": True, "details": []},
            {"ok": True, "details": []},
        ],
    ):
        with patch("src.cs2_playback.time.sleep", return_value=None):
            result = playback._wait_for_stable_probe(
                "demo_load",
                timeout_s=1.0,
                interval_s=0.01,
                consecutive_successes=2,
            )

    assert result["stable"] is True
    assert result["attempts"] == 3


def test_wait_for_stable_probe_times_out_when_channel_never_stabilizes():
    playback, _ = _make_controller()

    with patch.object(
        playback,
        "_probe_playback_channel_with_options",
        return_value={"ok": False, "details": [], "error": "still loading"},
    ):
        with patch("src.cs2_playback.time.sleep", return_value=None):
            with pytest.raises(PlaybackError, match="did not stabilize"):
                playback._wait_for_stable_probe(
                    "demo_load",
                    timeout_s=0.05,
                    interval_s=0.01,
                    consecutive_successes=2,
                )


def test_probe_playback_channel_can_allow_empty_output():
    playback, cs2 = _make_controller()
    cs2.exec_command.return_value = ""

    result = playback._probe_playback_channel_with_options(
        commands=["demo_timescale"],
        allow_empty=True,
    )

    assert result["ok"] is True


def test_wait_for_load_uses_fallback_probe_when_strong_probe_fails():
    playback, _ = _make_controller()

    calls = []

    def fake_wait(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise PlaybackError("strong probe failed")
        return {"attempts": 2, "stable": True, "last_probe": {"ok": True}, "label": "demo_load_fallback"}

    with patch.object(playback, "_wait_for_stable_probe", side_effect=fake_wait):
        with patch("src.cs2_playback.time.sleep", return_value=None):
            from src.cs2_playback import PlaybackResult

            result = PlaybackResult()
            playback._step_wait_for_load(result)

    assert result.steps[-1]["status"] == "ok"
    assert "warnings" in result.steps[-1]
    assert calls[0].get("allow_empty", False) is False
    assert calls[1]["allow_empty"] is True


def test_step_seek_uses_fallback_probe_when_strong_probe_fails():
    playback, cs2 = _make_controller()
    cs2.seek_to_tick.return_value = None

    calls = []

    def fake_wait(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise PlaybackError("strong seek probe failed")
        return {"attempts": 3, "stable": True, "last_probe": {"ok": True}, "label": "seek_fallback"}

    with patch.object(playback, "_wait_for_stable_probe", side_effect=fake_wait):
        with patch("src.cs2_playback.time.sleep", return_value=None):
            from src.cs2_playback import PlaybackRequest, PlaybackResult

            request = PlaybackRequest(
                demo_path=__file__,
                round_number=1,
                start_tick=1234,
                anchor_tick=1240,
                end_tick=1400,
            )
            result = PlaybackResult()
            playback._step_seek(request, result)

    assert result.actual_seek_tick == 1234
    assert result.steps[-1]["status"] == "ok"
    assert result.steps[-1]["probe_strength"] == "weak"
    assert "warnings" in result.steps[-1]
    assert calls[0].get("allow_empty", False) is False
    assert calls[1]["allow_empty"] is True
