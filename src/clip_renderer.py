"""
clip_renderer.py
Active render dispatcher for the worker-driven clip pipeline.

The legacy tactical renderer has been removed from the active code path.
This module now only:
  - normalizes clip-plan render input
  - validates mode compatibility
  - dispatches to the in-game capture renderer
"""

from __future__ import annotations

from typing import Any

from src.render_modes import (
    RENDER_MODE_INGAME_CAPTURE,
    RENDER_MODE_TACTICAL_2D,
    get_available_render_modes,
    is_render_mode_known,
    validate_plan_for_mode,
    validate_render_job,
)
from src.render_presets import get_render_preset


RENDER_JOB_SCHEMA_VERSION = 2
DEFAULT_RENDER_MODE = RENDER_MODE_INGAME_CAPTURE
DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 720
DEFAULT_FPS = 20


def build_render_job_input(
    demo_id: str,
    clip_plan: dict[str, Any],
    render_mode: str = DEFAULT_RENDER_MODE,
    target_settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings = dict(target_settings or {})
    plan_meta = clip_plan.get("metadata") if isinstance(clip_plan.get("metadata"), dict) else {}
    preset_name = (
        settings.pop("render_preset", None)
        or ((plan_meta.get("render_preset") or {}) if isinstance(plan_meta.get("render_preset"), dict) else {}).get("name")
        or "standard_highlight"
    )
    preset = get_render_preset(preset_name)

    preset_w, preset_h = preset.get("resolution_2d", (DEFAULT_WIDTH, DEFAULT_HEIGHT))
    preset_fps = preset.get("fps_2d", DEFAULT_FPS)
    width = max(640, int(settings.get("width", preset_w)))
    height = max(360, int(settings.get("height", preset_h)))
    fps = max(8, min(60, int(settings.get("fps", preset_fps))))

    planning_profile = plan_meta.get("planning_profile") if isinstance(plan_meta.get("planning_profile"), dict) else {}
    ingame_meta = planning_profile.get("ingame_capture") if isinstance(planning_profile.get("ingame_capture"), dict) else {}

    job = {
        "schema_version": RENDER_JOB_SCHEMA_VERSION,
        "demo_id": demo_id,
        "clip_plan_id": str(clip_plan.get("clip_plan_id") or ""),
        "source_highlight_id": str(clip_plan.get("source_highlight_id") or ""),
        "round_number": int(clip_plan.get("round_number") or 0),
        "start_tick": int(clip_plan.get("start_tick") or 0),
        "anchor_tick": int(clip_plan.get("anchor_tick") or 0),
        "end_tick": int(clip_plan.get("end_tick") or 0),
        "render_mode": render_mode,
        "pov_mode": str(clip_plan.get("pov_mode") or "auto"),
        "pov_player": clip_plan.get("pov_player"),
        "render_preset": preset_name,
        "quality_profile": {
            "preset_name": preset_name,
            "quality_tier": preset.get("quality_tier", "medium"),
            "pov_strategy": planning_profile.get("pov_strategy", ""),
            "camera_mode": planning_profile.get("camera_mode", ""),
            "hud_mode": planning_profile.get("hud_mode", preset.get("hud_preference", "default")),
            "capture_profile": preset.get("capture_profile", "default"),
        },
        "target_settings": {
            "width": width,
            "height": height,
            "fps": fps,
            "codec": settings.get("codec", "mp4v"),
            "container": settings.get("container", "mp4"),
        },
        "ingame_capture_settings": {
            "camera_mode": settings.get("camera_mode") or ingame_meta.get("camera_mode"),
            "observer_mode": settings.get("observer_mode") or ingame_meta.get("observer_mode"),
            "hud_mode": settings.get("hud_mode") or ingame_meta.get("hud_mode"),
            "capture_profile": settings.get("capture_profile") or ingame_meta.get("capture_profile"),
            "target_player_steamid64": settings.get("target_player_steamid64") or clip_plan.get("pov_player_steamid64"),
        },
        "postprocess_settings": dict(settings.get("postprocess") or {}),
        "camera_mode": settings.get("camera_mode") or ingame_meta.get("camera_mode"),
        "observer_mode": settings.get("observer_mode") or ingame_meta.get("observer_mode"),
        "hud_mode": settings.get("hud_mode") or ingame_meta.get("hud_mode"),
        "capture_profile": settings.get("capture_profile") or ingame_meta.get("capture_profile"),
        "target_player_steamid64": settings.get("target_player_steamid64") or clip_plan.get("pov_player_steamid64"),
    }
    return job


def render_clip_plan(
    parsed_data: dict[str, Any],
    demo_id: str,
    clip_plan: dict[str, Any],
    output_root: str,
    *,
    render_mode: str = DEFAULT_RENDER_MODE,
    target_settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not is_render_mode_known(render_mode):
        raise ValueError(
            f"Unknown render_mode: '{render_mode}'. "
            f"Known modes: {', '.join(get_available_render_modes())}"
        )

    compat = validate_plan_for_mode(clip_plan, render_mode)
    if not compat.get("compatible", False):
        raise ValueError(
            "Clip plan is not compatible with the requested render mode: "
            + "; ".join(compat.get("warnings") or [render_mode])
        )

    job = build_render_job_input(
        demo_id=demo_id,
        clip_plan=clip_plan,
        render_mode=render_mode,
        target_settings=target_settings,
    )
    # validate_render_job returns warnings, not hard blockers.
    validate_render_job(job)

    if render_mode == RENDER_MODE_INGAME_CAPTURE:
        from src.ingame_capture import render_ingame_clip

        demo_path = str(parsed_data.get("demo_path") or "")
        return render_ingame_clip(
            demo_path=demo_path,
            demo_id=demo_id,
            clip_plan=clip_plan,
            output_root=output_root,
            target_settings=target_settings,
            camera_overrides=target_settings,
        )

    if render_mode == RENDER_MODE_TACTICAL_2D:
        raise NotImplementedError(
            "The legacy tactical_2d_mp4 renderer has been removed from the active pipeline. "
            "Use cs2_ingame_capture or add a separate migration-only renderer module."
        )

    raise NotImplementedError(
        f"Render mode '{render_mode}' is recognized but has no renderer implementation."
    )
