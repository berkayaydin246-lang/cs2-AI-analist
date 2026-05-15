"""Tests for render_recipe.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src.cs2_config import load_config
from src.render_queue import RenderJob
from src.render_recipe import RenderRecipe, RenderRecipeError


def _make_clip_plan():
    return {
        "clip_plan_id": "cp_test",
        "highlight_id": "hl_test",
        "player_name": "alice",
        "player_steamid64": "76561198356633543",
        "round_number": 3,
        "start_tick": 12000,
        "anchor_tick": 12100,
        "end_tick": 12800,
        "round_start_tick": 11000,
        "round_end_tick": 15000,
        "freeze_end_tick": 11100,
        "lead_in_ticks": 128,
        "tail_ticks": 96,
        "pov_mode": "player_pov",
        "camera_mode": "first_person",
        "event_type": "multi_kill_3k",
        "selection_reason": "test reason",
    }


def test_render_recipe_from_clip_plan_uses_clip_identity_and_timing(tmp_path):
    config = load_config()
    demo = tmp_path / "test.dem"
    demo.write_text("x")

    recipe = RenderRecipe.from_clip_plan(
        _make_clip_plan(),
        job_id="rj_test",
        demo_path=str(demo),
        output_dir=str(tmp_path / "out"),
        config=config,
    )

    assert recipe.job_id == "rj_test"
    assert recipe.player_name == "alice"
    assert recipe.player_steamid64 == "76561198356633543"
    assert recipe.pre_roll_ticks == 128
    assert recipe.post_roll_ticks == 96
    assert recipe.seek_tick == 12000 - 128
    assert recipe.stop_tick == 12800 + 96


def test_render_recipe_from_render_job_respects_job_overrides(tmp_path):
    config = load_config()
    demo = tmp_path / "test.dem"
    demo.write_text("x")
    job = RenderJob(
        job_id="rj_test",
        clip_id="cp_test",
        demo_id="demo1",
        demo_path=str(demo),
        clip_plan=_make_clip_plan(),
        player_name="alice",
        player_steamid64="76561198356633543",
        start_tick=12000,
        anchor_tick=12100,
        end_tick=12800,
        output_dir=str(tmp_path / "out"),
        output_name="custom_name",
        fps=120,
        width=1280,
        height=720,
        video_container="mp4",
        encode_preset="fast",
    )

    recipe = RenderRecipe.from_render_job(job, config)

    assert recipe.output_name == "custom_name"
    assert recipe.fps == 120
    assert recipe.width == 1280
    assert recipe.height == 720
    assert recipe.encode_preset == "fast"


def test_render_recipe_validation_rejects_missing_player(tmp_path):
    demo = tmp_path / "test.dem"
    demo.write_text("x")
    recipe = RenderRecipe(
        job_id="rj_test",
        demo_path=str(demo),
        output_dir=str(tmp_path / "out"),
        output_name="clip",
        player_name="",
        start_tick=100,
        end_tick=200,
    )
    with pytest.raises(RenderRecipeError, match="player_name"):
        recipe.validate()
