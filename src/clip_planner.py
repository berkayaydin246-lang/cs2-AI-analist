"""
clip_planner.py
Convert normalized highlight objects into future-ready clip plans.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

from src.render_modes import (
    INGAME_MVP_HIGHLIGHT_TYPES,
    RENDER_MODE_INGAME_CAPTURE,
    build_ingame_plan_metadata,
    validate_plan_for_mode,
    get_available_render_modes,
)
from src.render_presets import (
    apply_timing_profile,
    resolve_pov_strategy,
    resolve_render_preset,
)


CLIP_PLAN_SCHEMA_VERSION = 2
DEFAULT_PRE_ROLL_TICKS = 160
DEFAULT_POST_ROLL_TICKS = 192
DEFAULT_MIN_DURATION_TICKS = 384
DEFAULT_MAX_DURATION_TICKS = 1792

CLIP_RULES: dict[str, dict] = {
    "opening_kill": {
        "pre_roll": 160,
        "post_roll": 160,
        "min_duration": 384,
        "max_duration": 1024,
        "pov_mode": "player_pov",
        "clip_type": "highlight",
    },
    "trade_kill": {
        "pre_roll": 192,
        "post_roll": 160,
        "min_duration": 448,
        "max_duration": 1152,
        "pov_mode": "player_pov",
        "clip_type": "highlight",
    },
    "multi_kill": {
        "pre_roll": 160,
        "post_roll": 224,
        "min_duration": 512,
        "max_duration": 1728,
        "pov_mode": "player_pov",
        "clip_type": "highlight",
    },
    "ace": {
        "pre_roll": 192,
        "post_roll": 288,
        "min_duration": 768,
        "max_duration": 2304,
        "pov_mode": "player_pov",
        "clip_type": "highlight",
    },
    "clutch_attempt": {
        "pre_roll": 256,
        "post_roll": 224,
        "min_duration": 896,
        "max_duration": 2816,
        "pov_mode": "player_pov",
        "clip_type": "highlight",
    },
    "clutch_win": {
        "pre_roll": 320,
        "post_roll": 256,
        "min_duration": 1024,
        "max_duration": 3072,
        "pov_mode": "player_pov",
        "clip_type": "highlight",
    },
    "flash_assist": {
        "pre_roll": 224,
        "post_roll": 160,
        "min_duration": 512,
        "max_duration": 1408,
        "pov_mode": "tactical_overview",
        "clip_type": "utility",
    },
    "grenade_damage_spike": {
        "pre_roll": 224,
        "post_roll": 192,
        "min_duration": 512,
        "max_duration": 1536,
        "pov_mode": "tactical_overview",
        "clip_type": "utility",
    },
    "bomb_pressure": {
        "pre_roll": 256,
        "post_roll": 256,
        "min_duration": 768,
        "max_duration": 1792,
        "pov_mode": "round_overview",
        "clip_type": "tactical",
    },
}


def build_clip_plans(parsed_data: dict) -> dict:
    highlights = parsed_data.get("highlights", []) or []
    rounds = parsed_data.get("rounds", []) or []
    round_bounds = _build_round_bounds(rounds, highlights)
    player_identities = parsed_data.get("player_identities") or {}

    plans = [_plan_highlight_clip(highlight, round_bounds, player_identities=player_identities) for highlight in highlights]
    plans = [plan for plan in plans if plan]
    plans = _deduplicate_clip_plans(plans)
    plans = _finalize_clip_plans(plans)
    summary = _build_clip_plan_summary(plans)
    return {
        "schema_version": CLIP_PLAN_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_clip_plans": len(plans),
        "counts_by_highlight_type": summary["counts_by_highlight_type"],
        "counts_by_pov_mode": summary["counts_by_pov_mode"],
        "warnings": summary["warnings"],
        "clip_plans": plans,
    }


def _safe_int(value, default: int | None = 0) -> int | None:
    if value in ("", None):
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clean_text(value) -> str:
    return str(value or "").strip()


def _build_round_bounds(rounds: list[dict], highlights: list[dict]) -> dict[int, tuple[int, int]]:
    bounds: dict[int, tuple[int, int]] = {}
    for round_row in rounds or []:
        round_num = _safe_int(round_row.get("round_num"), None)
        if round_num is None:
            continue
        start_tick = _safe_int(round_row.get("start_tick"), None)
        end_tick = _safe_int(round_row.get("end_tick"), None)
        if start_tick is None:
            start_tick = _safe_int(round_row.get("start"), None)
        if end_tick is None:
            end_tick = _safe_int(round_row.get("end"), None)
        if start_tick is None or end_tick is None or end_tick < start_tick:
            continue
        bounds[int(round_num)] = (int(start_tick), int(end_tick))

    for highlight in highlights or []:
        round_num = _safe_int(highlight.get("round_number"), None)
        if round_num is None or round_num in bounds:
            continue
        start_tick = _safe_int(highlight.get("start_tick"), 0) or 0
        end_tick = _safe_int(highlight.get("end_tick"), start_tick) or start_tick
        bounds[int(round_num)] = (start_tick, max(start_tick, end_tick))
    return bounds


def _rule_for_highlight(highlight_type: str) -> dict:
    rule = dict(CLIP_RULES.get(highlight_type, {}))
    rule.setdefault("pre_roll", DEFAULT_PRE_ROLL_TICKS)
    rule.setdefault("post_roll", DEFAULT_POST_ROLL_TICKS)
    rule.setdefault("min_duration", DEFAULT_MIN_DURATION_TICKS)
    rule.setdefault("max_duration", DEFAULT_MAX_DURATION_TICKS)
    rule.setdefault("pov_mode", "auto")
    rule.setdefault("clip_type", "highlight")
    return rule


def _choose_pov(highlight: dict, rule: dict, preset_name: str = "standard_highlight") -> dict:
    """Context-aware POV selection using render_presets engine.

    Returns a dict with pov_mode, pov_player, camera_mode, observer_mode,
    hud_mode, and strategy_reason.
    """
    highlight_type = _clean_text(highlight.get("type"))
    primary_player = _clean_text(highlight.get("primary_player"))
    score = _safe_float(highlight.get("score"), 0.0)
    metadata = dict(highlight.get("metadata") or {})

    return resolve_pov_strategy(
        highlight_type=highlight_type,
        primary_player=primary_player,
        score=score,
        metadata=metadata,
        preset_name=preset_name,
    )


def _expand_window(
    start_tick: int,
    anchor_tick: int,
    end_tick: int,
    *,
    round_start: int,
    round_end: int,
    pre_roll: int,
    post_roll: int,
    min_duration: int,
    max_duration: int,
) -> tuple[int, int, dict]:
    initial_start = min(start_tick, anchor_tick, end_tick)
    initial_end = max(start_tick, anchor_tick, end_tick)
    planned_start = max(round_start, initial_start - pre_roll)
    planned_end = min(round_end, initial_end + post_roll)

    if planned_end < planned_start:
        planned_end = planned_start

    duration = planned_end - planned_start
    warnings: list[str] = []

    if duration < min_duration:
        needed = min_duration - duration
        extra_before = min(needed // 2, max(0, planned_start - round_start))
        extra_after = min(needed - extra_before, max(0, round_end - planned_end))
        if extra_after < needed - extra_before:
            extra_before = min(extra_before + (needed - extra_before - extra_after), max(0, planned_start - round_start))
        planned_start -= extra_before
        planned_end += extra_after
        duration = planned_end - planned_start
        if duration < min_duration:
            warnings.append("duration_below_min_after_clamp")

    if duration > max_duration:
        half = max_duration // 2
        planned_start = max(round_start, anchor_tick - half)
        planned_end = min(round_end, planned_start + max_duration)
        if planned_end - planned_start < max_duration:
            planned_start = max(round_start, planned_end - max_duration)
        duration = planned_end - planned_start
        if duration > max_duration:
            warnings.append("duration_above_max_after_clamp")

    anchor_tick = max(planned_start, min(anchor_tick, planned_end))
    return planned_start, planned_end, {
        "duration_ticks": max(0, planned_end - planned_start),
        "window_warnings": warnings,
        "window_clamped_to_round": planned_start == round_start or planned_end == round_end,
        "requested_pre_roll_ticks": pre_roll,
        "requested_post_roll_ticks": post_roll,
    }


def _plan_highlight_clip(highlight: dict, round_bounds: dict[int, tuple[int, int]], *, player_identities: dict | None = None) -> dict | None:
    source_highlight_id = _clean_text(highlight.get("highlight_id"))
    round_number = _safe_int(highlight.get("round_number"), None)
    if not source_highlight_id or round_number is None:
        return None

    highlight_type = _clean_text(highlight.get("type"))
    rule = _rule_for_highlight(highlight_type)
    highlight_start = _safe_int(highlight.get("start_tick"), 0) or 0
    highlight_anchor = _safe_int(highlight.get("anchor_tick"), highlight_start) or highlight_start
    highlight_end = _safe_int(highlight.get("end_tick"), highlight_anchor) or highlight_anchor
    score = _safe_float(highlight.get("score"), 0.0)
    hl_metadata = dict(highlight.get("metadata") or {})

    # Resolve render preset from highlight context
    if highlight_type in INGAME_MVP_HIGHLIGHT_TYPES:
        # Fixed preset for MVP in-game reliability.
        preset_name = "standard_highlight"
        preset_info = resolve_render_preset(
            highlight_type=highlight_type,
            score=score,
            metadata=hl_metadata,
            explicit_preset=preset_name,
        )
    else:
        preset_info = resolve_render_preset(
            highlight_type=highlight_type,
            score=score,
            metadata=hl_metadata,
        )
        preset_name = preset_info["preset_name"]

    # Apply timing profile (preset + context adjustments)
    timing = apply_timing_profile(
        highlight_type=highlight_type,
        base_pre_roll=int(rule["pre_roll"]),
        base_post_roll=int(rule["post_roll"]),
        base_min_duration=int(rule["min_duration"]),
        base_max_duration=int(rule["max_duration"]),
        score=score,
        metadata=hl_metadata,
        preset_name=preset_name,
    )

    round_start, round_end = round_bounds.get(round_number, (highlight_start, max(highlight_end, highlight_anchor)))
    if round_end < round_start:
        round_end = round_start

    final_start, final_end, window_meta = _expand_window(
        highlight_start,
        highlight_anchor,
        highlight_end,
        round_start=round_start,
        round_end=round_end,
        pre_roll=timing["pre_roll"],
        post_roll=timing["post_roll"],
        min_duration=timing["min_duration"],
        max_duration=timing["max_duration"],
    )

    # Resolve POV strategy with full context
    pov_info = _choose_pov(highlight, rule, preset_name=preset_name)
    pov_mode = pov_info["pov_mode"]
    pov_player = pov_info["pov_player"]

    # Resolve stable steamid64 identity for pov_player
    pov_player_steamid64: str | None = None
    if pov_player and player_identities:
        by_name = player_identities.get("by_name") or {}
        pov_player_steamid64 = by_name.get(pov_player)

    # Strict in-game MVP policy: for supported highlight types, force player POV.
    if highlight_type in INGAME_MVP_HIGHLIGHT_TYPES and pov_player:
        pov_mode = "player_pov"
        pov_info.update(
            {
                "pov_mode": "player_pov",
                "camera_mode": "player_pov",
                "observer_mode": "first_person",
                "hud_mode": "default",
                "strategy_reason": "mvp_fixed_player_pov",
            }
        )

    involved_players = [player for player in (highlight.get("involved_players") or []) if _clean_text(player)]
    clip_title = _clean_text(highlight.get("title")) or _clean_text(highlight.get("type")).replace("_", " ").title()

    clip_duration_ticks = max(0, final_end - final_start)
    ingame_meta = build_ingame_plan_metadata(highlight_type, pov_player)
    if highlight_type == "multi_kill" and clip_duration_ticks > 1536:
        ingame_meta["mvp_supported"] = False
        ingame_meta["mvp_reason"] = "multi_kill_window_too_long_for_mvp"

    metadata = {
        "source_highlight_type": highlight_type,
        "round_bounds": {"start_tick": round_start, "end_tick": round_end},
        "highlight_window": {
            "start_tick": highlight_start,
            "anchor_tick": highlight_anchor,
            "end_tick": highlight_end,
        },
        "planning_profile": {
            "clip_type": _clean_text(rule.get("clip_type")),
            "pov_mode": pov_mode,
            "pov_strategy": pov_info.get("strategy_reason", ""),
            "camera_mode": pov_info.get("camera_mode", "observer_auto"),
            "observer_mode": pov_info.get("observer_mode", "first_person"),
            "hud_mode": pov_info.get("hud_mode", "default"),
            "available_pov_modes": _available_pov_modes(highlight_type, pov_player),
            "ingame_capture": ingame_meta,
        },
        "render_preset": {
            "name": preset_name,
            "selection_reason": preset_info.get("selection_reason", ""),
            "quality_tier": preset_info.get("quality_tier", "medium"),
            "timing_reason": timing.get("timing_reason", ""),
            "capture_profile": preset_info.get("capture_profile", "default"),
        },
        "source_metadata": hl_metadata,
        **window_meta,
    }

    # Build the plan shell first so validate_plan_for_mode can inspect it
    plan = {
        "clip_plan_id": "",
        "source_highlight_id": source_highlight_id,
        "round_number": int(round_number),
        "clip_type": _clean_text(rule.get("clip_type")) or "highlight",
        "title": clip_title,
        "description": _clean_text(highlight.get("description")) or "Planned clip from detected highlight",
        "primary_player": _clean_text(highlight.get("primary_player")),
        "involved_players": sorted({player for player in involved_players}),
        "pov_mode": pov_mode,
        "pov_player": pov_player,
        "pov_player_steamid64": pov_player_steamid64,
        "start_tick": final_start,
        "anchor_tick": max(final_start, min(highlight_anchor, final_end)),
        "end_tick": final_end,
        "pre_roll_ticks": max(0, highlight_anchor - final_start),
        "post_roll_ticks": max(0, final_end - highlight_anchor),
        "tags": sorted({_clean_text(tag) for tag in (highlight.get("tags") or []) if _clean_text(tag)}),
        "score": round(max(0.0, min(_safe_float(highlight.get("score"), 0.0), 1.0)), 3),
        "status": "planned",
        "metadata": metadata,
    }

    # Determine which render modes this plan is compatible with
    plan["compatible_render_modes"] = _compute_compatible_modes(plan)

    return plan


def _compute_compatible_modes(plan: dict) -> list[str]:
    """Return the list of render modes this clip plan can be rendered with."""
    compatible = []
    for mode in get_available_render_modes():
        result = validate_plan_for_mode(plan, mode)
        if result["compatible"]:
            compatible.append(mode)
    return compatible


def _available_pov_modes(highlight_type: str, pov_player: str | None) -> list[str]:
    modes = ["round_overview", "tactical_overview"]
    if pov_player and highlight_type in {"multi_kill", "ace", "trade_kill", "opening_kill", "clutch_attempt", "clutch_win"}:
        modes.insert(0, "player_pov")
    elif highlight_type in {"flash_assist", "grenade_damage_spike", "bomb_pressure"}:
        modes.insert(0, "tactical_overview")
    return list(dict.fromkeys(modes))


def _deduplicate_clip_plans(plans: list[dict]) -> list[dict]:
    if not plans:
        return []

    sorted_plans = sorted(
        plans,
        key=lambda plan: (
            _safe_float(plan.get("score"), 0.0),
            _safe_int(plan.get("anchor_tick"), 0) or 0,
        ),
        reverse=True,
    )
    kept: list[dict] = []
    for plan in sorted_plans:
        duplicate = False
        for existing in kept:
            if _clean_text(existing.get("source_highlight_id")) == _clean_text(plan.get("source_highlight_id")):
                duplicate = True
                break
            if _safe_int(existing.get("round_number"), -1) != _safe_int(plan.get("round_number"), -1):
                continue
            if _clean_text(existing.get("primary_player")) != _clean_text(plan.get("primary_player")):
                continue
            existing_start = _safe_int(existing.get("start_tick"), 0) or 0
            existing_end = _safe_int(existing.get("end_tick"), existing_start) or existing_start
            start_tick = _safe_int(plan.get("start_tick"), 0) or 0
            end_tick = _safe_int(plan.get("end_tick"), start_tick) or start_tick
            overlap = max(0, min(existing_end, end_tick) - max(existing_start, start_tick))
            shorter = max(1, min(existing_end - existing_start, end_tick - start_tick))
            if shorter <= 0:
                continue
            if overlap / shorter >= 0.85 and _clean_text(existing.get("clip_type")) == _clean_text(plan.get("clip_type")):
                duplicate = True
                break
        if not duplicate:
            kept.append(plan)

    kept.sort(key=lambda plan: (_safe_int(plan.get("round_number"), 0) or 0, _safe_int(plan.get("start_tick"), 0) or 0))
    return kept


def _finalize_clip_plans(plans: list[dict]) -> list[dict]:
    counters: Counter[str] = Counter()
    finalized = []
    for plan in plans:
        source_id = _clean_text(plan.get("source_highlight_id")) or "clip"
        counters[source_id] += 1
        plan["clip_plan_id"] = f"clip_{source_id}_{counters[source_id]:03d}"
        finalized.append(plan)
    return finalized


def _build_clip_plan_summary(plans: list[dict]) -> dict:
    counts_by_type = Counter()
    counts_by_pov_mode = Counter()
    warnings: list[str] = []
    for plan in plans:
        source_type = _clean_text((plan.get("metadata") or {}).get("source_highlight_type"))
        counts_by_type[source_type or "unknown"] += 1
        counts_by_pov_mode[_clean_text(plan.get("pov_mode")) or "unknown"] += 1
        start_tick = _safe_int(plan.get("start_tick"), 0) or 0
        anchor_tick = _safe_int(plan.get("anchor_tick"), start_tick) or start_tick
        end_tick = _safe_int(plan.get("end_tick"), anchor_tick) or anchor_tick
        if not (start_tick <= anchor_tick <= end_tick):
            warnings.append(f"invalid_clip_window:{_clean_text(plan.get('clip_plan_id')) or _clean_text(plan.get('source_highlight_id'))}")
        if _clean_text(plan.get("pov_mode")) == "player_pov" and not _clean_text(plan.get("pov_player")):
            warnings.append(f"missing_pov_player:{_clean_text(plan.get('clip_plan_id')) or _clean_text(plan.get('source_highlight_id'))}")
        for warning in (plan.get("metadata") or {}).get("window_warnings", []):
            warnings.append(f"{warning}:{_clean_text(plan.get('source_highlight_id'))}")
    return {
        "counts_by_highlight_type": dict(sorted(counts_by_type.items())),
        "counts_by_pov_mode": dict(sorted(counts_by_pov_mode.items())),
        "warnings": warnings,
    }
