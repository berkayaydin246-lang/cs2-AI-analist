"""
render_modes.py
Render mode registry, in-game capture planning strategies, and validation.

Defines supported render modes and the architectural foundation for
multi-mode clip rendering:
  - tactical_2d_mp4   (deprecated legacy renderer, opt-in only)
  - cs2_ingame_capture (implemented via OBS WebSocket + CS2 playback)
"""

from __future__ import annotations

import os


# ── Render mode constants ─────────────────────────────────────────────────────

RENDER_MODE_TACTICAL_2D = "tactical_2d_mp4"
RENDER_MODE_INGAME_CAPTURE = "cs2_ingame_capture"
LEGACY_TACTICAL_RENDERER_ENABLED = (
    os.getenv("ENABLE_LEGACY_TACTICAL_RENDERER", "").strip().lower() in {"1", "true", "yes", "on"}
)

# Strict in-game MVP: keep scope intentionally narrow for reliability.
INGAME_MVP_HIGHLIGHT_TYPES: set[str] = {
    "opening_kill",
    "multi_kill",
    "clutch_attempt",
    "clutch_win",
}
INGAME_MVP_MULTI_KILL_MAX_DURATION_TICKS = 1536

# ── Mode registry ─────────────────────────────────────────────────────────────

SUPPORTED_RENDER_MODES: dict[str, dict] = {
    RENDER_MODE_TACTICAL_2D: {
        "label": "Tactical 2D (MP4)",
        "available": LEGACY_TACTICAL_RENDERER_ENABLED,
        "deprecated": True,
        "requires_game_client": False,
        "requires_demo_file": False,
        "requires_parsed_data": True,
        "output_format": "mp4",
        "default_preset": "standard_highlight",
        "supported_pov_modes": ["player_pov", "round_overview", "tactical_overview"],
        "description": "Deprecated legacy tactical radar renderer kept only as an explicit opt-in fallback.",
    },
    RENDER_MODE_INGAME_CAPTURE: {
        "label": "CS2 In-Game Capture",
        "available": True,
        "deprecated": False,
        "requires_game_client": True,
        "requires_demo_file": True,
        "requires_parsed_data": False,
        "output_format": "mp4",
        "default_preset": "standard_highlight",
        "supported_pov_modes": ["player_pov", "round_overview", "tactical_overview"],
        "description": "First-person or spectator capture via CS2 GOTV demo playback.",
    },
}

# ── Camera, observer, and HUD mode vocabularies ──────────────────────────────
# These define the allowed values for in-game capture settings.

CAMERA_MODES: dict[str, str] = {
    "player_pov": "Lock camera to target player's first-person view.",
    "freecam": "Free-flying noclip camera positioned by automation.",
    "observer_auto": "CS2 auto-director / spectator HUD camera.",
}

OBSERVER_MODES: dict[str, str] = {
    "first_person": "First-person view locked to the target player.",
    "third_person": "Third-person chase camera following the target.",
    "spec_follow": "Spectator follow-cam with smooth transitions.",
}

HUD_MODES: dict[str, str] = {
    "clean": "All HUD elements hidden (cl_drawhud 0).",
    "default": "Standard CS2 spectator HUD.",
    "cinematic": "Minimal HUD — crosshair and killfeed only.",
}

# ── In-game camera strategy per highlight type ────────────────────────────────
# Maps highlight_type → preferred camera/observer/hud for cs2_ingame_capture.
# Used during clip planning to pre-compute preferred in-game settings.

INGAME_CAMERA_STRATEGY: dict[str, dict[str, str]] = {
    "multi_kill": {
        "camera_mode": "player_pov",
        "observer_mode": "first_person",
        "hud_mode": "default",
    },
    "ace": {
        "camera_mode": "player_pov",
        "observer_mode": "first_person",
        "hud_mode": "default",
    },
    "opening_kill": {
        "camera_mode": "player_pov",
        "observer_mode": "first_person",
        "hud_mode": "default",
    },
    "trade_kill": {
        "camera_mode": "player_pov",
        "observer_mode": "first_person",
        "hud_mode": "default",
    },
    "clutch_attempt": {
        "camera_mode": "player_pov",
        "observer_mode": "first_person",
        "hud_mode": "default",
    },
    "clutch_win": {
        "camera_mode": "player_pov",
        "observer_mode": "first_person",
        "hud_mode": "default",
    },
    "flash_assist": {
        "camera_mode": "observer_auto",
        "observer_mode": "third_person",
        "hud_mode": "clean",
    },
    "grenade_damage_spike": {
        "camera_mode": "freecam",
        "observer_mode": "third_person",
        "hud_mode": "clean",
    },
    "bomb_pressure": {
        "camera_mode": "observer_auto",
        "observer_mode": "spec_follow",
        "hud_mode": "default",
    },
}

_DEFAULT_CAMERA_STRATEGY: dict[str, str] = {
    "camera_mode": "observer_auto",
    "observer_mode": "first_person",
    "hud_mode": "default",
}

# ── Capture profiles ──────────────────────────────────────────────────────────
# Preset bundles of capture/output settings for cs2_ingame_capture.

CAPTURE_PROFILES: dict[str, dict] = {
    "default": {
        "resolution": "1920x1080",
        "fps": 60,
        "bitrate": "8000k",
        "format": "mp4",
    },
    "high_quality": {
        "resolution": "2560x1440",
        "fps": 60,
        "bitrate": "15000k",
        "format": "mp4",
    },
    "cinematic": {
        "resolution": "1920x1080",
        "fps": 30,
        "bitrate": "12000k",
        "format": "mp4",
    },
    "compact": {
        "resolution": "1280x720",
        "fps": 30,
        "bitrate": "4000k",
        "format": "mp4",
    },
}


# ── Public helpers ────────────────────────────────────────────────────────────

def get_camera_strategy(highlight_type: str) -> dict[str, str]:
    """Return preferred in-game camera settings for a highlight type."""
    return dict(INGAME_CAMERA_STRATEGY.get(highlight_type, _DEFAULT_CAMERA_STRATEGY))


def get_capture_profile(profile_name: str) -> dict:
    """Return a copy of a named capture profile, falling back to 'default'."""
    return dict(CAPTURE_PROFILES.get(profile_name, CAPTURE_PROFILES["default"]))


def is_render_mode_known(render_mode: str) -> bool:
    """True if the render mode is in the registry (even if not yet available)."""
    return render_mode in SUPPORTED_RENDER_MODES


def is_render_mode_available(render_mode: str) -> bool:
    """True if the render mode is known AND currently implemented."""
    entry = SUPPORTED_RENDER_MODES.get(render_mode)
    return bool(entry and entry.get("available"))


def get_available_render_modes() -> list[str]:
    """Return list of currently implemented render modes."""
    return [mode for mode, info in SUPPORTED_RENDER_MODES.items() if info.get("available")]


def build_ingame_plan_metadata(highlight_type: str, pov_player: str | None = None) -> dict:
    """Build the ingame_capture section for a clip plan's planning_profile.

    Called during clip planning to pre-compute preferred in-game settings
    so render time doesn't need to re-derive them from the highlight type.
    """
    hl_type = str(highlight_type or "").strip().lower()
    mvp_supported = hl_type in INGAME_MVP_HIGHLIGHT_TYPES and bool(pov_player)
    mvp_reason = "mvp_supported" if mvp_supported else (
        "missing_pov_player_for_mvp" if hl_type in INGAME_MVP_HIGHLIGHT_TYPES else "outside_ingame_mvp_scope"
    )

    # For MVP reliability, in-game capture uses a fixed player POV policy.
    if mvp_supported:
        strategy = {
            "camera_mode": "player_pov",
            "observer_mode": "first_person",
            "hud_mode": "default",
        }
    else:
        strategy = get_camera_strategy(highlight_type)

    return {
        "camera_mode": strategy["camera_mode"],
        "observer_mode": strategy["observer_mode"],
        "hud_mode": strategy["hud_mode"],
        "capture_profile": "default",
        "requires_pov_player": strategy["camera_mode"] == "player_pov",
        "pov_player_available": bool(pov_player),
        "mvp_supported": mvp_supported,
        "mvp_reason": mvp_reason,
        "mvp_allowed_highlight_types": sorted(INGAME_MVP_HIGHLIGHT_TYPES),
    }


def validate_render_job(job: dict) -> list[str]:
    """Validate a render job dict. Returns a list of warning strings (empty = valid)."""
    warnings: list[str] = []
    render_mode = str(job.get("render_mode") or "")

    if not render_mode:
        warnings.append("render_mode is empty")
    elif not is_render_mode_known(render_mode):
        warnings.append(f"render_mode '{render_mode}' is not recognized")
    elif not is_render_mode_available(render_mode):
        warnings.append(f"render_mode '{render_mode}' is not yet available")

    # POV validation
    pov_mode = str(job.get("pov_mode") or "")
    pov_player = job.get("pov_player")
    if pov_mode == "player_pov" and not pov_player:
        warnings.append("pov_mode is 'player_pov' but pov_player is not set")

    # In-game specific validation
    if render_mode == RENDER_MODE_INGAME_CAPTURE:
        camera_mode = str(job.get("camera_mode") or "")
        if camera_mode and camera_mode not in CAMERA_MODES:
            warnings.append(f"camera_mode '{camera_mode}' is not recognized")
        observer_mode = str(job.get("observer_mode") or "")
        if observer_mode and observer_mode not in OBSERVER_MODES:
            warnings.append(f"observer_mode '{observer_mode}' is not recognized")
        hud_mode = str(job.get("hud_mode") or "")
        if hud_mode and hud_mode not in HUD_MODES:
            warnings.append(f"hud_mode '{hud_mode}' is not recognized")
        if pov_mode == "player_pov" and not job.get("target_player_steamid64"):
            warnings.append("in-game player_pov requires target_player_steamid64")

    # Tick window validation
    start = int(job.get("start_tick") or 0)
    anchor = int(job.get("anchor_tick") or 0)
    end = int(job.get("end_tick") or 0)
    if not (start <= anchor <= end):
        warnings.append(f"invalid tick window: start={start} anchor={anchor} end={end}")

    round_number = int(job.get("round_number") or 0)
    if round_number <= 0:
        warnings.append("round_number is missing or invalid")

    return warnings


def get_mode_info(render_mode: str) -> dict | None:
    """Return a copy of the full mode registry entry, or None if unknown."""
    entry = SUPPORTED_RENDER_MODES.get(render_mode)
    return dict(entry) if entry else None


def validate_plan_for_mode(clip_plan: dict, render_mode: str) -> dict:
    """Check whether a clip plan is compatible with a given render mode.

    Returns:
        {
            "compatible": bool,
            "warnings": list[str],
            "fallback_mode": str | None,   # suggested alternative if incompatible
        }
    """
    warnings: list[str] = []
    mode_entry = SUPPORTED_RENDER_MODES.get(render_mode)
    if not mode_entry:
        return {"compatible": False, "warnings": [f"unknown render_mode: {render_mode}"], "fallback_mode": None}

    if not mode_entry.get("available"):
        warnings.append(f"render_mode '{render_mode}' is not yet available")
        return {"compatible": False, "warnings": warnings, "fallback_mode": None}

    pov_mode = str(clip_plan.get("pov_mode") or "")
    supported_pov = mode_entry.get("supported_pov_modes", [])
    if pov_mode and supported_pov and pov_mode not in supported_pov:
        warnings.append(f"pov_mode '{pov_mode}' not supported by {render_mode}")

    # In-game capture needs a POV player for player_pov camera
    if render_mode == RENDER_MODE_INGAME_CAPTURE:
        source_type = str(((clip_plan.get("metadata") or {}).get("source_highlight_type") or "")).strip().lower()
        if source_type not in INGAME_MVP_HIGHLIGHT_TYPES:
            warnings.append(
                f"in-game MVP does not support highlight_type '{source_type or 'unknown'}'"
            )

        ingame_meta = ((clip_plan.get("metadata") or {}).get("planning_profile") or {}).get("ingame_capture") or {}
        if ingame_meta.get("requires_pov_player") and not ingame_meta.get("pov_player_available"):
            warnings.append("in-game player_pov requested but no POV player available")

        pov_mode = str(clip_plan.get("pov_mode") or "")
        if pov_mode != "player_pov":
            warnings.append(f"in-game MVP requires pov_mode 'player_pov' (got '{pov_mode or 'empty'}')")

        if not clip_plan.get("pov_player"):
            warnings.append("in-game MVP requires pov_player")

        camera_mode = str(((clip_plan.get("metadata") or {}).get("planning_profile") or {}).get("camera_mode") or "")
        observer_mode = str(((clip_plan.get("metadata") or {}).get("planning_profile") or {}).get("observer_mode") or "")
        hud_mode = str(((clip_plan.get("metadata") or {}).get("planning_profile") or {}).get("hud_mode") or "")
        if camera_mode and camera_mode != "player_pov":
            warnings.append("in-game MVP requires camera_mode 'player_pov'")
        if observer_mode and observer_mode != "first_person":
            warnings.append("in-game MVP requires observer_mode 'first_person'")
        if hud_mode and hud_mode != "default":
            warnings.append("in-game MVP requires hud_mode 'default'")

        if source_type == "multi_kill":
            start_tick = int(clip_plan.get("start_tick") or 0)
            end_tick = int(clip_plan.get("end_tick") or start_tick)
            if end_tick > start_tick:
                duration_ticks = end_tick - start_tick
                if duration_ticks > INGAME_MVP_MULTI_KILL_MAX_DURATION_TICKS:
                    warnings.append(
                        "in-game MVP supports only short multi_kill windows"
                    )

    compatible = not any("not supported" in w or "not yet available" in w for w in warnings)
    if render_mode == RENDER_MODE_INGAME_CAPTURE and warnings:
        compatible = False
    fallback = None
    return {"compatible": compatible, "warnings": warnings, "fallback_mode": fallback}


def get_render_capability_matrix() -> dict:
    """Return a combined view of all modes, presets, and capture profiles."""
    return {
        "modes": {
            mode: {
                "label": info["label"],
                "available": info["available"],
                "requires_game_client": info.get("requires_game_client", False),
                "requires_demo_file": info.get("requires_demo_file", False),
                "requires_parsed_data": info.get("requires_parsed_data", False),
                "output_format": info.get("output_format", "mp4"),
                "default_preset": info.get("default_preset", "standard_highlight"),
                "supported_pov_modes": info.get("supported_pov_modes", []),
                "deprecated": bool(info.get("deprecated")),
                "description": info.get("description", ""),
            }
            for mode, info in SUPPORTED_RENDER_MODES.items()
        },
        "capture_profiles": {
            name: dict(profile) for name, profile in CAPTURE_PROFILES.items()
        },
        "camera_modes": list(CAMERA_MODES.keys()),
        "observer_modes": list(OBSERVER_MODES.keys()),
        "hud_modes": list(HUD_MODES.keys()),
    }
