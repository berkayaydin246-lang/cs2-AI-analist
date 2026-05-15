"""
hlae_commands.py
Centralized HLAE / CS2 command templates and sequencing for MVP POV renders.

The exact command set can be tuned later in one place without rewriting the
worker or renderer orchestration.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from src.render_recipe import RenderRecipe

STEAMID64_BASE = 76561197960265728


def steamid64_to_accountid(steamid64: str) -> str:
    value = str(steamid64 or "").strip()
    if not value.isdigit():
        return ""
    numeric = int(value)
    if numeric <= 0:
        return ""
    return str(numeric & 0xFFFFFFFF)


def build_cs2_command_line(recipe: RenderRecipe, netcon_port: int, extra_launch_options: str = "") -> str:
    extra_tokens = extra_launch_options.split() if extra_launch_options else []
    args = [
        "-steam",
        "-insecure",
        "-afxDisableSteamStorage",
        "-windowed",
        "-noborder",
        "-afxFixNetCon",
        "-usercon",
        "-netconport", str(netcon_port),
        "-w", str(recipe.width),
        "-h", str(recipe.height),
        "-novid",
    ]
    if extra_tokens:
        if "-novid" in extra_tokens:
            extra_tokens = [tok for tok in extra_tokens if tok != "-novid"]
        args.extend(extra_tokens)
    return subprocess.list2cmdline(args)


def build_custom_loader_launch(
    *,
    hlae_exe: str,
    cs2_exe: str,
    hook_dll: str,
    command_line: str,
) -> list[str]:
    return [
        hlae_exe,
        "-noGui",
        "-autoStart",
        "-customLoader",
        "-hookDllPath", hook_dll,
        "-programPath", cs2_exe,
        "-cmdLine", command_line,
    ]


def build_bootstrap_commands() -> list[str]:
    return [
        "mirv_endofmatch 0",
        "mirv_fix time 1",
        "mirv_suppress_disconnects 1",
        "tv_nochat 1",
        "spec_autodirector 0",
        "cl_spec_auto_observer 0",
        "mirv_streams record end",
    ]


def build_pov_commands(recipe: RenderRecipe) -> list[str]:
    accountid = steamid64_to_accountid(recipe.player_steamid64)
    commands = [
        "spec_autodirector 0",
        "cl_spec_auto_observer 0",
        "spec_camera_follow 0",
        "spec_lock_to_accountid 0",
        "spec_mode 4",
    ]
    if accountid:
        commands.extend([
            f"spec_player_by_accountid {accountid}",
            f"spec_lock_to_accountid {accountid}",
        ])
    else:
        escaped = recipe.player_name.replace('"', "").strip()
        if escaped:
            commands.append(f'spec_player_by_name "{escaped}"')
    commands.append("spec_camera_follow 1")
    return commands


def build_seek_window(recipe: RenderRecipe) -> tuple[int, int]:
    return recipe.seek_tick, recipe.stop_tick


def build_mirv_streams_setup_commands(
    recipe: RenderRecipe,
    raw_dir: Path,
    *,
    image_format: str = "png",
) -> list[str]:
    take_dir = raw_dir / recipe.raw_name_prefix
    take_dir.mkdir(parents=True, exist_ok=True)
    # Simple screen-record MVP commands. These remain centralized so we can
    # switch to richer stream templates later without changing orchestration.
    return [
        "mirv_streams record end",
        "mirv_streams record screen enabled 1",
        f"mirv_streams record fps {recipe.fps}",
        f'mirv_streams record name "{take_dir}"',
        "mirv_streams record campath 0",
    ]


def build_mirv_streams_start_commands(recipe: RenderRecipe) -> list[str]:
    return [
        "mirv_streams record start",
    ]


def build_mirv_streams_stop_commands() -> list[str]:
    return [
        "mirv_streams record end",
        "host_framerate 0",
    ]
