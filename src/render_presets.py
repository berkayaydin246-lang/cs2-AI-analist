"""
render_presets.py
Render quality presets, intelligent POV strategy, and timing profiles.

Provides:
  - RENDER_PRESETS: named bundles of quality/timing/camera settings
  - resolve_render_preset: picks the best preset for a highlight
  - resolve_pov_strategy: context-aware POV/camera decisions
  - apply_timing_profile: adjusts pre/post roll based on preset + context
"""

from __future__ import annotations


# ── Render presets ────────────────────────────────────────────────────────────
# Each preset governs timing behaviour, resolution/fps for 2D rendering,
# preferred capture profile for in-game capture, HUD preference, and a
# human-readable label.

RENDER_PRESETS: dict[str, dict] = {
    "quick_review": {
        "label": "Quick Review",
        "description": "Fast compact render for scanning many clips",
        "timing_scale": 0.70,
        "pre_roll_bonus": 0,
        "post_roll_bonus": 0,
        "resolution_2d": (960, 540),
        "fps_2d": 16,
        "capture_profile": "default",
        "hud_preference": "default",
        "quality_tier": "low",
    },
    "standard_highlight": {
        "label": "Standard Highlight",
        "description": "Balanced quality for typical highlights",
        "timing_scale": 1.0,
        "pre_roll_bonus": 0,
        "post_roll_bonus": 0,
        "resolution_2d": (1280, 720),
        "fps_2d": 24,
        "capture_profile": "default",
        "hud_preference": "default",
        "quality_tier": "medium",
    },
    "cinematic": {
        "label": "Cinematic",
        "description": "Higher quality with wider context for showcase clips",
        "timing_scale": 1.35,
        "pre_roll_bonus": 64,
        "post_roll_bonus": 96,
        "resolution_2d": (1920, 1080),
        "fps_2d": 30,
        "capture_profile": "high_quality",
        "hud_preference": "cinematic",
        "quality_tier": "high",
    },
    "tactical_focus": {
        "label": "Tactical Focus",
        "description": "Tactical/overhead emphasis for utility and setups",
        "timing_scale": 1.15,
        "pre_roll_bonus": 48,
        "post_roll_bonus": 32,
        "resolution_2d": (1280, 720),
        "fps_2d": 24,
        "capture_profile": "default",
        "hud_preference": "clean",
        "quality_tier": "medium",
    },
}

DEFAULT_PRESET = "standard_highlight"

PRESET_NAMES = list(RENDER_PRESETS.keys())


def get_render_preset(name: str) -> dict:
    """Return a copy of a named preset, falling back to standard_highlight."""
    return dict(RENDER_PRESETS.get(name, RENDER_PRESETS[DEFAULT_PRESET]))


def validate_capture_profile_ref(preset_name: str) -> list[str]:
    """Validate that a preset's capture_profile references a known profile.

    Returns list of warning strings (empty = valid).
    """
    from src.render_modes import CAPTURE_PROFILES
    preset = RENDER_PRESETS.get(preset_name)
    if not preset:
        return [f"unknown preset: {preset_name}"]
    cap_profile = preset.get("capture_profile", "default")
    if cap_profile not in CAPTURE_PROFILES:
        return [f"preset '{preset_name}' references unknown capture_profile '{cap_profile}'"]
    return []


# ── Highlight → preset selection ──────────────────────────────────────────────
# Picks a default render preset based on highlight type, score, and context.

_HIGHLIGHT_PRESET_RULES: list[dict] = [
    # High-impact aces always get cinematic
    {"types": {"ace"}, "min_score": 0.0, "preset": "cinematic"},
    # High-score clutch wins
    {"types": {"clutch_win"}, "min_score": 0.75, "preset": "cinematic"},
    # Standard clutch
    {"types": {"clutch_win", "clutch_attempt"}, "min_score": 0.0, "preset": "standard_highlight"},
    # High-score multi-kills (4k)
    {"types": {"multi_kill"}, "min_score": 0.85, "preset": "cinematic"},
    # Standard multi-kills
    {"types": {"multi_kill"}, "min_score": 0.0, "preset": "standard_highlight"},
    # Opening / trade kills
    {"types": {"opening_kill", "trade_kill"}, "min_score": 0.0, "preset": "standard_highlight"},
    # Utility highlights → tactical
    {"types": {"flash_assist", "grenade_damage_spike"}, "min_score": 0.0, "preset": "tactical_focus"},
    # Bomb pressure → tactical
    {"types": {"bomb_pressure"}, "min_score": 0.0, "preset": "tactical_focus"},
]


def resolve_render_preset(
    highlight_type: str,
    score: float = 0.0,
    metadata: dict | None = None,
    *,
    explicit_preset: str | None = None,
) -> dict:
    """Choose the best render preset for a highlight.

    If *explicit_preset* is given and valid, use it directly.
    Otherwise derive from highlight type + score + context.

    Returns a dict with the preset settings plus a ``preset_name`` key
    and a ``selection_reason`` key for diagnostics.
    """
    if explicit_preset and explicit_preset in RENDER_PRESETS:
        result = get_render_preset(explicit_preset)
        result["preset_name"] = explicit_preset
        result["selection_reason"] = "explicit"
        return result

    meta = metadata or {}
    hl_type = (highlight_type or "").strip().lower()

    for rule in _HIGHLIGHT_PRESET_RULES:
        if hl_type in rule["types"] and score >= rule["min_score"]:
            name = rule["preset"]
            result = get_render_preset(name)
            result["preset_name"] = name
            result["selection_reason"] = f"rule:{hl_type}/score>={rule['min_score']}"
            return result

    # Low-score fallback → quick_review
    if score < 0.45:
        result = get_render_preset("quick_review")
        result["preset_name"] = "quick_review"
        result["selection_reason"] = "low_score_fallback"
        return result

    result = get_render_preset(DEFAULT_PRESET)
    result["preset_name"] = DEFAULT_PRESET
    result["selection_reason"] = "default"
    return result


# ── Intelligent POV strategy ──────────────────────────────────────────────────
# Context-aware camera/POV decisions that consider highlight type, score,
# multi-kill count, clutch situation, etc.

_POV_STRATEGY_CACHE: dict[str, dict] = {}


def resolve_pov_strategy(
    highlight_type: str,
    primary_player: str | None,
    score: float = 0.0,
    metadata: dict | None = None,
    preset_name: str = "standard_highlight",
) -> dict:
    """Determine the best POV/camera approach for a highlight.

    Returns a dict with:
      pov_mode, pov_player, camera_mode, observer_mode, hud_mode,
      strategy_reason (diagnostic string)
    """
    meta = metadata or {}
    hl_type = (highlight_type or "").strip().lower()
    player = (primary_player or "").strip() or None
    preset = get_render_preset(preset_name)

    # Start with defaults
    result = {
        "pov_mode": "round_overview",
        "pov_player": None,
        "camera_mode": "observer_auto",
        "observer_mode": "first_person",
        "hud_mode": preset.get("hud_preference", "default"),
        "strategy_reason": "default",
    }

    if hl_type == "ace":
        # Aces: always player POV, cinematic HUD for high-score
        result.update(
            pov_mode="player_pov",
            pov_player=player,
            camera_mode="player_pov",
            observer_mode="first_person",
            hud_mode="cinematic" if score >= 0.9 else "default",
            strategy_reason="ace_player_pov",
        )

    elif hl_type == "multi_kill":
        kill_count = int(meta.get("kill_count", 2))
        if kill_count >= 4 and player:
            # 4k: player POV, cinematic feel
            result.update(
                pov_mode="player_pov",
                pov_player=player,
                camera_mode="player_pov",
                observer_mode="first_person",
                hud_mode="cinematic",
                strategy_reason=f"multi_kill_{kill_count}k_cinematic",
            )
        elif player:
            # 2k-3k: player POV, standard
            result.update(
                pov_mode="player_pov",
                pov_player=player,
                camera_mode="player_pov",
                observer_mode="first_person",
                hud_mode="default",
                strategy_reason=f"multi_kill_{kill_count}k_standard",
            )
        else:
            result.update(
                pov_mode="round_overview",
                camera_mode="observer_auto",
                strategy_reason="multi_kill_no_player",
            )

    elif hl_type == "opening_kill":
        # Opening kills: tight attacker POV
        if player:
            result.update(
                pov_mode="player_pov",
                pov_player=player,
                camera_mode="player_pov",
                observer_mode="first_person",
                hud_mode="default",
                strategy_reason="opening_attacker_pov",
            )

    elif hl_type == "trade_kill":
        # Trade kills: trader POV to show the response
        if player:
            result.update(
                pov_mode="player_pov",
                pov_player=player,
                camera_mode="player_pov",
                observer_mode="first_person",
                hud_mode="default",
                strategy_reason="trade_trader_pov",
            )

    elif hl_type in ("clutch_win", "clutch_attempt"):
        vs_count = int(meta.get("vs", 2))
        if player:
            # 1v3+ clutches: player POV with cinematic HUD for drama
            if vs_count >= 3 and score >= 0.7:
                result.update(
                    pov_mode="player_pov",
                    pov_player=player,
                    camera_mode="player_pov",
                    observer_mode="first_person",
                    hud_mode="cinematic",
                    strategy_reason=f"clutch_1v{vs_count}_cinematic",
                )
            else:
                result.update(
                    pov_mode="player_pov",
                    pov_player=player,
                    camera_mode="player_pov",
                    observer_mode="first_person",
                    hud_mode="default",
                    strategy_reason=f"clutch_1v{vs_count}_standard",
                )

    elif hl_type == "flash_assist":
        # Flash assists: show the flasher's perspective if high-impact,
        # otherwise tactical overview to show the geometry
        if score >= 0.65 and player:
            result.update(
                pov_mode="player_pov",
                pov_player=player,
                camera_mode="player_pov",
                observer_mode="first_person",
                hud_mode="clean",
                strategy_reason="flash_assist_flasher_pov",
            )
        else:
            result.update(
                pov_mode="tactical_overview",
                camera_mode="observer_auto",
                observer_mode="third_person",
                hud_mode="clean",
                strategy_reason="flash_assist_tactical",
            )

    elif hl_type == "grenade_damage_spike":
        # Grenade damage: freecam / tactical to show the impact area
        if score >= 0.75:
            result.update(
                pov_mode="tactical_overview",
                camera_mode="freecam",
                observer_mode="third_person",
                hud_mode="clean",
                strategy_reason="grenade_high_impact_freecam",
            )
        else:
            result.update(
                pov_mode="tactical_overview",
                camera_mode="observer_auto",
                observer_mode="third_person",
                hud_mode="clean",
                strategy_reason="grenade_tactical",
            )

    elif hl_type == "bomb_pressure":
        # Bomb pressure: round overview / observer
        result.update(
            pov_mode="round_overview",
            camera_mode="observer_auto",
            observer_mode="spec_follow",
            hud_mode="default",
            strategy_reason="bomb_pressure_overview",
        )

    return result


# ── Timing profile adjustments ────────────────────────────────────────────────
# Refine the base CLIP_RULES timing with preset-aware and context-aware
# adjustments for better clip framing.

# Extra timing per highlight type — applied *on top of* CLIP_RULES base values.
# Values are ticks (1 tick ≈ 1/64s).
_TIMING_ADJUSTMENTS: dict[str, dict] = {
    "ace": {
        # Aces: generous post-roll to include aftermath / celebration
        "pre_roll_add": 32,
        "post_roll_add": 96,
        "min_duration_add": 128,
    },
    "multi_kill": {
        # Multi-kills: slight pre-roll boost for context
        "pre_roll_add": 16,
        "post_roll_add": 48,
    },
    "clutch_win": {
        # Clutch wins: significantly more buildup
        "pre_roll_add": 96,
        "post_roll_add": 64,
        "min_duration_add": 192,
    },
    "clutch_attempt": {
        # Clutch attempts: more buildup but slightly less post-roll
        "pre_roll_add": 64,
        "post_roll_add": 32,
        "min_duration_add": 128,
    },
    "opening_kill": {
        # Opening kills: more setup before the duel
        "pre_roll_add": 48,
        "post_roll_add": 16,
    },
    "trade_kill": {
        # Trade kills: include the initial kill context
        "pre_roll_add": 48,
        "post_roll_add": 16,
    },
    "flash_assist": {
        # Flash assists: pre-roll to see the throw setup
        "pre_roll_add": 64,
        "post_roll_add": 16,
    },
    "grenade_damage_spike": {
        # Grenade: show the throw setup
        "pre_roll_add": 48,
        "post_roll_add": 32,
    },
    "bomb_pressure": {
        # Bomb pressure: wider context
        "pre_roll_add": 32,
        "post_roll_add": 64,
    },
}


def apply_timing_profile(
    highlight_type: str,
    base_pre_roll: int,
    base_post_roll: int,
    base_min_duration: int,
    base_max_duration: int,
    score: float = 0.0,
    metadata: dict | None = None,
    preset_name: str = "standard_highlight",
) -> dict:
    """Apply preset + context adjustments to base timing values.

    Returns a dict with final pre_roll, post_roll, min_duration, max_duration,
    plus a timing_reason diagnostic string.
    """
    preset = get_render_preset(preset_name)
    scale = float(preset.get("timing_scale", 1.0))
    pre_bonus = int(preset.get("pre_roll_bonus", 0))
    post_bonus = int(preset.get("post_roll_bonus", 0))

    adj = _TIMING_ADJUSTMENTS.get((highlight_type or "").strip().lower(), {})
    pre_add = int(adj.get("pre_roll_add", 0))
    post_add = int(adj.get("post_roll_add", 0))
    min_dur_add = int(adj.get("min_duration_add", 0))

    # Score-based boost: high-score highlights get up to 15% more timing
    score_boost = 1.0 + max(0.0, min(0.15, (score - 0.6) * 0.375))

    pre_roll = int((base_pre_roll + pre_add + pre_bonus) * scale * score_boost)
    post_roll = int((base_post_roll + post_add + post_bonus) * scale * score_boost)
    min_duration = int((base_min_duration + min_dur_add) * scale)
    max_duration = int(base_max_duration * scale * score_boost)

    # Guard rails
    pre_roll = max(64, pre_roll)
    post_roll = max(64, post_roll)
    min_duration = max(256, min_duration)
    max_duration = max(min_duration + 128, max_duration)

    reason_parts = [f"preset={preset_name}", f"scale={scale}"]
    if pre_add or post_add:
        reason_parts.append(f"type_adj=+{pre_add}/+{post_add}")
    if score_boost > 1.01:
        reason_parts.append(f"score_boost={score_boost:.2f}")

    return {
        "pre_roll": pre_roll,
        "post_roll": post_roll,
        "min_duration": min_duration,
        "max_duration": max_duration,
        "timing_reason": "; ".join(reason_parts),
    }
