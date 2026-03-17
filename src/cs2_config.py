"""
cs2_config.py
Configuration for local CS2 game control and demo playback automation.

All settings can be overridden via environment variables (CS2_* prefix)
or by editing the returned config dict at runtime.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path


# ── Default paths (Windows) ───────────────────────────────────────────────────

_DEFAULT_STEAM_PATHS: list[str] = [
    r"C:\Program Files (x86)\Steam",
    r"C:\Program Files\Steam",
    r"D:\Steam",
    r"D:\SteamLibrary",
]

_CS2_RELATIVE = r"steamapps\common\Counter-Strike Global Offensive"
_CS2_EXE_NAME = "cs2.exe"
_CS2_WINDOW_TITLE = "Counter-Strike 2"
_CS2_PROCESS_NAME = "cs2.exe"

# CS2 console uses a local TCP RCON-like interface on this default port.
# Valve's -netconport launch option controls it.
_DEFAULT_NETCON_PORT = 2121

# ── Timeouts (seconds) ───────────────────────────────────────────────────────

_DEFAULT_LAUNCH_TIMEOUT = 45
_DEFAULT_DEMO_LOAD_TIMEOUT = 30
_DEFAULT_SEEK_TIMEOUT = 15
_DEFAULT_COMMAND_DELAY = 0.3
_DEFAULT_CONTROL_BACKEND = "plain"
_DEFAULT_DEMO_READY_SETTLE = 8.0
_DEFAULT_POST_SEEK_SETTLE = 4.0


def build_cs2_config(overrides: dict | None = None) -> dict:
    """Build a CS2 control config dict.

    Resolution order for each key:
      1. overrides dict (explicit caller value)
      2. environment variable (CS2_* prefix)
      3. built-in default
    """
    overrides = overrides or {}

    def _env(key: str, default: str | None = None) -> str | None:
        return os.environ.get(f"CS2_{key.upper()}", default)

    def _env_int(key: str, default: int) -> int:
        raw = _env(key)
        if raw is not None:
            try:
                return int(raw)
            except ValueError:
                pass
        return default

    def _env_bool(key: str, default: bool) -> bool:
        raw = _env(key)
        if raw is not None:
            return raw.lower() in ("1", "true", "yes")
        return default

    # ── Resolve CS2 executable ────────────────────────────────────────────
    cs2_exe = overrides.get("cs2_exe") or _env("EXE")
    if not cs2_exe:
        cs2_exe = _find_cs2_executable()

    steam_path = overrides.get("steam_path") or _env("STEAM_PATH")
    if not steam_path:
        steam_path = _find_steam_path()

    # ── Build config ──────────────────────────────────────────────────────
    cfg = {
        # Paths
        "cs2_exe": cs2_exe,
        "steam_path": steam_path,
        "demo_dir": overrides.get("demo_dir") or _env("DEMO_DIR"),
        "demo_stage_subdir": overrides.get("demo_stage_subdir") or _env("DEMO_STAGE_SUBDIR", "replays/cs2coach"),

        # Launch options
        "launch_options": overrides.get("launch_options") or _env("LAUNCH_OPTIONS", ""),
        "skip_launch": overrides.get("skip_launch") if "skip_launch" in overrides else _env_bool("SKIP_LAUNCH", False),
        "netcon_port": overrides.get("netcon_port") or _env_int("NETCON_PORT", _DEFAULT_NETCON_PORT),
        "fullscreen": overrides.get("fullscreen") if "fullscreen" in overrides else _env_bool("FULLSCREEN", False),
        "width": overrides.get("width") or _env_int("WIDTH", 1920),
        "height": overrides.get("height") or _env_int("HEIGHT", 1080),

        # Process matching
        "process_name": _CS2_PROCESS_NAME,
        "window_title": overrides.get("window_title") or _env("WINDOW_TITLE", _CS2_WINDOW_TITLE),

        # Timeouts (seconds)
        "launch_timeout": overrides.get("launch_timeout") or _env_int("LAUNCH_TIMEOUT", _DEFAULT_LAUNCH_TIMEOUT),
        "demo_load_timeout": overrides.get("demo_load_timeout") or _env_int("DEMO_LOAD_TIMEOUT", _DEFAULT_DEMO_LOAD_TIMEOUT),
        "seek_timeout": overrides.get("seek_timeout") or _env_int("SEEK_TIMEOUT", _DEFAULT_SEEK_TIMEOUT),
        "command_delay": overrides.get("command_delay") or float(_env("COMMAND_DELAY", str(_DEFAULT_COMMAND_DELAY))),
        "demo_ready_settle_s": overrides.get("demo_ready_settle_s") or float(_env("DEMO_READY_SETTLE_S", str(_DEFAULT_DEMO_READY_SETTLE))),
        "post_seek_settle_s": overrides.get("post_seek_settle_s") or float(_env("POST_SEEK_SETTLE_S", str(_DEFAULT_POST_SEEK_SETTLE))),
        "use_coarse_round_seek": overrides.get("use_coarse_round_seek") if "use_coarse_round_seek" in overrides else _env_bool("USE_COARSE_ROUND_SEEK", False),

        # Control method
        "use_netcon": overrides.get("use_netcon") if "use_netcon" in overrides else _env_bool("USE_NETCON", True),
        # Manual debug only: allows Win32 keyboard UI fallback when netcon is unavailable.
        # Keep disabled for production rendering.
        "allow_ui_fallback": overrides.get("allow_ui_fallback") if "allow_ui_fallback" in overrides else _env_bool("ALLOW_UI_FALLBACK", False),

        # Backend selection
        "control_backend": str(overrides.get("control_backend") or _env("CONTROL_BACKEND", _DEFAULT_CONTROL_BACKEND)).strip().lower(),

        # Optional HLAE integration
        "hlae_exe": overrides.get("hlae_exe") or _env("HLAE_EXE"),
        "hlae_args": overrides.get("hlae_args") or _env("HLAE_ARGS", ""),
        "hlae_launch_template": overrides.get("hlae_launch_template") or _env("HLAE_LAUNCH_TEMPLATE", ""),
        "hlae_config_dir": overrides.get("hlae_config_dir") or _env("HLAE_CONFIG_DIR"),
        "hlae_hook_dll": overrides.get("hlae_hook_dll") or _env("HLAE_HOOK_DLL"),
    }
    return cfg


def validate_cs2_config(cfg: dict) -> list[dict]:
    """Validate config and return a list of diagnostic entries.

    Each entry: {"level": "ok"|"warning"|"error", "field": str, "message": str}
    """
    diag: list[dict] = []

    # CS2 executable
    exe = cfg.get("cs2_exe")
    if not exe:
        diag.append({"level": "error", "field": "cs2_exe", "message": "CS2 executable path not found. Set CS2_EXE env var or configure manually."})
    elif not Path(exe).is_file():
        diag.append({"level": "error", "field": "cs2_exe", "message": f"CS2 executable not found at: {exe}"})
    else:
        diag.append({"level": "ok", "field": "cs2_exe", "message": f"CS2 found: {exe}"})

    # Steam path
    steam = cfg.get("steam_path")
    if not steam:
        diag.append({"level": "warning", "field": "steam_path", "message": "Steam path not found. Set CS2_STEAM_PATH if needed."})
    elif not Path(steam).is_dir():
        diag.append({"level": "warning", "field": "steam_path", "message": f"Steam path does not exist: {steam}"})
    else:
        diag.append({"level": "ok", "field": "steam_path", "message": f"Steam found: {steam}"})

    # Netcon port
    port = cfg.get("netcon_port", 0)
    if not isinstance(port, int) or port < 1 or port > 65535:
        diag.append({"level": "error", "field": "netcon_port", "message": f"Invalid netcon port: {port}"})
    else:
        diag.append({"level": "ok", "field": "netcon_port", "message": f"Netcon port: {port}"})

    if bool(cfg.get("skip_launch")):
        diag.append({
            "level": "warning",
            "field": "skip_launch",
            "message": "CS2_SKIP_LAUNCH is enabled. The render worker will require an already running CS2 instance.",
        })

    backend = str(cfg.get("control_backend") or _DEFAULT_CONTROL_BACKEND).strip().lower()
    if backend not in {"plain", "hlae"}:
        diag.append({
            "level": "error",
            "field": "control_backend",
            "message": f"Unsupported control backend: {backend}",
        })
    else:
        diag.append({
            "level": "ok",
            "field": "control_backend",
            "message": f"Control backend: {backend}",
        })

    if backend == "hlae":
        hlae_exe = cfg.get("hlae_exe")
        template = str(cfg.get("hlae_launch_template") or "").strip()
        if not hlae_exe:
            diag.append({
                "level": "error",
                "field": "hlae_exe",
                "message": "HLAE backend requested but HLAE_EXE is not configured.",
            })
        elif not Path(hlae_exe).is_file():
            diag.append({
                "level": "error",
                "field": "hlae_exe",
                "message": f"HLAE executable not found at: {hlae_exe}",
            })
        else:
            diag.append({"level": "ok", "field": "hlae_exe", "message": f"HLAE found: {hlae_exe}"})

        if not template:
            diag.append({
                "level": "error",
                "field": "hlae_launch_template",
                "message": "HLAE backend requested but HLAE_LAUNCH_TEMPLATE is empty.",
            })
        else:
            diag.append({
                "level": "ok",
                "field": "hlae_launch_template",
                "message": "HLAE launch template configured.",
            })

    # Demo dir
    demo_dir = cfg.get("demo_dir")
    if demo_dir and not Path(demo_dir).is_dir():
        diag.append({"level": "warning", "field": "demo_dir", "message": f"Demo directory does not exist: {demo_dir}"})

    return diag


# ── Auto-detection helpers ────────────────────────────────────────────────────

def _find_steam_path() -> str | None:
    """Try to locate Steam installation directory."""
    for candidate in _DEFAULT_STEAM_PATHS:
        p = Path(candidate)
        if p.is_dir() and (p / "steam.exe").is_file():
            return str(p)
    return None


def _find_cs2_executable() -> str | None:
    """Try to locate cs2.exe under known Steam library paths."""
    for steam_base in _DEFAULT_STEAM_PATHS:
        candidate = Path(steam_base) / _CS2_RELATIVE / "game" / "bin" / "win64" / _CS2_EXE_NAME
        if candidate.is_file():
            return str(candidate)
    # Also check if steam path has a libraryfolders.vdf pointing elsewhere
    steam = _find_steam_path()
    if steam:
        candidate = Path(steam) / _CS2_RELATIVE / "game" / "bin" / "win64" / _CS2_EXE_NAME
        if candidate.is_file():
            return str(candidate)
    return None


def get_cs2_launch_args(cfg: dict, demo_path: str | None = None) -> list[str]:
    """Build command-line arguments for launching CS2.

    Returns a list suitable for subprocess.Popen.
    """
    exe = cfg.get("cs2_exe")
    if not exe:
        raise ValueError("cs2_exe is not configured")

    args = [exe]

    # Console-enabled for automation
    args.append("-console")

    # usercon enables remote console input on Source-engine based clients.
    # On some systems/netcon configurations, -netconport alone is not enough
    # for CS2 to open the TCP listener.
    args.append("-usercon")

    # Netcon port for TCP console commands
    port = cfg.get("netcon_port", _DEFAULT_NETCON_PORT)
    args.extend(["-netconport", str(port)])

    # Window mode
    if not cfg.get("fullscreen"):
        args.append("-windowed")
        w = cfg.get("width", 1920)
        h = cfg.get("height", 1080)
        args.extend(["-w", str(w), "-h", str(h)])

    # Extra launch options from config
    extra = str(cfg.get("launch_options") or "").strip()
    if extra:
        args.extend(extra.split())

    # Auto-play demo on launch if provided
    if demo_path:
        args.extend(["+playdemo", demo_path])

    return args


def get_hlae_launch_args(cfg: dict, demo_path: str | None = None) -> list[str]:
    """Build launch arguments for an HLAE-driven CS2 session.

    This is configuration-driven because HLAE setups differ across machines.
    Supported template tokens:
      {hlae_exe}
      {hlae_args}
      {cs2_exe}
      {cs2_args}
      {cs2_launch_command}
      {demo_path}
      {netcon_port}
      {hlae_config_dir}
      {hlae_hook_dll}
    """
    hlae_exe = str(cfg.get("hlae_exe") or "").strip()
    if not hlae_exe:
        raise ValueError("hlae_exe is not configured")

    template = str(cfg.get("hlae_launch_template") or "").strip()
    if not template:
        raise ValueError("hlae_launch_template is not configured")

    cs2_launch = get_cs2_launch_args(cfg, demo_path=demo_path)
    cs2_exe = cs2_launch[0]
    cs2_args = cs2_launch[1:]

    tokens = {
        "hlae_exe": hlae_exe,
        "hlae_args": str(cfg.get("hlae_args") or "").strip(),
        "cs2_exe": cs2_exe,
        "cs2_args": subprocess.list2cmdline([str(part) for part in cs2_args]),
        "cs2_launch_command": subprocess.list2cmdline([str(part) for part in cs2_launch]),
        "demo_path": demo_path or "",
        "netcon_port": str(cfg.get("netcon_port") or _DEFAULT_NETCON_PORT),
        "hlae_config_dir": str(cfg.get("hlae_config_dir") or "").strip(),
        "hlae_hook_dll": str(cfg.get("hlae_hook_dll") or "").strip(),
    }
    rendered = template.format_map(tokens)
    return shlex.split(rendered, posix=False)
