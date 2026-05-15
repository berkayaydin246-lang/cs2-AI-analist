"""Tests for hlae_commands.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.hlae_commands import (
    build_cs2_command_line,
    build_mirv_streams_setup_commands,
    build_pov_commands,
    steamid64_to_accountid,
)
from src.render_recipe import RenderRecipe


def _make_recipe(tmp_path):
    demo = tmp_path / "test.dem"
    demo.write_text("x")
    return RenderRecipe(
        job_id="rj_test",
        demo_path=str(demo),
        output_dir=str(tmp_path / "out"),
        output_name="clip_test",
        player_name="alice",
        player_steamid64="76561198356633543",
        start_tick=12000,
        anchor_tick=12100,
        end_tick=12800,
        pre_roll_ticks=128,
        post_roll_ticks=96,
        fps=60,
        width=1920,
        height=1080,
    )


def test_steamid64_to_accountid():
    assert steamid64_to_accountid("76561198356633543") == "396367815"


def test_build_pov_commands_prefers_accountid(tmp_path):
    recipe = _make_recipe(tmp_path)
    commands = build_pov_commands(recipe)
    assert any("spec_player_by_accountid 396367815" in cmd for cmd in commands)
    assert any("spec_lock_to_accountid 396367815" in cmd for cmd in commands)
    assert not any("spec_player_by_name" in cmd for cmd in commands)


def test_build_pov_commands_falls_back_to_name(tmp_path):
    recipe = _make_recipe(tmp_path)
    recipe.player_steamid64 = ""
    commands = build_pov_commands(recipe)
    assert any('spec_player_by_name "alice"' in cmd for cmd in commands)


def test_build_cs2_command_line_includes_netcon_and_resolution(tmp_path):
    recipe = _make_recipe(tmp_path)
    cmdline = build_cs2_command_line(recipe, netcon_port=2121, extra_launch_options="-novid")
    assert "-netconport 2121" in cmdline
    assert "-w 1920" in cmdline
    assert "-h 1080" in cmdline
    assert "-afxFixNetCon" in cmdline
    assert "-afxDisableSteamStorage" in cmdline
    assert cmdline.count("-novid") == 1


def test_build_mirv_streams_setup_commands(tmp_path):
    recipe = _make_recipe(tmp_path)
    commands = build_mirv_streams_setup_commands(recipe, tmp_path / "raw")
    assert any("mirv_streams record screen enabled 1" in cmd for cmd in commands)
    assert any("mirv_streams record fps 60" in cmd for cmd in commands)
    record_name_cmd = next(cmd for cmd in commands if "mirv_streams record name" in cmd)
    assert recipe.raw_name_prefix in record_name_cmd
    assert "host_framerate" not in " ".join(commands)
