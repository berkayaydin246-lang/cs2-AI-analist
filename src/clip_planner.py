"""
clip_planner.py
Deterministic clip-window derivation from highlights.

For each highlight type, defines explicit timing rules to produce
a clip window with start_tick, anchor_tick, end_tick, and full metadata.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field, asdict
from typing import Any

TICK_RATE = 64


def _ticks(seconds: float) -> int:
    return int(seconds * TICK_RATE)


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


# ── Timing rules per highlight type ──────────────────────────────────────────
# Each rule defines lead_in_s (before anchor) and tail_s (after last relevant tick).
# These are explicit and inspectable.

TIMING_RULES: dict[str, dict[str, float]] = {
    "opening_kill": {
        "lead_in_s": 3.0,     # show approach before first contact
        "tail_s": 2.0,        # brief aftermath
        "min_duration_s": 4.0,
        "max_duration_s": 8.0,
    },
    "multi_kill_2k": {
        "lead_in_s": 3.0,
        "tail_s": 2.5,
        "min_duration_s": 5.0,
        "max_duration_s": 15.0,
    },
    "multi_kill_3k": {
        "lead_in_s": 3.5,
        "tail_s": 3.0,
        "min_duration_s": 6.0,
        "max_duration_s": 20.0,
    },
    "multi_kill_4k": {
        "lead_in_s": 4.0,
        "tail_s": 3.0,
        "min_duration_s": 7.0,
        "max_duration_s": 25.0,
    },
    "multi_kill_ace": {
        "lead_in_s": 4.0,
        "tail_s": 3.5,
        "min_duration_s": 8.0,
        "max_duration_s": 30.0,
    },
    "clutch_win": {
        "lead_in_s": 2.0,     # start near clutch onset
        "tail_s": 3.0,        # show resolution
        "min_duration_s": 6.0,
        "max_duration_s": 30.0,
    },
    "clutch_attempt": {
        "lead_in_s": 2.0,
        "tail_s": 2.0,
        "min_duration_s": 5.0,
        "max_duration_s": 25.0,
    },
    "trade_kill": {
        "lead_in_s": 2.5,     # show teammate's death
        "tail_s": 2.0,
        "min_duration_s": 4.0,
        "max_duration_s": 10.0,
    },
    "entry_kill": {
        "lead_in_s": 3.0,
        "tail_s": 2.0,
        "min_duration_s": 4.0,
        "max_duration_s": 8.0,
    },
}

DEFAULT_TIMING = {
    "lead_in_s": 3.0,
    "tail_s": 2.5,
    "min_duration_s": 5.0,
    "max_duration_s": 15.0,
}


# ── Clip plan data model ─────────────────────────────────────────────────────

@dataclass
class ClipPlan:
    clip_plan_id: str
    highlight_id: str
    demo_id: str

    # Player identity
    player_name: str
    player_steamid64: str

    # Timing (ticks)
    start_tick: int
    anchor_tick: int
    end_tick: int
    duration_s: float

    # Round context
    round_number: int
    round_start_tick: int
    round_end_tick: int
    freeze_end_tick: int

    # Event metadata
    event_type: str
    score: float
    priority: int
    confidence: float
    selection_reason: str

    # Derivation metadata
    timing_rule: str
    lead_in_ticks: int
    tail_ticks: int
    clamped: bool = False     # True if window was clamped to round bounds

    # Camera
    pov_mode: str = "player_pov"
    camera_mode: str = "first_person"

    created_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ── Clip window derivation ───────────────────────────────────────────────────

def _compute_clip_window(
    highlight: dict,
    timing_rule: dict[str, float],
) -> tuple[int, int, int, int, int, bool]:
    """Compute start_tick, anchor_tick, end_tick, lead_in_ticks, tail_ticks, clamped."""
    anchor_tick = _safe_int(highlight.get("anchor_tick"))
    round_start = _safe_int(highlight.get("round_start_tick"))
    round_end = _safe_int(highlight.get("round_end_tick"))
    freeze_end = _safe_int(highlight.get("freeze_end_tick"))

    # Determine the last relevant tick (last kill in sequence, or anchor)
    kills = highlight.get("kills_in_sequence", [])
    if kills:
        last_tick = max(_safe_int(k.get("tick")) for k in kills)
    else:
        last_tick = anchor_tick

    lead_in_s = timing_rule.get("lead_in_s", 3.0)
    tail_s = timing_rule.get("tail_s", 2.5)
    min_duration_s = timing_rule.get("min_duration_s", 5.0)
    max_duration_s = timing_rule.get("max_duration_s", 15.0)

    lead_in_ticks = _ticks(lead_in_s)
    tail_ticks = _ticks(tail_s)

    start_tick = anchor_tick - lead_in_ticks
    end_tick = last_tick + tail_ticks

    # Ensure minimum duration
    duration_ticks = end_tick - start_tick
    min_ticks = _ticks(min_duration_s)
    if duration_ticks < min_ticks:
        # Extend equally on both sides
        deficit = min_ticks - duration_ticks
        start_tick -= deficit // 2
        end_tick += deficit - deficit // 2

    # Enforce maximum duration
    max_ticks = _ticks(max_duration_s)
    if end_tick - start_tick > max_ticks:
        end_tick = start_tick + max_ticks

    # Clamp to round bounds if available
    clamped = False
    effective_start = freeze_end if freeze_end > 0 else round_start
    if effective_start > 0 and start_tick < effective_start:
        start_tick = effective_start
        clamped = True
    if round_end > 0 and end_tick > round_end:
        end_tick = round_end
        clamped = True

    # Ensure start < end
    if end_tick <= start_tick:
        end_tick = start_tick + _ticks(5.0)

    return start_tick, anchor_tick, end_tick, lead_in_ticks, tail_ticks, clamped


def plan_clip(
    highlight: dict,
    demo_id: str,
) -> ClipPlan:
    """Create a deterministic clip plan from a highlight.

    Args:
        highlight: A highlight dict from highlights.extract_highlights()
        demo_id: The demo session ID

    Returns:
        A ClipPlan with fully resolved timing and identity.
    """
    event_type = str(highlight.get("event_type", "unknown"))
    timing_rule = TIMING_RULES.get(event_type, DEFAULT_TIMING)

    start_tick, anchor_tick, end_tick, lead_in_ticks, tail_ticks, clamped = \
        _compute_clip_window(highlight, timing_rule)

    duration_s = round((end_tick - start_tick) / TICK_RATE, 2)

    # Generate deterministic clip plan ID
    raw_id = f"{demo_id}_{highlight.get('highlight_id', '')}_{start_tick}_{end_tick}"
    short_hash = hashlib.md5(raw_id.encode()).hexdigest()[:8]
    clip_plan_id = f"cp_{event_type}_r{highlight.get('round_number', 0)}_{short_hash}"

    return ClipPlan(
        clip_plan_id=clip_plan_id,
        highlight_id=highlight.get("highlight_id", ""),
        demo_id=demo_id,
        player_name=highlight.get("player_name", ""),
        player_steamid64=highlight.get("player_steamid64", ""),
        start_tick=start_tick,
        anchor_tick=anchor_tick,
        end_tick=end_tick,
        duration_s=duration_s,
        round_number=_safe_int(highlight.get("round_number")),
        round_start_tick=_safe_int(highlight.get("round_start_tick")),
        round_end_tick=_safe_int(highlight.get("round_end_tick")),
        freeze_end_tick=_safe_int(highlight.get("freeze_end_tick")),
        event_type=event_type,
        score=float(highlight.get("score", 0)),
        priority=_safe_int(highlight.get("priority")),
        confidence=float(highlight.get("confidence", 0)),
        selection_reason=highlight.get("selection_reason", ""),
        timing_rule=event_type,
        lead_in_ticks=lead_in_ticks,
        tail_ticks=tail_ticks,
        clamped=clamped,
        pov_mode="player_pov",
        camera_mode="first_person",
        created_at=time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
    )


def plan_clips(
    highlights: list[dict],
    demo_id: str,
    max_clips: int = 10,
) -> list[dict]:
    """Create clip plans for a list of highlights.

    Args:
        highlights: List of highlight dicts from extract_highlights()
        demo_id: The demo session ID
        max_clips: Maximum number of clips to plan

    Returns:
        List of clip plan dicts, sorted by priority.
    """
    plans = []
    for hl in highlights[:max_clips]:
        plan = plan_clip(hl, demo_id)
        plans.append(plan.to_dict())
    return plans
