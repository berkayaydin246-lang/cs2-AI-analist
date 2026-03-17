"""
Tests for POV identity (steamid64) propagation through the clip pipeline
and camera strictness enforcement.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from src.clip_planner import build_clip_plans, _plan_highlight_clip
from src.clip_renderer import build_render_job_input
from src.cs2_playback import build_playback_job, validate_playback_job
from src.game_control import CameraSelection


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _make_highlight(
    *,
    highlight_type: str = "multi_kill",
    primary_player: str = "TestPlayer",
    round_number: int = 3,
    start_tick: int = 5000,
    anchor_tick: int = 5200,
    end_tick: int = 5500,
    score: float = 0.85,
) -> dict:
    return {
        "highlight_id": f"hl_{highlight_type}_r{round_number}",
        "type": highlight_type,
        "primary_player": primary_player,
        "round_number": round_number,
        "start_tick": start_tick,
        "anchor_tick": anchor_tick,
        "end_tick": end_tick,
        "score": score,
        "title": f"{highlight_type} by {primary_player}",
        "description": "Test highlight",
        "involved_players": [primary_player],
        "tags": [highlight_type],
        "metadata": {},
    }


def _make_parsed_data(
    highlights: list[dict] | None = None,
    player_identities: dict | None = None,
) -> dict:
    if player_identities is None:
        player_identities = {
            "by_name": {
                "TestPlayer": "76561198012345678",
                "OtherPlayer": "76561198087654321",
            },
            "by_steamid": {
                "76561198012345678": {"player_name": "TestPlayer", "appearances": 50},
                "76561198087654321": {"player_name": "OtherPlayer", "appearances": 45},
            },
        }
    return {
        "highlights": highlights or [_make_highlight()],
        "rounds": [
            {"round_num": 3, "start_tick": 4500, "end_tick": 6500},
        ],
        "player_identities": player_identities,
    }


# ── Tests: clip_planner steamid64 propagation ────────────────────────────────

class TestClipPlannerSteamID64(unittest.TestCase):
    def test_clip_plan_includes_pov_player_steamid64(self):
        """Clip plan must include pov_player_steamid64 from player_identities."""
        parsed = _make_parsed_data()
        result = build_clip_plans(parsed)
        plans = result["clip_plans"]
        self.assertGreater(len(plans), 0)

        plan = plans[0]
        self.assertEqual(plan["pov_player"], "TestPlayer")
        self.assertEqual(plan["pov_player_steamid64"], "76561198012345678")

    def test_clip_plan_steamid64_none_when_player_not_in_identities(self):
        """pov_player_steamid64 is None when player not found in identities."""
        parsed = _make_parsed_data(
            highlights=[_make_highlight(primary_player="UnknownPlayer")],
            player_identities={"by_name": {}, "by_steamid": {}},
        )
        result = build_clip_plans(parsed)
        plans = result["clip_plans"]
        self.assertGreater(len(plans), 0)
        self.assertIsNone(plans[0]["pov_player_steamid64"])

    def test_clip_plan_steamid64_none_when_no_identities(self):
        """pov_player_steamid64 is None when player_identities not provided."""
        parsed = _make_parsed_data()
        del parsed["player_identities"]
        result = build_clip_plans(parsed)
        plans = result["clip_plans"]
        self.assertGreater(len(plans), 0)
        self.assertIsNone(plans[0]["pov_player_steamid64"])

    def test_multiple_highlights_get_correct_steamid64(self):
        """Each highlight gets the correct steamid64 for its pov_player."""
        parsed = _make_parsed_data(
            highlights=[
                _make_highlight(primary_player="TestPlayer", highlight_type="multi_kill", round_number=1, start_tick=1000, anchor_tick=1200, end_tick=1500),
                _make_highlight(primary_player="OtherPlayer", highlight_type="opening_kill", round_number=2, start_tick=3000, anchor_tick=3100, end_tick=3300),
            ],
        )
        parsed["rounds"] = [
            {"round_num": 1, "start_tick": 500, "end_tick": 2000},
            {"round_num": 2, "start_tick": 2500, "end_tick": 4000},
        ]
        result = build_clip_plans(parsed)
        plans = result["clip_plans"]
        self.assertEqual(len(plans), 2)

        by_player = {p["pov_player"]: p for p in plans}
        self.assertEqual(by_player["TestPlayer"]["pov_player_steamid64"], "76561198012345678")
        self.assertEqual(by_player["OtherPlayer"]["pov_player_steamid64"], "76561198087654321")


# ── Tests: clip_renderer steamid64 propagation ──────────────────────────────

class TestClipRendererSteamID64(unittest.TestCase):
    def test_render_job_propagates_steamid64_from_clip_plan(self):
        """build_render_job_input must populate target_player_steamid64 from clip plan."""
        clip_plan = {
            "clip_plan_id": "clip_test_001",
            "source_highlight_id": "hl_test",
            "round_number": 3,
            "start_tick": 5000,
            "anchor_tick": 5200,
            "end_tick": 5500,
            "pov_mode": "player_pov",
            "pov_player": "TestPlayer",
            "pov_player_steamid64": "76561198012345678",
            "metadata": {},
        }
        job = build_render_job_input("demo_001", clip_plan)
        self.assertEqual(job["target_player_steamid64"], "76561198012345678")
        self.assertEqual(
            job["ingame_capture_settings"]["target_player_steamid64"],
            "76561198012345678",
        )

    def test_render_job_settings_override_steamid64(self):
        """Explicit target_settings should override clip plan steamid64."""
        clip_plan = {
            "clip_plan_id": "clip_test_001",
            "source_highlight_id": "hl_test",
            "round_number": 3,
            "start_tick": 5000,
            "anchor_tick": 5200,
            "end_tick": 5500,
            "pov_player": "TestPlayer",
            "pov_player_steamid64": "76561198012345678",
            "metadata": {},
        }
        job = build_render_job_input(
            "demo_001",
            clip_plan,
            target_settings={"target_player_steamid64": "76561198099999999"},
        )
        self.assertEqual(job["target_player_steamid64"], "76561198099999999")
        self.assertEqual(
            job["ingame_capture_settings"]["target_player_steamid64"],
            "76561198099999999",
        )

    def test_render_job_no_steamid64_when_absent(self):
        """target_player_steamid64 is None when not in clip plan or settings."""
        clip_plan = {
            "clip_plan_id": "clip_test_001",
            "source_highlight_id": "hl_test",
            "round_number": 3,
            "start_tick": 5000,
            "anchor_tick": 5200,
            "end_tick": 5500,
            "pov_player": "TestPlayer",
            "metadata": {},
        }
        job = build_render_job_input("demo_001", clip_plan)
        self.assertIsNone(job["target_player_steamid64"])
        self.assertIsNone(job["ingame_capture_settings"]["target_player_steamid64"])


# ── Tests: cs2_playback steamid64 propagation ───────────────────────────────

class TestPlaybackJobSteamID64(unittest.TestCase):
    def test_build_playback_job_includes_steamid64(self):
        """build_playback_job must carry pov_player_steamid64 from clip plan."""
        clip_plan = {
            "round_number": 3,
            "start_tick": 5000,
            "anchor_tick": 5200,
            "end_tick": 5500,
            "pov_player": "TestPlayer",
            "pov_player_steamid64": "76561198012345678",
            "metadata": {},
        }
        job = build_playback_job("/path/to/demo.dem", clip_plan)
        self.assertEqual(job["pov_player"], "TestPlayer")
        self.assertEqual(job["pov_player_steamid64"], "76561198012345678")

    def test_build_playback_job_steamid64_none_when_absent(self):
        """pov_player_steamid64 should be None when clip plan lacks it."""
        clip_plan = {
            "round_number": 3,
            "start_tick": 5000,
            "anchor_tick": 5200,
            "end_tick": 5500,
            "pov_player": "TestPlayer",
            "metadata": {},
        }
        job = build_playback_job("/path/to/demo.dem", clip_plan)
        self.assertIsNone(job["pov_player_steamid64"])


# ── Tests: CameraSelection steamid64 ────────────────────────────────────────

class TestCameraSelectionSteamID64(unittest.TestCase):
    def test_camera_selection_carries_steamid64(self):
        """CameraSelection dataclass must accept and carry pov_player_steamid64."""
        cam = CameraSelection(
            camera_mode="player_pov",
            pov_player="TestPlayer",
            pov_player_steamid64="76561198012345678",
        )
        self.assertEqual(cam.pov_player, "TestPlayer")
        self.assertEqual(cam.pov_player_steamid64, "76561198012345678")

    def test_camera_selection_steamid64_defaults_none(self):
        """CameraSelection.pov_player_steamid64 defaults to None."""
        cam = CameraSelection(camera_mode="observer_auto")
        self.assertIsNone(cam.pov_player_steamid64)


# ── Tests: camera strictness (player_pov failure = hard failure) ─────────────

class TestCameraStrictness(unittest.TestCase):
    def test_validate_playback_job_warns_player_pov_without_player(self):
        """validate_playback_job must warn when player_pov has no pov_player."""
        job = {
            "demo_path": "/path/to/demo.dem",
            "round_number": 3,
            "start_tick": 5000,
            "anchor_tick": 5200,
            "end_tick": 5500,
            "camera_mode": "player_pov",
            "pov_player": None,
        }
        with patch("src.cs2_playback.Path") as MockPath:
            MockPath.return_value.is_file.return_value = True
            warnings = validate_playback_job(job)
        self.assertTrue(
            any("player_pov" in w and "pov_player" in w for w in warnings),
            f"Expected player_pov/pov_player warning, got: {warnings}",
        )


# ── Tests: end-to-end pipeline identity coherence ───────────────────────────

class TestEndToEndIdentityCoherence(unittest.TestCase):
    def test_full_pipeline_steamid64_flow(self):
        """steamid64 flows from parsed_data → clip_plan → render_job → playback_job."""
        # Step 1: clip_planner
        parsed = _make_parsed_data(
            highlights=[_make_highlight(primary_player="TestPlayer")],
        )
        clip_result = build_clip_plans(parsed)
        plan = clip_result["clip_plans"][0]
        self.assertEqual(plan["pov_player_steamid64"], "76561198012345678")

        # Step 2: clip_renderer
        render_job = build_render_job_input("demo_001", plan)
        self.assertEqual(render_job["target_player_steamid64"], "76561198012345678")
        self.assertEqual(
            render_job["ingame_capture_settings"]["target_player_steamid64"],
            "76561198012345678",
        )

        # Step 3: cs2_playback
        playback_job = build_playback_job("/path/to/demo.dem", plan)
        self.assertEqual(playback_job["pov_player_steamid64"], "76561198012345678")

        # Step 4: CameraSelection
        cam = CameraSelection(
            camera_mode=playback_job.get("camera_mode", "observer_auto"),
            pov_player=playback_job.get("pov_player"),
            pov_player_steamid64=playback_job.get("pov_player_steamid64"),
        )
        self.assertEqual(cam.pov_player_steamid64, "76561198012345678")
        self.assertEqual(cam.pov_player, "TestPlayer")


if __name__ == "__main__":
    unittest.main()
