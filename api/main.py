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

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
load_dotenv(dotenv_path=BASE_DIR / ".env")

from src.analyzer import analyze_player          # noqa: E402
from src.coach import get_coaching, get_scouting_report  # noqa: E402
from src.parser import parse_demo                # noqa: E402
from src.team_analyzer import analyze_team       # noqa: E402
from src.utils import (                          # noqa: E402
    create_round_route_gif,
    get_grenade_positions,
    get_player_movement_positions,
    plot_player_activity_map,
    plot_utility_map,
)

# â”€â”€ App setup â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

app = FastAPI(title="CS2 AI Coach", version="1.0.0")

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
REQUIRED_SCHEMA_VERSION = 9
STEAM_FAILURE_CACHE_TTL_SEC = 30


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
    try:
        sid = str(int(float(value)))
    except (TypeError, ValueError):
        return None
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

@app.post("/api/demo/upload")
async def upload_demo(file: UploadFile = File(...)):
    demo_id = uuid.uuid4().hex[:10]
    suffix = Path(file.filename or "demo.dem").suffix or ".dem"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    content = await file.read()
    tmp.write(content)
    tmp.close()
    _sessions[demo_id] = {
        "path": tmp.name,
        "filename": file.filename or "demo.dem",
        "size_mb": round(len(content) / 1_048_576, 1),
        "created_at": time.time(),
        "last_access": time.time(),
    }
    _cleanup_sessions()
    return {"demo_id": demo_id, "filename": file.filename, "size_mb": _sessions[demo_id]["size_mb"]}


@app.post("/api/demo/{demo_id}/parse")
def parse(demo_id: str):
    s = _sess(demo_id)
    try:
        parsed = _ensure_session_parsed_schema(s, min_schema=REQUIRED_SCHEMA_VERSION)
        player_steamids = _build_player_steamids(parsed)
        print(f"[parse] player_steamids resolved: {len(player_steamids)} players")
        for name, sid in player_steamids.items():
            print(f"    name={name!r}  steamid64={sid}")
        return {
            "map": parsed["map"],
            "total_rounds": parsed["total_rounds"],
            "players": parsed["players"],
            "player_steamids": player_steamids,
        }
    except Exception as exc:
        raise HTTPException(500, detail=str(exc))


@app.get("/api/demo/{demo_id}/info")
def demo_info(demo_id: str):
    s, p = _parsed(demo_id)
    return {
        "demo_id": demo_id,
        "filename": s.get("filename"),
        "map": p["map"],
        "total_rounds": p["total_rounds"],
        "players": p["players"],
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

