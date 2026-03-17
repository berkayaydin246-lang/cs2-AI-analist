"""
api/main.py
FastAPI backend â€” serves the frontend SPA and exposes CS2 analysis endpoints.

Run:
    uvicorn api.main:app --reload --port 8000
Then open http://localhost:8000
"""
from __future__ import annotations

import json
import math
import os
import shutil
import sys
import tempfile
import time
import uuid
from bisect import bisect_left
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

import logging

logger = logging.getLogger("cs2coach")

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
load_dotenv(dotenv_path=BASE_DIR / ".env")

from src.analyzer import analyze_player          # noqa: E402
from src.clip_store import get_clip_record, list_all_clips, list_demo_clips, scan_clip_integrity  # noqa: E402
from src.render_queue import RenderQueueManager  # noqa: E402
from src.render_modes import RENDER_MODE_INGAME_CAPTURE  # noqa: E402
from src.coach import get_coaching, get_scouting_report  # noqa: E402
from src.parser import parse_demo                # noqa: E402
from src.team_analyzer import analyze_team       # noqa: E402
from src.utils import (                          # noqa: E402
    atomic_json_write,
    create_round_route_gif,
    get_grenade_positions,
    get_player_movement_positions,
    plot_player_activity_map,
    plot_utility_map,
)

# â”€â”€ App setup â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

APP_VERSION = "2.0.0"
app = FastAPI(title="CS2 AI Coach", version=APP_VERSION)

MAX_UPLOAD_SIZE_MB = 2048

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIR = BASE_DIR / "frontend"
GENERATED_DIR = BASE_DIR / "outputs" / "generated"
GENERATED_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")
app.mount("/generated", StaticFiles(directory=str(GENERATED_DIR)), name="generated")

# In-memory session store: demo_id -> session dict
_sessions: dict[str, dict[str, Any]] = {}
MAX_SESSION_COUNT = 20



# ── Startup validation ───────────────────────────────────────────────────────

def _validate_environment() -> dict:
    """Check the local environment and return a structured diagnostic summary."""
    checks: list[dict] = []
    cs2_port_hint = int(os.environ.get("CS2_NETCON_PORT", "2121") or 2121)

    def _add_check(
        name: str,
        ok: bool,
        detail: str,
        *,
        required: bool,
        category: str,
        hint: str | None = None,
    ) -> None:
        item = {
            "name": name,
            "ok": bool(ok),
            "detail": detail,
            "required": bool(required),
            "category": category,
        }
        if not required:
            item["optional"] = True
        if hint:
            item["hint"] = hint
        checks.append(item)

    # Python version
    py = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    _add_check(
        "python",
        sys.version_info >= (3, 10),
        py,
        required=True,
        category="runtime",
        hint="Use Python 3.10+",
    )

    # .env file
    env_file = BASE_DIR / ".env"
    _add_check(
        "env_file",
        env_file.exists(),
        str(env_file),
        required=False,
        category="config",
        hint="Copy .env.example to .env and configure keys/paths as needed",
    )

    # Core package imports (required)
    for pkg_name in ("fastapi", "uvicorn", "awpy", "pandas"):
        try:
            __import__(pkg_name)
            _add_check(
                f"pkg_{pkg_name}",
                True,
                "import ok",
                required=True,
                category="dependencies",
            )
        except Exception:
            _add_check(
                f"pkg_{pkg_name}",
                False,
                "missing or import failed",
                required=True,
                category="dependencies",
                hint="Run: pip install -r requirements.txt",
            )

    # Render package imports (required for clip rendering)
    for pkg_name in ("cv2",):
        try:
            __import__(pkg_name)
            _add_check(
                f"pkg_{pkg_name}",
                True,
                "import ok",
                required=True,
                category="rendering",
            )
        except Exception:
            _add_check(
                f"pkg_{pkg_name}",
                False,
                "missing or import failed",
                required=True,
                category="rendering",
                hint="Run: pip install -r requirements.txt",
            )

    # OBS package import (optional unless in-game capture is used)
    try:
        __import__("obsws_python")
        _add_check(
            "pkg_obsws_python",
            True,
            "import ok",
            required=False,
            category="ingame_capture",
        )
    except Exception:
        _add_check(
            "pkg_obsws_python",
            False,
            "missing or import failed",
            required=False,
            category="ingame_capture",
            hint="Install obsws-python to use cs2_ingame_capture",
        )

    # Anthropic API key (optional for AI routes only)
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    has_key = bool(key and key != "sk-ant-...")
    _add_check(
        "anthropic_api_key",
        has_key,
        "configured" if has_key else "missing",
        required=False,
        category="ai",
        hint="Set ANTHROPIC_API_KEY in .env to enable coaching/scouting endpoints",
    )

    # Steam API key (optional)
    steam_key = os.environ.get("STEAM_API_KEY", "")
    _add_check(
        "steam_api_key",
        bool(steam_key),
        "configured" if steam_key else "not set",
        required=False,
        category="integrations",
        hint="Optional: set STEAM_API_KEY for avatars/profile links",
    )

    # Output directories
    _add_check(
        "output_dir",
        GENERATED_DIR.is_dir(),
        str(GENERATED_DIR),
        required=True,
        category="filesystem",
        hint="Ensure outputs/generated is writable",
    )

    queue_dir = GENERATED_DIR / "queue"
    _add_check(
        "queue_dir",
        queue_dir.exists() or GENERATED_DIR.exists(),
        str(queue_dir),
        required=True,
        category="filesystem",
        hint="Queue directory will be auto-created under outputs/generated/queue",
    )

    # Demo directory
    demos = BASE_DIR / "demos"
    _add_check(
        "demos_dir",
        demos.is_dir(),
        str(demos),
        required=True,
        category="filesystem",
        hint="Create demos directory or upload demos through UI",
    )

    # awpy maps (radar images)
    try:
        import awpy.data  # type: ignore
        maps_ok = awpy.data.MAPS_DIR.is_dir() and any(awpy.data.MAPS_DIR.glob("*.png"))
        _add_check(
            "awpy_maps",
            maps_ok,
            str(awpy.data.MAPS_DIR) if maps_ok else "missing",
            required=True,
            category="dependencies",
            hint="Run: awpy get maps",
        )
    except Exception:
        _add_check(
            "awpy_maps",
            False,
            "awpy not installed or maps missing",
            required=True,
            category="dependencies",
            hint="Run: pip install -r requirements.txt and awpy get maps",
        )

    # Platform (Windows required only for in-game capture)
    is_windows = sys.platform == "win32"
    _add_check(
        "platform",
        True,
        sys.platform,
        required=True,
        category="runtime",
        hint=None if is_windows else "In-game capture currently requires Windows.",
    )

    # In-game capture config validation (optional)
    try:
        from src.cs2_config import build_cs2_config, validate_cs2_config
        from src.obs_controller import build_obs_config, validate_obs_config

        cs2_cfg = build_cs2_config()
        cs2_diag = validate_cs2_config(cs2_cfg)
        cs2_ok = all(item.get("level") != "error" for item in cs2_diag)
        cs2_detail = "; ".join(item.get("message", "") for item in cs2_diag if item.get("level") == "error") or "config ok"
        _add_check(
            "cs2_config",
            cs2_ok,
            cs2_detail,
            required=False,
            category="ingame_capture",
            hint=f"Set CS2_EXE and ensure launch options include -usercon -netconport {cs2_port_hint}",
        )

        obs_cfg = build_obs_config()
        obs_diag = validate_obs_config(obs_cfg)
        obs_ok = all(item.get("level") != "error" for item in obs_diag)
        obs_detail = "; ".join(item.get("message", "") for item in obs_diag if item.get("level") == "error") or "config ok"
        _add_check(
            "obs_config",
            obs_ok,
            obs_detail,
            required=False,
            category="ingame_capture",
            hint="Set OBS_WS_* values in .env if OBS is not on localhost:4455",
        )
    except Exception:
        _add_check(
            "ingame_capture_config",
            False,
            "could not validate CS2/OBS config",
            required=False,
            category="ingame_capture",
            hint="Check CS2 and OBS configuration modules",
        )

    required_failures = [c for c in checks if c.get("required") and not c.get("ok")]
    optional_failures = [c for c in checks if (not c.get("required")) and not c.get("ok")]

    next_steps: list[str] = []
    for item in required_failures + optional_failures:
        hint = str(item.get("hint") or "").strip()
        if hint and hint not in next_steps:
            next_steps.append(hint)

    return {
        "status": "ok" if not required_failures else "degraded",
        "version": APP_VERSION,
        "checks": checks,
        "required_failures": [c.get("name") for c in required_failures],
        "optional_failures": [c.get("name") for c in optional_failures],
        "operator_next_steps": next_steps,
    }


def _log_startup_summary() -> None:
    env = _validate_environment()
    logger.info("CS2 AI Coach %s starting — status: %s", APP_VERSION, env["status"])
    for c in env["checks"]:
        icon = "OK" if c["ok"] else ("--" if c.get("optional") else "!!")
        logger.info("  [%s] %-20s %s", icon, c["name"], c["detail"])


_log_startup_summary()


# ── Render queue ──────────────────────────────────────────────────────────────
_render_queue = RenderQueueManager(
    persist_dir=GENERATED_DIR / "queue",
    max_retries=int(os.getenv("RENDER_JOB_MAX_RETRIES", "1")),
    lease_timeout_s=int(os.getenv("RENDER_JOB_LEASE_TIMEOUT_S", "120")),
)


def _demo_snapshot_path(demo_id: str) -> Path:
    return GENERATED_DIR / "queue" / "snapshots" / f"{demo_id}.json"


def _persist_demo_snapshot(demo_id: str, session: dict, parsed: dict) -> str:
    """Persist demo context needed by the standalone render worker."""
    snapshot = {
        "demo_id": demo_id,
        "demo_path": str(session.get("path") or ""),
        "filename": str(session.get("filename") or ""),
        "saved_at": time.time(),
        "parsed_data": parsed,
    }
    path = _demo_snapshot_path(demo_id)
    atomic_json_write(path, snapshot)
    return str(path)


REQUIRED_SCHEMA_VERSION = 12
STEAM_FAILURE_CACHE_TTL_SEC = 30


def _normalize_queue_render_request(payload: dict) -> tuple[str, dict]:
    render_mode = str(payload.get("render_mode") or RENDER_MODE_INGAME_CAPTURE)
    settings = payload.get("target_settings") if isinstance(payload.get("target_settings"), dict) else {}
    settings = dict(settings or {})
    if payload.get("render_preset"):
        settings["render_preset"] = str(payload["render_preset"])
    return render_mode, settings


def _validate_queue_plans_for_render(demo_id: str, plans: list[dict], render_mode: str) -> None:
    from src.capture_pipeline import validate_capture_environment
    from src.render_modes import RENDER_MODE_INGAME_CAPTURE, validate_plan_for_mode

    invalid: list[str] = []
    for plan in plans:
        result = validate_plan_for_mode(plan, render_mode)
        if result.get("compatible"):
            continue
        title = str(plan.get("title") or plan.get("clip_plan_id") or "clip plan")
        reason = "; ".join(result.get("warnings") or []) or f"{render_mode} is not supported"
        fallback = result.get("fallback_mode")
        if fallback:
            reason = f"{reason}. Suggested fallback: {fallback}"
        invalid.append(f"{title}: {reason}")

    if invalid:
        raise HTTPException(
            400,
            detail=f"Some clip plans cannot be queued for {render_mode}: {' | '.join(invalid[:4])}",
        )

    if render_mode != RENDER_MODE_INGAME_CAPTURE:
        return

    session = _sess(demo_id)
    env_check = validate_capture_environment(
        demo_path=session.get("path", "") or None,
        output_dir=str(GENERATED_DIR / "clips"),
        check_obs_connection=True,
        check_cs2_process=True,
    )
    if env_check.blockers:
        raise HTTPException(
            400,
            detail="In-game capture is not ready: " + "; ".join(env_check.blockers[:4]),
        )


def _partition_queue_plans_for_render(
    demo_id: str,
    plans: list[dict],
    render_mode: str,
) -> tuple[list[dict], list[str]]:
    from src.render_modes import validate_plan_for_mode

    compatible: list[dict] = []
    invalid: list[str] = []
    for plan in plans:
        result = validate_plan_for_mode(plan, render_mode)
        if result.get("compatible"):
            compatible.append(plan)
            continue
        title = str(plan.get("title") or plan.get("clip_plan_id") or "clip plan")
        reason = "; ".join(result.get("warnings") or []) or f"{render_mode} is not supported"
        invalid.append(f"{title}: {reason}")

    if compatible and render_mode == RENDER_MODE_INGAME_CAPTURE:
        _validate_queue_plans_for_render(demo_id, compatible, render_mode)

    return compatible, invalid


def _enqueue_render_job_for_plan(
    *,
    demo_id: str,
    session: dict,
    parsed: dict,
    clip_plan: dict,
    render_mode: str,
    settings: dict,
):
    snapshot_path = _persist_demo_snapshot(demo_id, session, parsed)
    return _render_queue.enqueue(
        demo_id=demo_id,
        clip_plan_id=str(clip_plan.get("clip_plan_id") or ""),
        render_mode=render_mode,
        target_settings=settings,
        clip_plan=clip_plan,
        demo_snapshot_path=snapshot_path,
    )


# â”€â”€ Helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _sess(demo_id: str) -> dict:
    s = _sessions.get(demo_id)
    if not s:
        raise HTTPException(404, detail="Demo session not found")
    s["last_access"] = time.time()
    return s


def _parsed(demo_id: str) -> tuple[dict, dict]:
    s = _sess(demo_id)
    if "parsed_data" not in s:
        raise HTTPException(400, detail="Demo not parsed yet â€” POST /api/demo/{id}/parse first")
    return s, s["parsed_data"]


def _safe(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


GRENADE_FLIGHT_TICKS = {
    "smoke": 105,
    "flash": 78,
    "he_grenade": 90,
    "molotov": 112,
    "incendiary": 112,
    "decoy": 95,
}


def _round_meta_bounds(parsed: dict, round_num: int) -> tuple[int, int] | None:
    for r in parsed.get("rounds", []):
        rn = _safe_int(r.get("round_num", r.get("round")), 0)
        if rn != round_num:
            continue
        start = _safe_int(r.get("start"), 0)
        freeze_end = _safe_int(r.get("freeze_end"), 0)
        end = _safe_int(r.get("official_end"), 0)
        if end <= 0:
            end = _safe_int(r.get("end"), 0)
        if start <= 0:
            start = freeze_end
        if start > 0 and end >= start:
            return (start, end)
    return None


def _cleanup_sessions(max_sessions: int = MAX_SESSION_COUNT) -> None:
    """Keep in-memory sessions bounded and delete temp files for old sessions."""
    if len(_sessions) <= max_sessions:
        return

    ordered = sorted(
        _sessions.items(),
        key=lambda kv: float(kv[1].get("created_at", 0.0)),
    )
    drop_count = max(0, len(_sessions) - max_sessions)
    for demo_id, sess in ordered[:drop_count]:
        path = sess.get("path")
        if isinstance(path, str) and path:
            try:
                os.remove(path)
            except OSError:
                pass
        generated_dir = sess.get("generated_dir")
        if isinstance(generated_dir, str) and generated_dir:
            try:
                shutil.rmtree(generated_dir, ignore_errors=True)
            except OSError:
                pass
        _sessions.pop(demo_id, None)


def _safe_slug(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in str(value or "").strip())
    cleaned = cleaned.strip("_").lower()
    return cleaned or "player"


def _generated_url(path_str: str | None) -> str | None:
    if not path_str:
        return None
    path = Path(path_str)
    if not path.exists():
        return None
    try:
        rel = path.relative_to(GENERATED_DIR).as_posix()
    except ValueError:
        return None
    ver = int(path.stat().st_mtime)
    return f"/generated/{rel}?v={ver}"


def _ensure_session_parsed_schema(session: dict, min_schema: int = REQUIRED_SCHEMA_VERSION) -> dict:
    parsed = session.get("parsed_data")
    if isinstance(parsed, dict) and int(parsed.get("schema_version", 0)) >= min_schema:
        return parsed
    parsed = parse_demo(session["path"])
    session["parsed_data"] = parsed
    return parsed


def _normalize_steamid64(value: Any) -> str | None:
    """Convert any SteamID representation to a normalized SteamID64 string.

    CRITICAL: Must NOT use float() as an intermediate step.
    SteamID64 values (~7.6×10^16) exceed float64 precision (2^53 ≈ 9×10^15).
    Using float() silently truncates the ID, e.g.:
        int(float(76561198388732174)) == 76561198388732160  ← WRONG
    This causes the Steam API to return a completely different player's profile.
    """
    if value is None or value == "":
        return None
    try:
        if isinstance(value, str):
            s = value.strip()
            if not s or not s.isdigit():
                return None
            int_val = int(s)          # string → int: no precision loss
        elif isinstance(value, float):
            if value != value:        # NaN check
                return None
            int_val = int(value)      # float already lost precision, best effort
        else:
            int_val = int(value)      # int/numpy.int64/pandas.Int64: no precision loss
    except (TypeError, ValueError, OverflowError):
        return None
    if int_val <= 0:
        return None
    sid = str(int_val)
    if sid.isdigit() and 16 <= len(sid) <= 20:
        return sid
    return None


def _extract_player_steamid64(parsed: dict, player_name: str) -> str | None:
    """Resolve SteamID64 for a player name from parsed demo data.

    Lookup order:
    1. identities["by_name"]  — new dual-index structure (steamid64-primary)
    2. identities legacy      — old name-keyed structure {name: {steamid64: str}}
    3. Scan player_positions  — frequency vote fallback
    """
    identities = parsed.get("player_identities", {})

    # ── New structure: {"by_steamid": {...}, "by_name": {...}} ────────────────
    if isinstance(identities, dict) and "by_name" in identities:
        by_name: dict = identities.get("by_name") or {}
        # Exact name match
        sid = _normalize_steamid64(by_name.get(player_name))
        if sid:
            return sid
        # Case-insensitive fallback
        lname = player_name.strip().lower()
        for name, steamid in by_name.items():
            if str(name).strip().lower() == lname:
                sid = _normalize_steamid64(steamid)
                if sid:
                    return sid

    # ── Legacy structure: {player_name: {"steamid64": str}} ──────────────────
    elif isinstance(identities, dict):
        direct = identities.get(player_name)
        if isinstance(direct, dict):
            sid = _normalize_steamid64(direct.get("steamid64"))
            if sid:
                return sid
        lname = player_name.strip().lower()
        for name, identity in identities.items():
            if str(name).strip().lower() != lname:
                continue
            if isinstance(identity, dict):
                sid = _normalize_steamid64(identity.get("steamid64"))
                if sid:
                    return sid

    # ── Final fallback: vote on steamid field across player_positions ─────────
    counts: dict[str, int] = {}
    lname = player_name.strip().lower()
    for row in parsed.get("player_positions", []):
        if str(row.get("player_name") or "").strip().lower() != lname:
            continue
        sid = _normalize_steamid64(row.get("steamid"))
        if not sid:
            continue
        counts[sid] = counts.get(sid, 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda kv: kv[1])[0]


def _build_player_steamids(parsed: dict) -> dict[str, str]:
    """Return a flat {player_name → steamid64} map from parsed data."""
    identities = parsed.get("player_identities", {})
    result: dict[str, str] = {}
    if not isinstance(identities, dict):
        return result
    # New structure
    by_name = identities.get("by_name")
    if isinstance(by_name, dict):
        for name, val in by_name.items():
            sid = _normalize_steamid64(val)
            if sid:
                result[name] = sid
        return result
    # Legacy structure: {name: {steamid64: str}}
    for name, val in identities.items():
        if isinstance(val, dict):
            sid = _normalize_steamid64(val.get("steamid64"))
            if sid:
                result[name] = sid
    return result


def _fetch_steam_profile(steamid64: str) -> tuple[dict[str, Any] | None, str | None, dict[str, Any]]:
    api_key = str(os.getenv("STEAM_API_KEY", "")).strip()
    if not api_key:
        return None, "steam_api_key_missing", {"api_called": False}

    params = urlencode({"key": api_key, "steamids": steamid64})
    url = f"https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/?{params}"
    req = Request(url, headers={"Accept": "application/json", "User-Agent": "cs2-coach/steam-profile"})

    try:
        with urlopen(req, timeout=6) as resp:
            status_code = getattr(resp, "status", 200)
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    except HTTPError as exc:
        return None, "steam_api_http_error", {"api_called": True, "http_status": int(getattr(exc, "code", 0) or 0)}
    except (URLError, TimeoutError):
        return None, "steam_api_unavailable", {"api_called": True}
    except Exception:
        return None, "steam_api_error", {"api_called": True}

    players = payload.get("response", {}).get("players", [])
    if not isinstance(players, list) or not players:
        return None, "steam_profile_not_found", {"api_called": True, "http_status": int(status_code)}

    p0 = players[0] or {}
    return {
        "steamid64": _normalize_steamid64(p0.get("steamid")) or steamid64,
        "personaname": str(p0.get("personaname") or ""),
        "avatar_url": str(p0.get("avatarfull") or ""),
        "profile_url": str(p0.get("profileurl") or ""),
    }, None, {"api_called": True, "http_status": int(status_code)}


# â”€â”€ Frontend â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@app.get("/", include_in_schema=False)
def serve_index():
    return FileResponse(str(FRONTEND_DIR / "index.html"))


# â”€â”€ Demo lifecycle â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@app.get("/api/health")
def health():
    """Environment health check — validates API keys, directories, dependencies."""
    return _validate_environment()


@app.post("/api/demo/upload")
async def upload_demo(file: UploadFile = File(...)):
    original_name = str(file.filename or "")
    suffix = Path(original_name).suffix.lower()
    if suffix != ".dem":
        raise HTTPException(
            400,
            detail="Invalid file type. Upload a .dem file exported from Counter-Strike 2.",
        )

    demo_id = uuid.uuid4().hex[:10]
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix or ".dem")
    content = await file.read()
    if not content:
        tmp.close()
        Path(tmp.name).unlink(missing_ok=True)
        raise HTTPException(400, detail="Uploaded file is empty. Please choose a valid .dem file.")

    size_mb = round(len(content) / 1_048_576, 1)
    if size_mb > MAX_UPLOAD_SIZE_MB:
        tmp.close()
        Path(tmp.name).unlink(missing_ok=True)
        raise HTTPException(
            400,
            detail=f"Demo file is too large ({size_mb} MB). Maximum supported upload is {MAX_UPLOAD_SIZE_MB} MB.",
        )

    tmp.write(content)
    tmp.close()
    _sessions[demo_id] = {
        "path": tmp.name,
        "filename": original_name or "demo.dem",
        "size_mb": size_mb,
        "created_at": time.time(),
        "last_access": time.time(),
    }
    _cleanup_sessions()
    return {"demo_id": demo_id, "filename": original_name, "size_mb": _sessions[demo_id]["size_mb"]}


@app.post("/api/demo/{demo_id}/parse")
def parse(demo_id: str):
    s = _sess(demo_id)
    try:
        parsed = _ensure_session_parsed_schema(s, min_schema=REQUIRED_SCHEMA_VERSION)
        _persist_demo_snapshot(demo_id, s, parsed)
        player_steamids = _build_player_steamids(parsed)
        print(f"[parse] player_steamids resolved: {len(player_steamids)} players")
        for name, sid in player_steamids.items():
            print(f"    name={name!r}  steamid64={sid}")
        return {
            "map": parsed["map"],
            "total_rounds": parsed["total_rounds"],
            "players": parsed["players"],
            "player_steamids": player_steamids,
            "highlight_count": int((parsed.get("highlight_summary") or {}).get("total_highlights", 0)),
            "clip_plan_count": int((parsed.get("clip_plan_summary") or {}).get("total_clip_plans", 0)),
        }
    except Exception as exc:
        message = str(exc).strip() or exc.__class__.__name__
        hints: list[str] = [
            "Ensure the uploaded file is a valid CS2 .dem file.",
            "If map images are missing, run: awpy get maps",
        ]
        lower = message.lower()
        if "file" in lower and "not found" in lower:
            hints.insert(0, "Re-upload the demo file and retry parsing.")
        if "schema" in lower:
            hints.insert(0, "Delete stale parsed output and re-parse the demo.")
        if "awpy" in lower:
            hints.insert(0, "Verify awpy installation: pip install -r requirements.txt")

        raise HTTPException(
            500,
            detail={
                "error": "Demo parse failed",
                "message": message,
                "demo_id": demo_id,
                "filename": s.get("filename"),
                "hints": hints,
            },
        )


@app.get("/api/demo/{demo_id}/info")
def demo_info(demo_id: str):
    s, p = _parsed(demo_id)
    return {
        "demo_id": demo_id,
        "filename": s.get("filename"),
        "map": p["map"],
        "total_rounds": p["total_rounds"],
        "players": p["players"],
        "highlight_count": int((p.get("highlight_summary") or {}).get("total_highlights", 0)),
        "clip_plan_count": int((p.get("clip_plan_summary") or {}).get("total_clip_plans", 0)),
    }


@app.get("/api/demo/{demo_id}/highlights")
def demo_highlights(demo_id: str):
    _, parsed = _parsed(demo_id)
    return {
        "summary": parsed.get("highlight_summary", {}),
        "highlights": parsed.get("highlights", []),
    }


@app.get("/api/demo/{demo_id}/clip-plans")
def demo_clip_plans(demo_id: str):
    _, parsed = _parsed(demo_id)
    return {
        "summary": parsed.get("clip_plan_summary", {}),
        "clip_plans": parsed.get("clip_plans", []),
    }


def _find_clip_plan(parsed: dict, clip_plan_id: str) -> dict | None:
    for plan in parsed.get("clip_plans", []) or []:
        if str(plan.get("clip_plan_id") or "") == clip_plan_id:
            return plan
    return None


def _find_highlight(parsed: dict, highlight_id: str) -> dict | None:
    if not highlight_id:
        return None
    for item in parsed.get("highlights", []) or []:
        if str(item.get("highlight_id") or "") == highlight_id:
            return item
    return None


def _summarize_clips(clips: list[dict]) -> dict:
    by_type: dict[str, int] = {}
    by_status: dict[str, int] = {}
    missing_files = 0
    for item in clips:
        clip_type = str(item.get("clip_type") or "unknown")
        status = str(item.get("status") or "unknown")
        by_type[clip_type] = by_type.get(clip_type, 0) + 1
        by_status[status] = by_status.get(status, 0) + 1
        warnings = list(((item.get("metadata") or {}).get("validation_warnings")) or [])
        if "missing_video_file" in warnings:
            missing_files += 1
    return {
        "total_clips": len(clips),
        "by_type": by_type,
        "by_status": by_status,
        "missing_files": missing_files,
    }


@app.get("/api/demo/{demo_id}/clips")
def demo_clips(demo_id: str):
    _parsed(demo_id)
    clips = list_demo_clips(GENERATED_DIR, demo_id)
    return {
        "summary": _summarize_clips(clips),
        "clips": clips,
    }


@app.get("/api/clips")
def all_clips():
    clips = list_all_clips(GENERATED_DIR)
    return {
        "summary": _summarize_clips(clips),
        "clips": clips,
    }


@app.get("/api/clips/integrity")
def clips_integrity():
    """Scan all clip indexes for missing files and stale references."""
    return scan_clip_integrity(GENERATED_DIR)


@app.get("/api/clips/{clip_id}")
def clip_detail(clip_id: str, demo_id: str | None = None):
    clip = get_clip_record(GENERATED_DIR, clip_id, demo_id=demo_id)
    if not clip:
        raise HTTPException(404, detail=f"Clip not found: {clip_id}")
    return {"clip": clip}


@app.get("/api/render-modes")
def list_render_modes():
    """Return available and known render modes."""
    from src.render_modes import SUPPORTED_RENDER_MODES, get_available_render_modes
    return {
        "available": get_available_render_modes(),
        "all": {
            mode: {
                "label": info["label"],
                "available": info["available"],
                "deprecated": bool(info.get("deprecated")),
                "description": info.get("description", ""),
                "requires_game_client": info.get("requires_game_client", False),
                "output_format": info.get("output_format", "mp4"),
            }
            for mode, info in SUPPORTED_RENDER_MODES.items()
        },
    }


@app.get("/api/render-presets")
def list_render_presets():
    """Return available render quality presets."""
    from src.render_presets import RENDER_PRESETS, DEFAULT_PRESET
    return {
        "default": DEFAULT_PRESET,
        "presets": {
            name: {
                "label": p["label"],
                "description": p["description"],
                "quality_tier": p["quality_tier"],
                "capture_profile": p.get("capture_profile", "default"),
            }
            for name, p in RENDER_PRESETS.items()
        },
    }


@app.get("/api/render-info")
def render_info():
    """Return the full render capability matrix: modes + presets + capture profiles."""
    from src.render_modes import get_render_capability_matrix
    from src.render_presets import RENDER_PRESETS, DEFAULT_PRESET
    matrix = get_render_capability_matrix()
    matrix["presets"] = {
        name: {
            "label": p["label"],
            "description": p["description"],
            "quality_tier": p["quality_tier"],
            "capture_profile": p.get("capture_profile", "default"),
            "hud_preference": p.get("hud_preference", "default"),
        }
        for name, p in RENDER_PRESETS.items()
    }
    matrix["default_preset"] = DEFAULT_PRESET
    return matrix


@app.post("/api/demo/{demo_id}/clips/render/{clip_plan_id}")
def render_demo_clip(demo_id: str, clip_plan_id: str, payload: dict | None = None):
    s, parsed = _parsed(demo_id)
    clip_plan = _find_clip_plan(parsed, clip_plan_id)
    if not clip_plan:
        raise HTTPException(404, detail=f"Clip plan not found: {clip_plan_id}")

    payload = payload or {}
    render_mode, settings = _normalize_queue_render_request(payload)
    _validate_queue_plans_for_render(demo_id, [clip_plan], render_mode)
    job = _enqueue_render_job_for_plan(
        demo_id=demo_id,
        session=s,
        parsed=parsed,
        clip_plan=clip_plan,
        render_mode=render_mode,
        settings=settings,
    )
    return {
        "status": "queued",
        "message": f"Render job queued. Poll /api/queue or /api/queue/job/{job.job_id} for progress and /api/demo/{demo_id}/clips for completed artifacts.",
        "job": job.to_dict(),
    }


# â”€â”€ Radar image â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@app.get("/api/radar/{map_name}")
def radar(map_name: str):
    try:
        import awpy.data  # type: ignore
        p = awpy.data.MAPS_DIR / f"{map_name}.png"
        if p.exists():
            with open(p, "rb") as f:
                return Response(f.read(), media_type="image/png")
    except Exception:
        pass
    legacy = BASE_DIR / "De_mirage_radar.webp"
    if map_name == "de_mirage" and legacy.exists():
        with open(legacy, "rb") as f:
            return Response(f.read(), media_type="image/webp")
    raise HTTPException(404, detail="Radar image not found")


# â”€â”€ Player analysis â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@app.post("/api/demo/{demo_id}/analyze/{player_name:path}")
def analyze(demo_id: str, player_name: str):
    s, parsed = _parsed(demo_id)
    result = analyze_player(parsed, player_name)
    if "analyses" not in s:
        s["analyses"] = {}
    s["analyses"][player_name] = result
    return result


@app.get("/api/demo/{demo_id}/player/{player_name:path}/steam")
def player_steam_profile(
    demo_id: str,
    player_name: str,
    steamid64: str = "",
    refresh: int = 0,
    debug: int = 0,
):
    s = _sess(demo_id)
    parsed = _ensure_session_parsed_schema(s, min_schema=REQUIRED_SCHEMA_VERSION)

    # Use caller-supplied SteamID64 when available — avoids name-based lookup entirely
    sid_source = "param"
    resolved = _normalize_steamid64(steamid64.strip()) if steamid64.strip() else None
    if not resolved:
        resolved = _extract_player_steamid64(parsed, player_name)
        sid_source = "lookup"

    # Always log: which player, which SteamID64, how it was resolved
    print(f"[steam] Player: {player_name!r}  SteamID64: {resolved}  source={sid_source}")

    # Rename to steamid64 for rest of function
    steamid64 = resolved  # type: ignore[assignment]

    debug_info: dict[str, Any] = {
        "player": player_name,
        "has_steamid64": bool(steamid64),
        "steamid64": steamid64,
        "sid_source": sid_source,
        "refresh_requested": bool(refresh),
        "schema_version": int(parsed.get("schema_version", 0)),
    }
    if not steamid64:
        resp = {
            "player": player_name,
            "available": False,
            "steamid64": None,
            "personaname": player_name,
            "avatar_url": None,
            "profile_url": None,
            "reason": "steamid_not_found",
        }
        if debug:
            resp["debug"] = debug_info
        print(f"[steam] Player: {player_name!r}  SteamID64: none  reason=steamid_not_found")
        return resp

    steam_cache = s.setdefault("steam_profiles", {})
    cached = steam_cache.get(steamid64)
    should_use_cache = False
    if isinstance(cached, dict) and not bool(refresh):
        cached_ok = bool(cached.get("available") or cached.get("profile_url"))
        fetched_at = float(cached.get("fetched_at", 0.0) or 0.0)
        age_sec = max(0.0, time.time() - fetched_at)
        if cached_ok:
            should_use_cache = True
        elif age_sec <= STEAM_FAILURE_CACHE_TTL_SEC:
            should_use_cache = True
        debug_info["cache_age_sec"] = round(age_sec, 2)
        debug_info["cache_ok"] = cached_ok

    if should_use_cache and isinstance(cached, dict):
        resp = {
            "player": player_name,
            "available": bool(cached.get("profile_url")),
            "steamid64": cached.get("steamid64", steamid64),
            "personaname": cached.get("personaname") or player_name,
            "avatar_url": cached.get("avatar_url") or None,
            "profile_url": cached.get("profile_url") or None,
            "reason": cached.get("reason") or None,
        }
        profile_url_log = resp.get("profile_url") or "unavailable"
        print(f"[steam] Player: {player_name!r}  SteamID64: {steamid64}  Profile: {profile_url_log}  (cache_hit)")
        if debug:
            debug_info["cache_hit"] = True
            resp["debug"] = debug_info
        return resp

    profile, err, fetch_meta = _fetch_steam_profile(steamid64)
    if profile:
        steam_cache[steamid64] = {
            **profile,
            "available": bool(profile.get("profile_url")),
            "reason": None,
            "fetched_at": time.time(),
        }
        resp = {
            "player": player_name,
            "available": bool(profile.get("profile_url")),
            "steamid64": profile.get("steamid64", steamid64),
            "personaname": profile.get("personaname") or player_name,
            "avatar_url": profile.get("avatar_url") or None,
            "profile_url": profile.get("profile_url") or None,
            "reason": None,
        }
        print(f"[steam] Player: {player_name!r}  SteamID64: {steamid64}  Profile: {resp['profile_url'] or 'unavailable'}")
        if debug:
            debug_info["cache_hit"] = False
            debug_info.update(fetch_meta)
            resp["debug"] = debug_info
        return resp

    fallback = {
        "steamid64": steamid64,
        "personaname": player_name,
        "avatar_url": None,
        "profile_url": None,
        "available": False,
        "reason": err,
        "fetched_at": time.time(),
    }
    steam_cache[steamid64] = fallback
    resp = {
        "player": player_name,
        "available": False,
        "steamid64": steamid64,
        "personaname": player_name,
        "avatar_url": None,
        "profile_url": None,
        "reason": err,
    }
    print(f"[steam] Player: {player_name!r}  SteamID64: {steamid64}  Profile: unavailable  reason={err}")
    if debug:
        debug_info["cache_hit"] = False
        debug_info.update(fetch_meta)
        resp["debug"] = debug_info
    return resp


@app.get("/api/demo/{demo_id}/player/{player_name:path}/visuals")
def player_visuals(demo_id: str, player_name: str):
    s, parsed = _parsed(demo_id)
    map_name = parsed.get("map", "unknown")

    out_dir = GENERATED_DIR / demo_id
    out_dir.mkdir(parents=True, exist_ok=True)
    s["generated_dir"] = str(out_dir)

    player_cache = s.setdefault("player_visuals", {})
    cached = player_cache.get(player_name)
    if isinstance(cached, dict):
        paths = cached.get("paths", {})
        if isinstance(paths, dict):
            expected = [v for v in paths.values() if isinstance(v, str) and v]
            if (not expected) or all(Path(p).exists() for p in expected):
                return {
                    "player": player_name,
                    "map": map_name,
                    "heatmap_t_url": _generated_url(paths.get("heatmap_t")),
                    "heatmap_ct_url": _generated_url(paths.get("heatmap_ct")),
                    "utility_url": _generated_url(paths.get("utility")),
                    "route_gif_url": _generated_url(paths.get("route_gif")),
                }

    import matplotlib.pyplot as plt

    slug = _safe_slug(player_name)
    paths: dict[str, str | None] = {
        "heatmap_t": None,
        "heatmap_ct": None,
        "utility": None,
        "route_gif": None,
    }

    # T/CT movement heatmaps
    for side_code, suffix in (("T", "t"), ("CT", "ct")):
        movement = get_player_movement_positions(parsed, player_name, side=side_code)
        if not movement:
            continue
        prefix = f"{slug}_heatmap_{suffix}"
        fig = plot_player_activity_map(
            movement_positions=movement,
            map_name=map_name,
            player_name=player_name,
            output_dir=str(out_dir),
            output_prefix=prefix,
            title_suffix=f"{side_code}-side",
        )
        if fig is not None:
            plt.close(fig)
        img_path = out_dir / f"{prefix}_on_map.png"
        if img_path.exists():
            paths[f"heatmap_{suffix}"] = str(img_path)

    # Utility map
    grenade_positions = get_grenade_positions(parsed, player_name)
    if grenade_positions:
        utility_path = out_dir / f"{slug}_utility.png"
        fig = plot_utility_map(
            grenade_positions=grenade_positions,
            map_name=map_name,
            player_name=player_name,
            save_path=str(utility_path),
        )
        if fig is not None:
            plt.close(fig)
        if utility_path.exists():
            paths["utility"] = str(utility_path)

    # Multi-round route GIF
    gif_path = create_round_route_gif(
        parsed_data=parsed,
        player_name=player_name,
        map_name=map_name,
        output_dir=str(out_dir),
        output_prefix=f"{slug}_route",
        frames_per_round=12,
        frame_duration_ms=75,
        side_filter=None,
    )
    if gif_path and Path(gif_path).exists():
        paths["route_gif"] = str(Path(gif_path))

    player_cache[player_name] = {"paths": paths, "created_at": time.time()}
    return {
        "player": player_name,
        "map": map_name,
        "heatmap_t_url": _generated_url(paths.get("heatmap_t")),
        "heatmap_ct_url": _generated_url(paths.get("heatmap_ct")),
        "utility_url": _generated_url(paths.get("utility")),
        "route_gif_url": _generated_url(paths.get("route_gif")),
    }


# â”€â”€ Team analysis â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@app.get("/api/demo/{demo_id}/team")
def team(demo_id: str):
    s, parsed = _parsed(demo_id)
    if "team_analysis" not in s:
        s["team_analysis"] = analyze_team(parsed)
    return s["team_analysis"]


# â”€â”€ Replay â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@app.get("/api/demo/{demo_id}/replay/rounds")
def replay_rounds(demo_id: str):
    _, parsed = _parsed(demo_id)
    rounds: set[int] = set()
    for pos in parsed.get("player_positions", []):
        rn = _safe_int(pos.get("round_num"))
        if rn > 0:
            rounds.add(rn)
    return {"rounds": sorted(rounds)}


@app.get("/api/demo/{demo_id}/replay/{round_num}")
def replay_round(demo_id: str, round_num: int):
    _, parsed = _parsed(demo_id)
    map_name = parsed.get("map", "unknown")

    def _norm_side(raw) -> str:
        s = str(raw or "").strip().lower()
        if s in ("ct", "3", "counter-terrorist", "counterterrorist"):
            return "CT"
        if s in ("t", "2", "terrorist"):
            return "T"
        return str(raw or "")

    # Build per-player tracks from sampled position data
    tracks_by_player: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for pos in parsed.get("player_positions", []):
        if _safe_int(pos.get("round_num")) != round_num:
            continue
        x_raw = pos.get("x")
        y_raw = pos.get("y")
        if x_raw in (None, "", "nan") or y_raw in (None, "", "nan"):
            continue
        x_val = _safe(x_raw)
        y_val = _safe(y_raw)
        if x_val == 0.0 and y_val == 0.0:
            continue

        tick = _safe_int(pos.get("tick"))
        if tick <= 0:
            continue
        name = str(pos.get("player_name") or "")
        if not name:
            continue
        hp_raw = pos.get("hp")
        yaw_raw = pos.get("yaw")
        tracks_by_player[name].append(
            {
                "tick": tick,
                "x": x_val,
                "y": y_val,
                "side": _norm_side(pos.get("side")),
                "hp": _safe(hp_raw, 100.0) if hp_raw not in (None, "", "nan") else 100.0,
                "yaw": _safe(yaw_raw) if yaw_raw not in (None, "", "nan") else None,
            }
        )

    if not tracks_by_player:
        return {
            "round": round_num,
            "map": map_name,
            "frames": [],
            "kills": [],
            "bombs": [],
            "grenades": [],
            "frame_count": 0,
        }

    for trk in tracks_by_player.values():
        trk.sort(key=lambda p: p["tick"])

    raw_tick_min = min(trk[0]["tick"] for trk in tracks_by_player.values() if trk)
    raw_tick_max = max(trk[-1]["tick"] for trk in tracks_by_player.values() if trk)
    meta_bounds = _round_meta_bounds(parsed, round_num)
    if meta_bounds:
        round_tick_min, round_tick_max = meta_bounds
    else:
        round_tick_min, round_tick_max = raw_tick_min, raw_tick_max

    # Build an evenly spaced round timeline so frame->tick mapping is stable.
    if round_tick_max < round_tick_min:
        round_tick_max = round_tick_min
    max_frames = 300
    span = max(round_tick_max - round_tick_min, 1)
    step = max(1, math.ceil(span / max(max_frames - 1, 1)))
    frame_ticks = list(range(round_tick_min, round_tick_max + 1, step))
    if frame_ticks[-1] != round_tick_max:
        frame_ticks.append(round_tick_max)

    track_ticks_cache = {
        name: [pt["tick"] for pt in trk]
        for name, trk in tracks_by_player.items()
    }

    def _interp_state(track: list[dict[str, Any]], track_ticks: list[int], tick: int) -> dict[str, Any] | None:
        if not track:
            return None
        idx = bisect_left(track_ticks, tick)
        if idx <= 0:
            p1 = track[0]
            p2 = track[1] if len(track) > 1 else track[0]
            alpha = 0.0
        elif idx >= len(track):
            p1 = track[-2] if len(track) > 1 else track[-1]
            p2 = track[-1]
            alpha = 1.0
        else:
            p1 = track[idx - 1]
            p2 = track[idx]
            dt = max(p2["tick"] - p1["tick"], 1)
            alpha = (tick - p1["tick"]) / dt

        x = p1["x"] + (p2["x"] - p1["x"]) * alpha
        y = p1["y"] + (p2["y"] - p1["y"]) * alpha
        hp = p2.get("hp", p1.get("hp", 100.0))
        yaw = p2.get("yaw")
        if yaw is None:
            yaw = p1.get("yaw")
        side = p2.get("side") or p1.get("side") or ""
        return {"x": x, "y": y, "hp": hp, "yaw": yaw, "side": side}

    frames = []
    for t in frame_ticks:
        players = []
        for name, track in tracks_by_player.items():
            st = _interp_state(track, track_ticks_cache.get(name, []), t)
            if not st:
                continue
            players.append(
                {
                    "name": name,
                    "x": st["x"],
                    "y": st["y"],
                    "side": st["side"],
                    "hp": st["hp"],
                    "yaw": st["yaw"],
                }
            )
        frames.append({"tick": t, "players": players})

    def _event_in_round(event_round: int, event_tick: int) -> bool:
        if event_tick <= 0:
            return False
        if event_round > 0:
            if event_round == round_num:
                return True
            # Some schemas may carry noisy round_num; keep tick-bound fallback.
            return round_tick_min <= event_tick <= round_tick_max
        return round_tick_min <= event_tick <= round_tick_max

    # Kills
    kills = []
    for k in parsed.get("kills", []):
        tick = _safe_int(k.get("tick"))
        if not _event_in_round(_safe_int(k.get("round_num"), 0), tick):
            continue
        vx = k.get("victim_x")
        vy = k.get("victim_y")
        kills.append(
            {
                "tick": tick,
                "attacker": str(k.get("attacker_name") or ""),
                "victim": str(k.get("victim_name") or ""),
                "weapon": str(k.get("weapon") or ""),
                "headshot": bool(k.get("headshot")),
                "victim_x": _safe(vx) if vx not in (None, "") else None,
                "victim_y": _safe(vy) if vy not in (None, "") else None,
                "victim_side": str(k.get("victim_side") or ""),
                "attacker_side": str(k.get("attacker_side") or ""),
            }
        )
    kills.sort(key=lambda x: x.get("tick", 0))

    # Bomb events
    bombs = []
    for b in parsed.get("bomb_events", []):
        tick = _safe_int(b.get("tick"))
        if not _event_in_round(_safe_int(b.get("round_num"), 0), tick):
            continue
        x = b.get("x")
        y = b.get("y")
        bombs.append(
            {
                "tick": tick,
                "event": str(b.get("event") or ""),
                "player": str(b.get("player_name") or ""),
                "x": _safe(x) if x not in (None, "") else None,
                "y": _safe(y) if y not in (None, "") else None,
            }
        )
    bombs.sort(key=lambda x: x.get("tick", 0))

    # Grenade events
    grenades_out = []
    seen_grenade_keys: set = set()
    for g in parsed.get("grenades", []):
        tick = _safe_int(g.get("tick"))
        if not _event_in_round(_safe_int(g.get("round_num"), 0), tick):
            continue

        gtype = str(g.get("grenade_type") or "unknown").strip().lower() or "unknown"
        thrower = str(g.get("thrower_name") or "")

        dedup_key = (thrower, gtype, tick)
        if dedup_key in seen_grenade_keys:
            continue
        seen_grenade_keys.add(dedup_key)

        start_x = g.get("nade_start_x", g.get("nade_x", g.get("x")))
        start_y = g.get("nade_start_y", g.get("nade_y", g.get("y")))
        end_x = g.get("nade_end_x", g.get("nade_x", g.get("x")))
        end_y = g.get("nade_end_y", g.get("nade_y", g.get("y")))

        path_points = []
        raw_path = g.get("nade_path", [])
        if isinstance(raw_path, list):
            for pt in raw_path:
                if not isinstance(pt, (list, tuple)) or len(pt) < 2:
                    continue
                px = _safe(pt[0], float("nan"))
                py = _safe(pt[1], float("nan"))
                if not math.isfinite(px) or not math.isfinite(py):
                    continue
                path_points.append([px, py])

        sx = _safe(start_x, float("nan"))
        sy = _safe(start_y, float("nan"))
        ex = _safe(end_x, float("nan"))
        ey = _safe(end_y, float("nan"))

        if not path_points:
            if math.isfinite(sx) and math.isfinite(sy):
                path_points.append([sx, sy])
            if math.isfinite(ex) and math.isfinite(ey):
                path_points.append([ex, ey])

        if len(path_points) == 1:
            path_points.append(path_points[0])

        if not path_points:
            continue

        if not math.isfinite(sx) or not math.isfinite(sy):
            sx, sy = path_points[0]
        if not math.isfinite(ex) or not math.isfinite(ey):
            ex, ey = path_points[-1]

        flight_ticks = GRENADE_FLIGHT_TICKS.get(gtype, 96)
        detonate_tick = tick + flight_ticks

        grenades_out.append(
            {
                "tick": tick,
                "throw_tick": tick,
                "detonate_tick": detonate_tick,
                "flight_ticks": flight_ticks,
                "type": gtype,
                "thrower": thrower,
                "x": ex,
                "y": ey,
                "start_x": sx,
                "start_y": sy,
                "end_x": ex,
                "end_y": ey,
                "path": path_points,
            }
        )
    grenades_out.sort(key=lambda x: x.get("tick", 0))

    return {
        "round": round_num,
        "map": map_name,
        "frames": frames,
        "kills": kills,
        "bombs": bombs,
        "grenades": grenades_out,
        "round_bounds": [round_tick_min, round_tick_max],
        "tick_range": [frame_ticks[0], frame_ticks[-1]],
        "frame_count": len(frames),
    }
@app.post("/api/demo/{demo_id}/coaching/{player_name:path}")
def coaching(demo_id: str, player_name: str):
    s, _ = _parsed(demo_id)
    analyses = s.get("analyses", {})
    if player_name not in analyses:
        raise HTTPException(400, detail=f"Player '{player_name}' not analyzed yet")
    try:
        report = get_coaching(analyses[player_name])
        return {"report": report, "player": player_name}
    except Exception as exc:
        raise HTTPException(500, detail=str(exc))


@app.post("/api/demo/{demo_id}/scouting/{target_team}")
def scouting(demo_id: str, target_team: str):
    s, parsed = _parsed(demo_id)
    if "team_analysis" not in s:
        s["team_analysis"] = analyze_team(parsed)
    try:
        report = get_scouting_report(s["team_analysis"], target_team=target_team)
        return {"report": report, "team": target_team}
    except Exception as exc:
        raise HTTPException(500, detail=str(exc))


# ── In-game playback automation ──────────────────────────────────────────────

@app.get("/api/ingame/status")
def ingame_status():
    """Check CS2 installation and process status."""
    from src.cs2_controller import CS2Controller
    ctrl = CS2Controller()
    try:
        return ctrl.get_diagnostics()
    finally:
        ctrl.close()


@app.post("/api/demo/{demo_id}/ingame/prepare/{clip_plan_id}")
def ingame_prepare_playback(demo_id: str, clip_plan_id: str, payload: dict | None = None):
    """Prepare CS2 demo playback for a clip plan.

    This triggers the local CS2 automation to:
      - ensure CS2 is running
      - load the demo
      - seek to the clip window
      - apply the requested camera strategy

    It does NOT start recording — that is a future phase.
    """
    from src.cs2_playback import build_playback_job, prepare_playback, validate_playback_job

    s, parsed = _parsed(demo_id)
    clip_plan = _find_clip_plan(parsed, clip_plan_id)
    if not clip_plan:
        raise HTTPException(404, detail=f"Clip plan not found: {clip_plan_id}")

    # Resolve demo path
    demo_path = s.get("path", "")
    if not demo_path or not Path(demo_path).is_file():
        raise HTTPException(400, detail="Demo file not available in session")

    payload = payload or {}
    camera_overrides = payload.get("camera_overrides") if isinstance(payload.get("camera_overrides"), dict) else None
    skip_launch = bool(payload.get("skip_launch", False))
    config_overrides = payload.get("config") if isinstance(payload.get("config"), dict) else None

    job = build_playback_job(demo_path, clip_plan, camera_overrides=camera_overrides)

    # Pre-validate and return early if demo is missing
    pre_warnings = validate_playback_job(job)
    blocking = [w for w in pre_warnings if "not found" in w.lower() and "demo" in w.lower()]
    if blocking:
        raise HTTPException(400, detail=f"Playback validation failed: {'; '.join(blocking)}")

    result = prepare_playback(job, config=config_overrides, skip_launch=skip_launch)
    return result.to_dict()


@app.get("/api/ingame/obs-status")
def obs_status():
    """Check OBS Studio connection status and readiness for capture."""
    from src.obs_controller import OBSController, OBSStatus, build_obs_config, validate_obs_config

    config = build_obs_config()
    validation = validate_obs_config(config)

    ctrl = OBSController(config)
    try:
        conn_status = ctrl.connect()
        if conn_status != OBSStatus.CONNECTED:
            return {
                "connected": False,
                "status": conn_status.value,
                "config_validation": validation,
                "diagnostics": None,
                "hint": "Ensure OBS Studio 28+ is running with WebSocket Server enabled (Tools → obs-websocket Settings).",
            }
        diag = ctrl.get_diagnostics()
        return {
            "connected": True,
            "status": "ready",
            "config_validation": validation,
            "diagnostics": diag,
        }
    except Exception as exc:
        return {
            "connected": False,
            "status": "error",
            "config_validation": validation,
            "diagnostics": None,
            "error": str(exc),
        }
    finally:
        ctrl.disconnect()


@app.get("/api/ingame/health")
def ingame_health(demo_id: str | None = None):
    """Comprehensive health check for local in-game capture readiness.

    Validates: platform, CS2 executable, CS2 process, OBS connection,
    demo file (if demo_id provided), output directory writability.

    Returns structured readiness: ready / partially_ready / blocked,
    plus a ready_for_capture boolean, per-component diagnostics, and
    the total time the check took.
    """
    import time as _time
    from src.capture_pipeline import validate_capture_environment
    from src.cs2_config import build_cs2_config
    from src.cs2_controller import CS2Controller
    from src.obs_controller import OBSController, OBSStatus, build_obs_config

    t0 = _time.monotonic()
    netcon_port = int(build_cs2_config().get("netcon_port") or 2121)

    demo_path = None
    if demo_id:
        try:
            s = _sess(demo_id)
            demo_path = s.get("path", "")
        except Exception:
            pass

    env_check = validate_capture_environment(
        demo_path=demo_path,
        output_dir=str(GENERATED_DIR / "clips"),
        check_obs_connection=True,
        check_cs2_process=True,
    )
    result = env_check.to_dict()

    # ── CS2 detailed diagnostics ──────────────────────────────────────────
    try:
        ctrl = CS2Controller()
        cs2_diag = ctrl.get_diagnostics()
        ctrl.close()
    except Exception as exc:
        cs2_diag = {"error": str(exc)}
    result["cs2_diagnostics"] = cs2_diag

    # ── OBS detailed diagnostics ──────────────────────────────────────────
    obs_diag: dict = {}
    try:
        obs_cfg = build_obs_config()
        obs_ctrl = OBSController(obs_cfg)
        conn = obs_ctrl.connect()
        if conn == OBSStatus.CONNECTED:
            obs_diag = obs_ctrl.get_diagnostics()
            obs_ctrl.disconnect()
        else:
            obs_diag = {"status": conn.value, "connected": False}
    except Exception as exc:
        obs_diag = {"error": str(exc)}
    result["obs_diagnostics"] = obs_diag

    next_actions: list[str] = []
    for blocker in result.get("blockers", []):
        text = str(blocker)
        if "CS2 executable" in text:
            next_actions.append("Set CS2_EXE in .env to your local cs2.exe path.")
        elif "netcon" in text.lower():
            next_actions.append(
                f"Launch CS2 with -usercon -netconport {netcon_port} and verify netcon can connect before rendering."
            )
        elif "OBS" in text:
            next_actions.append("Start OBS Studio and enable WebSocket Server (Tools -> obs-websocket Settings).")
        elif "Demo file" in text:
            next_actions.append("Upload and parse a demo before running in-game capture readiness for that demo.")
        elif "Output directory" in text:
            next_actions.append("Ensure outputs/generated/clips exists and is writable.")
        else:
            next_actions.append(text)

    for warning in result.get("warnings", []):
        w = str(warning)
        if "CS2 not running" in w:
            next_actions.append("Launch CS2 before capturing, or let the renderer auto-launch it.")
        elif "command transport" in w:
            next_actions.append(f"Add CS2 launch options: -usercon -netconport {netcon_port}")

    result["next_actions"] = sorted(set(next_actions))
    result["demo_context"] = {
        "demo_id": demo_id,
        "demo_path": demo_path,
    }
    result["check_duration_ms"] = round((_time.monotonic() - t0) * 1000, 1)
    return result


@app.get("/api/local/doctor")
def local_doctor(demo_id: str | None = None):
    """End-to-end local operator validation summary.

    Combines app health, in-game readiness, queue status, and clip integrity
    into one practical response for local setup validation.
    """
    app_health = _validate_environment()
    ingame = ingame_health(demo_id=demo_id)
    queue = _render_queue.get_status()
    integrity = scan_clip_integrity(GENERATED_DIR)
    stale_indexes = integrity.get("stale_indexes", [])

    blockers: list[str] = []
    if app_health.get("status") != "ok":
        blockers.extend([f"app:{name}" for name in app_health.get("required_failures", [])])
    if not bool(ingame.get("ready_for_capture", False)):
        blockers.extend([f"ingame:{b}" for b in ingame.get("blockers", [])])
    if isinstance(stale_indexes, list) and stale_indexes:
        blockers.append("clip_indexes:stale_index_files")

    overall_ready = len(blockers) == 0
    next_actions = []
    next_actions.extend(app_health.get("operator_next_steps", []))
    next_actions.extend(ingame.get("next_actions", []))
    if int(integrity.get("missing_video", 0)) > 0:
        next_actions.append("Run GET /api/clips/integrity and clean or re-render clips with missing files.")
    if int(queue.get("failed_count", 0)) > 0:
        next_actions.append("Retry failed queue jobs via /api/queue/retry-all-failed or clear completed/failed state.")

    return {
        "status": "ready" if overall_ready else "attention_required",
        "ready_for_local_use": overall_ready,
        "blockers": blockers,
        "next_actions": sorted(set(str(x) for x in next_actions if x)),
        "app_health": app_health,
        "ingame_health": {
            "readiness": ingame.get("readiness"),
            "ready_for_capture": ingame.get("ready_for_capture"),
            "blockers": ingame.get("blockers", []),
            "warnings": ingame.get("warnings", []),
        },
        "queue_summary": {
            "queue_size": queue.get("queue_size", 0),
            "active_count": queue.get("active_count", 0),
            "failed_count": queue.get("failed_count", 0),
            "completed_count": queue.get("completed_count", 0),
        },
        "clip_integrity": {
            "total_clips": integrity.get("total_clips", 0),
            "ok": integrity.get("ok", 0),
            "missing_video": integrity.get("missing_video", 0),
            "missing_thumbnail": integrity.get("missing_thumbnail", 0),
            "missing_artifact_metadata": integrity.get("missing_artifact_metadata", 0),
            "stale_queue_refs": integrity.get("stale_queue_refs", 0),
        },
    }


# ── Render queue endpoints ────────────────────────────────────────────────────


@app.get("/api/queue")
def queue_status():
    """Full queue status with all jobs."""
    return _render_queue.get_status()


@app.post("/api/queue/enqueue")
def queue_enqueue(payload: dict):
    """Enqueue a single render job.

    Body: {demo_id, clip_plan_id, render_mode?, target_settings?}
    """
    demo_id = str(payload.get("demo_id") or "")
    clip_plan_id = str(payload.get("clip_plan_id") or "")
    if not demo_id or not clip_plan_id:
        raise HTTPException(400, detail="demo_id and clip_plan_id required")

    session, parsed = _parsed(demo_id)
    clip_plan = _find_clip_plan(parsed, clip_plan_id)
    if not clip_plan:
        raise HTTPException(404, detail=f"Clip plan not found: {clip_plan_id}")

    render_mode, settings = _normalize_queue_render_request(payload)
    _validate_queue_plans_for_render(demo_id, [clip_plan], render_mode)
    job = _enqueue_render_job_for_plan(
        demo_id=demo_id,
        session=session,
        parsed=parsed,
        clip_plan=clip_plan,
        render_mode=render_mode,
        settings=settings,
    )
    return job.to_dict()


@app.post("/api/queue/enqueue-batch")
def queue_enqueue_batch(payload: dict):
    """Enqueue multiple render jobs at once.

    Body: {demo_id, clip_plan_ids: [...], render_mode?, target_settings?}
    OR:   {demo_id, mode: "top", count: N, render_mode?, target_settings?}
    OR:   {demo_id, mode: "all", render_mode?, target_settings?}
    """
    demo_id = str(payload.get("demo_id") or "")
    if not demo_id:
        raise HTTPException(400, detail="demo_id required")

    session, parsed = _parsed(demo_id)
    all_plans = parsed.get("clip_plans", []) or []
    snapshot_path = _persist_demo_snapshot(demo_id, session, parsed)
    render_mode, settings = _normalize_queue_render_request(payload)

    mode = str(payload.get("mode") or "selected")
    clip_plan_ids = payload.get("clip_plan_ids", [])

    if mode == "top":
        count = max(1, min(50, int(payload.get("count", 5))))
        sorted_plans = sorted(all_plans, key=lambda p: float(p.get("score", 0)), reverse=True)
        plans = sorted_plans[:count]
    elif mode == "all":
        plans = list(all_plans)
    elif mode == "failed":
        # Re-queue failed jobs instead
        retried = _render_queue.retry_all_failed()
        return {"retried_count": retried, "jobs": []}
    else:
        # "selected" — use clip_plan_ids
        if not isinstance(clip_plan_ids, list) or not clip_plan_ids:
            raise HTTPException(400, detail="clip_plan_ids required for selected mode")
        id_set = set(str(x) for x in clip_plan_ids)
        plans = [p for p in all_plans if str(p.get("clip_plan_id", "")) in id_set]

    if not plans:
        raise HTTPException(400, detail="No clip plans matched")

    compatible_plans, invalid_reasons = _partition_queue_plans_for_render(demo_id, plans, render_mode)
    if not compatible_plans:
        raise HTTPException(
            400,
            detail=f"Some clip plans cannot be queued for {render_mode}: {' | '.join(invalid_reasons[:4])}",
        )

    jobs = _render_queue.enqueue_batch(
        demo_id=demo_id,
        clip_plans=compatible_plans,
        render_mode=render_mode,
        target_settings=settings,
        demo_snapshot_path=snapshot_path,
    )
    return {
        "enqueued_count": len(jobs),
        "skipped_count": len(invalid_reasons),
        "skipped_reasons": invalid_reasons[:10],
        "jobs": [j.to_dict() for j in jobs],
    }


@app.post("/api/queue/cancel/{job_id}")
def queue_cancel(job_id: str):
    """Cancel a queued or running job."""
    job = _render_queue.cancel(job_id)
    if not job:
        raise HTTPException(404, detail=f"Job not found: {job_id}")
    return job.to_dict()


@app.post("/api/queue/cancel-all")
def queue_cancel_all():
    """Cancel all queued (pending) jobs."""
    count = _render_queue.cancel_all_queued()
    return {"cancelled_count": count}


@app.post("/api/queue/retry/{job_id}")
def queue_retry(job_id: str):
    """Retry a failed or cancelled job."""
    job = _render_queue.retry(job_id)
    if not job:
        raise HTTPException(404, detail=f"Job not found or not retryable: {job_id}")
    return job.to_dict()


@app.post("/api/queue/retry-all-failed")
def queue_retry_all_failed():
    """Retry all failed jobs."""
    count = _render_queue.retry_all_failed()
    return {"retried_count": count}


@app.post("/api/queue/clear-completed")
def queue_clear_completed():
    """Remove completed and cancelled jobs from the queue list."""
    count = _render_queue.clear_completed()
    return {"removed_count": count}


@app.post("/api/queue/clear-failed")
def queue_clear_failed():
    """Remove failed jobs from the queue list."""
    count = _render_queue.clear_failed()
    return {"removed_count": count}


@app.get("/api/queue/job/{job_id}")
def queue_job_detail(job_id: str):
    """Get a single queue job's details."""
    job = _render_queue.get_job(job_id)
    if not job:
        raise HTTPException(404, detail=f"Job not found: {job_id}")
    return job.to_dict()


@app.get("/api/queue/job/{job_id}/events")
def queue_job_events(job_id: str, limit: int = 200):
    """Get structured persistent event logs for a queue job."""
    job = _render_queue.get_job(job_id)
    if not job:
        raise HTTPException(404, detail=f"Job not found: {job_id}")
    return {
        "job_id": job_id,
        "events": _render_queue.get_job_events(job_id, limit=max(1, min(1000, int(limit)))),
    }

