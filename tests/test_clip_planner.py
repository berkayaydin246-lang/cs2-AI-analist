"""Tests for clip_planner.py — deterministic clip window derivation."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from src.clip_planner import (
    plan_clip,
    plan_clips,
    ClipPlan,
    TIMING_RULES,
    DEFAULT_TIMING,
    _compute_clip_window,
)

TICK_RATE = 64


def _ticks(seconds: float) -> int:
    return int(seconds * TICK_RATE)


def _make_highlight(
    event_type="opening_kill",
    round_number=5,
    anchor_tick=10000,
    player_name="alice",
    player_steamid64="76561198356633543",
    score=25.0,
    priority=1,
    confidence=0.9,
    round_start_tick=8000,
    round_end_tick=15000,
    freeze_end_tick=8500,
    kills_in_sequence=None,
):
    return {
        "highlight_id": f"hl_{event_type}_r{round_number}_test",
        "player_name": player_name,
        "player_steamid64": player_steamid64,
        "round_number": round_number,
        "event_type": event_type,
        "anchor_tick": anchor_tick,
        "score": score,
        "priority": priority,
        "confidence": confidence,
        "selection_reason": "test highlight",
        "kills_in_sequence": kills_in_sequence or [],
        "round_start_tick": round_start_tick,
        "round_end_tick": round_end_tick,
        "freeze_end_tick": freeze_end_tick,
    }


class TestComputeClipWindow:
    def test_basic_window(self):
        hl = _make_highlight(anchor_tick=10000)
        rule = TIMING_RULES["opening_kill"]
        start, anchor, end, lead_in, tail, clamped = _compute_clip_window(hl, rule)
        assert anchor == 10000
        assert start < anchor
        assert end > anchor
        assert start == anchor - _ticks(rule["lead_in_s"])

    def test_minimum_duration_enforced(self):
        hl = _make_highlight(anchor_tick=10000)
        rule = {"lead_in_s": 0.1, "tail_s": 0.1, "min_duration_s": 5.0, "max_duration_s": 30.0}
        start, anchor, end, _, _, _ = _compute_clip_window(hl, rule)
        duration_s = (end - start) / TICK_RATE
        assert duration_s >= 5.0

    def test_maximum_duration_enforced(self):
        hl = _make_highlight(
            anchor_tick=10000,
            kills_in_sequence=[
                {"tick": 10000}, {"tick": 12000},  # 31 seconds apart
            ],
        )
        rule = {"lead_in_s": 3.0, "tail_s": 3.0, "min_duration_s": 1.0, "max_duration_s": 10.0}
        start, anchor, end, _, _, _ = _compute_clip_window(hl, rule)
        duration_s = (end - start) / TICK_RATE
        assert duration_s <= 10.0

    def test_clamped_to_round_bounds(self):
        hl = _make_highlight(
            anchor_tick=8600,  # very close to round start
            round_start_tick=8000,
            freeze_end_tick=8500,
            round_end_tick=15000,
        )
        rule = {"lead_in_s": 5.0, "tail_s": 2.0, "min_duration_s": 3.0, "max_duration_s": 20.0}
        start, anchor, end, _, _, clamped = _compute_clip_window(hl, rule)
        # start should be clamped to freeze_end (8500), not go before it
        assert start >= 8500
        assert clamped is True

    def test_end_clamped_to_round_end(self):
        hl = _make_highlight(
            anchor_tick=14500,  # very close to round end
            round_end_tick=15000,
        )
        rule = {"lead_in_s": 2.0, "tail_s": 10.0, "min_duration_s": 1.0, "max_duration_s": 30.0}
        start, anchor, end, _, _, clamped = _compute_clip_window(hl, rule)
        assert end <= 15000
        assert clamped is True


class TestPlanClip:
    def test_basic_plan(self):
        hl = _make_highlight()
        plan = plan_clip(hl, demo_id="test_demo")
        assert isinstance(plan, ClipPlan)
        assert plan.demo_id == "test_demo"
        assert plan.player_name == "alice"
        assert plan.player_steamid64 == "76561198356633543"
        assert plan.start_tick > 0
        assert plan.anchor_tick == 10000
        assert plan.end_tick > plan.start_tick
        assert plan.duration_s > 0
        assert plan.pov_mode == "player_pov"
        assert plan.camera_mode == "first_person"

    def test_plan_id_deterministic(self):
        hl = _make_highlight()
        plan1 = plan_clip(hl, demo_id="test")
        plan2 = plan_clip(hl, demo_id="test")
        assert plan1.clip_plan_id == plan2.clip_plan_id

    def test_plan_id_different_for_different_demos(self):
        hl = _make_highlight()
        plan1 = plan_clip(hl, demo_id="demo1")
        plan2 = plan_clip(hl, demo_id="demo2")
        assert plan1.clip_plan_id != plan2.clip_plan_id

    def test_all_event_types_have_timing_rules(self):
        event_types = [
            "opening_kill", "multi_kill_2k", "multi_kill_3k", "multi_kill_4k",
            "multi_kill_ace", "clutch_win", "clutch_attempt", "trade_kill",
        ]
        for etype in event_types:
            assert etype in TIMING_RULES, f"Missing timing rule for {etype}"
            hl = _make_highlight(event_type=etype)
            plan = plan_clip(hl, demo_id="test")
            assert plan.duration_s > 0
            assert plan.start_tick < plan.anchor_tick
            assert plan.end_tick > plan.anchor_tick

    def test_identity_preserved(self):
        hl = _make_highlight(
            player_name="VixToix037",
            player_steamid64="76561198388732174",
        )
        plan = plan_clip(hl, demo_id="test")
        assert plan.player_name == "VixToix037"
        assert plan.player_steamid64 == "76561198388732174"

    def test_round_context_preserved(self):
        hl = _make_highlight(
            round_start_tick=8000,
            round_end_tick=15000,
            freeze_end_tick=8500,
        )
        plan = plan_clip(hl, demo_id="test")
        assert plan.round_start_tick == 8000
        assert plan.round_end_tick == 15000
        assert plan.freeze_end_tick == 8500


class TestPlanClips:
    def test_multiple_plans(self):
        highlights = [
            _make_highlight(round_number=1, anchor_tick=1000, priority=1),
            _make_highlight(round_number=2, anchor_tick=5000, priority=2),
            _make_highlight(round_number=3, anchor_tick=9000, priority=3),
        ]
        plans = plan_clips(highlights, demo_id="test")
        assert len(plans) == 3
        assert all(isinstance(p, dict) for p in plans)

    def test_max_clips_limit(self):
        highlights = [_make_highlight(round_number=i) for i in range(1, 20)]
        plans = plan_clips(highlights, demo_id="test", max_clips=5)
        assert len(plans) == 5

    def test_empty_highlights(self):
        plans = plan_clips([], demo_id="test")
        assert plans == []

    def test_each_plan_has_required_fields(self):
        highlights = [_make_highlight()]
        plans = plan_clips(highlights, demo_id="test")
        assert len(plans) == 1
        p = plans[0]
        required = [
            "clip_plan_id", "highlight_id", "demo_id",
            "player_name", "player_steamid64",
            "start_tick", "anchor_tick", "end_tick", "duration_s",
            "round_number", "round_start_tick", "round_end_tick",
            "event_type", "score", "priority", "confidence",
            "timing_rule", "lead_in_ticks", "tail_ticks",
            "pov_mode", "camera_mode",
        ]
        for field in required:
            assert field in p, f"Missing field: {field}"
