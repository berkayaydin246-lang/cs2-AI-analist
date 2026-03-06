"""
api/main.py
FastAPI backend — serves the frontend SPA and exposes CS2 analysis endpoints.

Run:
    uvicorn api.main:app --reload --port 8000
Then open http://localhost:8000
"""
from __future__ import annotations

import os
import sys
import tempfile
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from src.analyzer import analyze_player          # noqa: E402
from src.coach import get_coaching, get_scouting_report  # noqa: E402
from src.parser import parse_demo                # noqa: E402
from src.team_analyzer import analyze_team       # noqa: E402

# ── App setup ─────────────────────────────────────────────────────────────────

app = FastAPI(title="CS2 AI Coach", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIR = BASE_DIR / "frontend"
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

# In-memory session store: demo_id -> session dict
_sessions: dict[str, dict[str, Any]] = {}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sess(demo_id: str) -> dict:
    s = _sessions.get(demo_id)
    if not s:
        raise HTTPException(404, detail="Demo session not found")
    return s


def _parsed(demo_id: str) -> tuple[dict, dict]:
    s = _sess(demo_id)
    if "parsed_data" not in s:
        raise HTTPException(400, detail="Demo not parsed yet — POST /api/demo/{id}/parse first")
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


# ── Frontend ──────────────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
def serve_index():
    return FileResponse(str(FRONTEND_DIR / "index.html"))


# ── Demo lifecycle ────────────────────────────────────────────────────────────

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
    }
    return {"demo_id": demo_id, "filename": file.filename, "size_mb": _sessions[demo_id]["size_mb"]}


@app.post("/api/demo/{demo_id}/parse")
def parse(demo_id: str):
    s = _sess(demo_id)
    if "parsed_data" in s:
        p = s["parsed_data"]
        return {"map": p["map"], "total_rounds": p["total_rounds"], "players": p["players"]}
    try:
        parsed = parse_demo(s["path"])
        s["parsed_data"] = parsed
        return {"map": parsed["map"], "total_rounds": parsed["total_rounds"], "players": parsed["players"]}
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


# ── Radar image ───────────────────────────────────────────────────────────────

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


# ── Player analysis ───────────────────────────────────────────────────────────

@app.post("/api/demo/{demo_id}/analyze/{player_name:path}")
def analyze(demo_id: str, player_name: str):
    s, parsed = _parsed(demo_id)
    result = analyze_player(parsed, player_name)
    if "analyses" not in s:
        s["analyses"] = {}
    s["analyses"][player_name] = result
    return result


# ── Team analysis ─────────────────────────────────────────────────────────────

@app.get("/api/demo/{demo_id}/team")
def team(demo_id: str):
    s, parsed = _parsed(demo_id)
    if "team_analysis" not in s:
        s["team_analysis"] = analyze_team(parsed)
    return s["team_analysis"]


# ── Replay ────────────────────────────────────────────────────────────────────

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

    # ── Build frames ─────────────────────────────────────────────────────────
    by_tick: dict[int, list] = defaultdict(list)
    for pos in parsed.get("player_positions", []):
        if _safe_int(pos.get("round_num")) != round_num:
            continue
        x_raw = pos.get("x")
        y_raw = pos.get("y")
        # Skip entries with no valid coordinates
        if x_raw in (None, "", "nan") or y_raw in (None, "", "nan"):
            continue
        x_val = _safe(x_raw)
        y_val = _safe(y_raw)
        # Skip zero-coordinate entries (invalid/uninitialized positions)
        if x_val == 0.0 and y_val == 0.0:
            continue
        tick    = _safe_int(pos.get("tick"))
        hp_raw  = pos.get("hp")
        yaw_raw = pos.get("yaw")
        by_tick[tick].append({
            "name": str(pos.get("player_name") or ""),
            "x":    x_val,
            "y":    y_val,
            "side": _norm_side(pos.get("side")),
            "hp":   _safe(hp_raw, 100.0) if hp_raw not in (None, "", "nan") else 100.0,
            "yaw":  _safe(yaw_raw) if yaw_raw not in (None, "", "nan") else None,
        })

    ticks = sorted(by_tick.keys())
    if not ticks:
        return {"round": round_num, "map": map_name, "frames": [], "kills": [], "bombs": [], "frame_count": 0}

    # Sample to ≤ 300 frames
    MAX_FRAMES = 300
    if len(ticks) > MAX_FRAMES:
        step = max(1, len(ticks) // MAX_FRAMES)
        ticks = ticks[::step]

    frames = [{"tick": t, "players": by_tick[t]} for t in ticks]

    # ── Kills ─────────────────────────────────────────────────────────────────
    kills = []
    for k in parsed.get("kills", []):
        if _safe_int(k.get("round_num")) != round_num:
            continue
        vx = k.get("victim_x")
        vy = k.get("victim_y")
        kills.append({
            "tick": _safe_int(k.get("tick")),
            "attacker": str(k.get("attacker_name") or ""),
            "victim": str(k.get("victim_name") or ""),
            "weapon": str(k.get("weapon") or ""),
            "headshot": bool(k.get("headshot")),
            "victim_x": _safe(vx) if vx not in (None, "") else None,
            "victim_y": _safe(vy) if vy not in (None, "") else None,
            "victim_side": str(k.get("victim_side") or ""),
            "attacker_side": str(k.get("attacker_side") or ""),
        })

    # ── Bomb events ───────────────────────────────────────────────────────────
    bombs = []
    for b in parsed.get("bomb_events", []):
        if _safe_int(b.get("round_num")) != round_num:
            continue
        x = b.get("x")
        y = b.get("y")
        bombs.append({
            "tick": _safe_int(b.get("tick")),
            "event": str(b.get("event") or ""),
            "player": str(b.get("player_name") or ""),
            "x": _safe(x) if x not in (None, "") else None,
            "y": _safe(y) if y not in (None, "") else None,
        })

    # ── Grenade events ────────────────────────────────────────────────────────
    grenades_out = []
    seen_grenade_keys: set = set()
    for g in parsed.get("grenades", []):
        if _safe_int(g.get("round_num")) != round_num:
            continue
        tick    = _safe_int(g.get("tick"))
        gtype   = str(g.get("grenade_type") or "")
        thrower = str(g.get("thrower_name") or "")
        # Deduplicate by thrower + type + coarse tick bucket
        dedup_key = (thrower, gtype, tick // 64)
        if dedup_key in seen_grenade_keys:
            continue
        seen_grenade_keys.add(dedup_key)
        x = g.get("x")
        y = g.get("y")
        if x in (None, "") or y in (None, ""):
            continue
        grenades_out.append({
            "tick": tick,
            "type": gtype,
            "thrower": thrower,
            "x": _safe(x),
            "y": _safe(y),
        })

    return {
        "round": round_num,
        "map": map_name,
        "frames": frames,
        "kills": kills,
        "bombs": bombs,
        "grenades": grenades_out,
        "tick_range": [ticks[0], ticks[-1]],
        "frame_count": len(frames),
    }


# ── AI Coaching ───────────────────────────────────────────────────────────────

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
