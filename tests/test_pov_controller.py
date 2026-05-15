"""Tests for pov_controller.py — stricter POV lock verification."""
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from src.pov_controller import POVController, POVState, POVError
from src.cs2_controller import CS2Controller, CS2ControlError


def _make_mock_cs2(lock_accountid="396367815", spec_mode="4", autodirector="0", auto_observer="0", camera_follow="1"):
    cs2 = MagicMock(spec=CS2Controller)

    def exec_command_side_effect(cmd):
        mapping = {
            "spec_lock_to_accountid": f'"spec_lock_to_accountid" = "{lock_accountid}"',
            "spec_mode": f'"spec_mode" = "{spec_mode}"',
            "spec_autodirector": f'"spec_autodirector" = "{autodirector}"',
            "cl_spec_auto_observer": f'"cl_spec_auto_observer" = "{auto_observer}"',
            "spec_camera_follow": f'"spec_camera_follow" = "{camera_follow}"',
            "status": '# 3 "alice" STEAM_1:0:123 00:10 64 0 active',
        }
        return mapping.get(cmd, "")

    cs2.exec_command.side_effect = exec_command_side_effect
    return cs2


class TestPOVState:
    def test_default_state(self):
        state = POVState()
        assert not state.applied
        assert not state.verified
        assert state.apply_attempts == 0

    def test_serialization(self):
        state = POVState(
            requested_player="alice",
            requested_steamid64="76561198356633543",
            target_accountid="396367815",
            applied=True,
            verified=True,
        )
        d = state.to_dict()
        assert d["requested_player"] == "alice"
        assert d["requested_steamid64"] == "76561198356633543"
        assert d["target_accountid"] == "396367815"
        assert d["applied"] is True
        assert d["verified"] is True


class TestPOVController:
    @pytest.fixture
    def mock_cs2(self):
        return _make_mock_cs2()

    def test_apply_pov_sends_accountid_and_name_commands(self, mock_cs2):
        pov = POVController(cs2=mock_cs2, max_apply_attempts=1, apply_settle_s=0)
        state = pov.apply_pov("alice", "76561198356633543")
        assert state.applied
        assert state.verified
        assert state.target_accountid == "396367815"
        commands = state.commands_sent
        assert "spec_autodirector 0" in commands
        assert "cl_spec_auto_observer 0" in commands
        assert any("spec_player_by_accountid 396367815" in cmd for cmd in commands)
        assert any("spec_lock_to_accountid 396367815" in cmd for cmd in commands)
        assert not any('spec_player_by_name "alice"' in cmd for cmd in commands)
        assert any("spec_mode 4" in cmd for cmd in commands)

    def test_pov_failure_raises_error_on_command_failure(self, mock_cs2):
        mock_cs2.exec_command.side_effect = CS2ControlError("netcon lost")
        pov = POVController(cs2=mock_cs2, max_apply_attempts=2, apply_settle_s=0)
        with pytest.raises(POVError):
            pov.apply_pov("alice", "76561198356633543")
        assert pov.state.apply_attempts == 2
        assert not pov.state.applied

    def test_pov_failure_raises_error_on_verification_mismatch(self):
        mock_cs2 = _make_mock_cs2(lock_accountid="123")
        pov = POVController(cs2=mock_cs2, max_apply_attempts=1, apply_settle_s=0)
        with pytest.raises(POVError, match="spec_lock_to_accountid"):
            pov.apply_pov("alice", "76561198356633543")

    def test_autodirector_false_string_is_accepted(self):
        mock_cs2 = _make_mock_cs2(
            lock_accountid="396367815",
            spec_mode="4",
            autodirector="false",
        )
        pov = POVController(cs2=mock_cs2, max_apply_attempts=1, apply_settle_s=0)
        state = pov.apply_pov("alice", "76561198356633543")
        assert state.verified is True

    def test_first_person_mode_falls_back_to_alternate_spec_mode(self):
        mock_cs2 = _make_mock_cs2(lock_accountid="396367815", spec_mode="1", autodirector="0")
        pov = POVController(cs2=mock_cs2, max_apply_attempts=2, apply_settle_s=0)
        state = pov.apply_pov("alice", "76561198356633543")
        assert state.verified is True
        assert state.spec_mode_value in (1, 4)

    def test_freecam_mode(self, mock_cs2):
        pov = POVController(cs2=mock_cs2, apply_settle_s=0)
        state = pov.apply_pov("", camera_mode="freecam")
        assert state.applied
        assert "spec_mode 5" in state.commands_sent

    def test_reapply_pov(self, mock_cs2):
        pov = POVController(cs2=mock_cs2, max_apply_attempts=1, apply_settle_s=0)
        pov.apply_pov("alice", "76561198356633543")
        first_calls = mock_cs2.exec_command.call_count
        pov.reapply_pov()
        assert mock_cs2.exec_command.call_count > first_calls

    def test_prepare_for_recording_sends_hud_commands(self, mock_cs2):
        pov = POVController(cs2=mock_cs2, max_apply_attempts=1, apply_settle_s=0)
        pov.apply_pov("alice", "76561198356633543")
        pov.prepare_for_recording()
        commands = pov.state.commands_sent
        assert any("cl_drawhud" in cmd for cmd in commands)
        assert any("spec_lock_to_accountid 396367815" in cmd for cmd in commands)
        assert not any('spec_player_by_name "alice"' in cmd for cmd in commands if "spec_player_by_name" in cmd)

    def test_prepare_for_recording_fails_if_not_applied(self, mock_cs2):
        pov = POVController(cs2=mock_cs2)
        with pytest.raises(POVError, match="not applied"):
            pov.prepare_for_recording()

    def test_third_person_observer_mode(self):
        mock_cs2 = _make_mock_cs2(lock_accountid="396367815", spec_mode="5")
        pov = POVController(cs2=mock_cs2, max_apply_attempts=1, apply_settle_s=0)
        pov.apply_pov("alice", "76561198356633543", observer_mode="third_person")
        commands = pov.state.commands_sent
        assert any("spec_mode 5" in cmd for cmd in commands)

    def test_auto_observer_false_string_is_required(self):
        mock_cs2 = _make_mock_cs2(auto_observer="false")
        pov = POVController(cs2=mock_cs2, max_apply_attempts=1, apply_settle_s=0)
        state = pov.apply_pov("alice", "76561198356633543")
        assert state.verified is True

    def test_name_fallback_used_when_no_accountid(self):
        mock_cs2 = _make_mock_cs2(lock_accountid="", spec_mode="4")
        pov = POVController(cs2=mock_cs2, max_apply_attempts=1, apply_settle_s=0)
        state = pov.apply_pov("alice", "")
        assert state.applied is True
        assert any('spec_player_by_name "alice"' in cmd for cmd in state.commands_sent)

    def test_accountid_targeting_does_not_fall_back_to_name_when_present(self):
        mock_cs2 = _make_mock_cs2(lock_accountid="396367815", spec_mode="1")
        pov = POVController(cs2=mock_cs2, max_apply_attempts=2, apply_settle_s=0)
        state = pov.apply_pov("alice", "76561198356633543")

        assert state.verified is True
        assert not any('spec_player_by_name "alice"' in cmd for cmd in state.commands_sent)

    def test_missing_identity_rejected(self, mock_cs2):
        pov = POVController(cs2=mock_cs2, max_apply_attempts=1, apply_settle_s=0)
        with pytest.raises(POVError, match="player_name or player_steamid64"):
            pov.apply_pov("", "")

    def test_tolerates_alternate_valid_spec_modes(self):
        mock_cs2 = _make_mock_cs2(lock_accountid="396367815", spec_mode="1")
        pov = POVController(cs2=mock_cs2, max_apply_attempts=1, apply_settle_s=0)
        state = pov.apply_pov("alice", "76561198356633543")
        assert state.verified is True

    def test_confirmation_window_defaults_are_extended(self, mock_cs2):
        pov = POVController(cs2=mock_cs2)
        assert pov.confirmation_polls == 5
        assert pov.confirmation_interval_s == 0.3
