
"""
replay.py
Interactive 2D replay helpers (Plotly).
"""

from __future__ import annotations

import base64
import io
import math
from bisect import bisect_left
from collections import defaultdict
from typing import Any

import plotly.graph_objects as go

from src.utils import _game_to_pixel, _load_radar_img


MAX_FRAMES_PER_ROUND = 320
KILL_MARKER_WINDOW_TICKS = 96
BOMB_MARKER_WINDOW_TICKS = 192
DEFAULT_TRAIL_FRAMES = 14

GRENADE_TYPE_COLORS = {
    "smoke": "#94a3b8",
    "flash": "#fde68a",
    "he_grenade": "#f97316",
    "molotov": "#ef4444",
    "incendiary": "#fb7185",
    "decoy": "#60a5fa",
    "unknown": "#34d399",
}

GRENADE_FLIGHT_TICKS = {
    "smoke": 105,
    "flash": 78,
    "he_grenade": 90,
    "molotov": 112,
    "incendiary": 112,
    "decoy": 95,
    "unknown": 96,
}

BOMB_EVENT_STYLE = {
    "plant_start": ("triangle-up", "#facc15"),
    "plant": ("star", "#eab308"),
    "defuse_start": ("triangle-down", "#38bdf8"),
    "defuse": ("star-diamond", "#0ea5e9"),
    "drop": ("x", "#ef4444"),
    "pickup": ("circle", "#22c55e"),
    "explode": ("hexagram", "#fb7185"),
    "unknown": ("diamond", "#a78bfa"),
}

SITE_LABELS_WORLD = {
    "de_mirage": {"A": (820.0, -300.0), "B": (-1630.0, 415.0)},
    "de_dust2": {"A": (1210.0, 2440.0), "B": (-1470.0, 2470.0)},
    "de_inferno": {"A": (1300.0, 740.0), "B": (460.0, 2650.0)},
    "de_nuke": {"A": (320.0, -380.0), "B": (-620.0, -1450.0)},
    "de_ancient": {"A": (1090.0, 420.0), "B": (-1210.0, 1240.0)},
    "de_anubis": {"A": (920.0, 2120.0), "B": (-1210.0, 2420.0)},
    "de_vertigo": {"A": (-380.0, -690.0), "B": (-1460.0, 300.0)},
}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_side(side_val: Any) -> str:
    side = str(side_val or "").strip().lower()
    if side in ("2", "t", "terrorist"):
        return "T"
    if side in ("3", "ct", "counter-terrorist", "counterterrorist"):
        return "CT"
    return str(side_val or "").upper()


def _round_num(row: dict[str, Any]) -> int:
    return _safe_int(row.get("round_num", row.get("round", 0)), 0)


def _img_to_data_uri(img) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _unit_vector(dx: float, dy: float, fallback: tuple[float, float] = (1.0, 0.0)) -> tuple[float, float]:
    norm = math.sqrt(dx * dx + dy * dy)
    if norm < 1e-6:
        return fallback
    return dx / norm, dy / norm


def _build_round_tracks(player_positions: list[dict[str, Any]]):
    tracks = defaultdict(lambda: defaultdict(list))
    bounds = {}

    for row in player_positions:
        rn = _round_num(row)
        if rn <= 0:
            continue

        player = row.get("player_name")
        tick = _safe_int(row.get("tick"), 0)
        x = _safe_float(row.get("x"))
        y = _safe_float(row.get("y"))
        if not player or x is None or y is None:
            continue

        tracks[rn][player].append(
            {
                "tick": tick,
                "x": x,
                "y": y,
                "side": _normalize_side(row.get("side")),
                "yaw": _safe_float(row.get("yaw")),
                "hp": _safe_float(row.get("hp")),
                "armor": _safe_float(row.get("armor")),
            }
        )

    for rn, players in tracks.items():
        min_tick = None
        max_tick = None
        for _, path in players.items():
            path.sort(key=lambda r: r["tick"])
            if path:
                p_min = path[0]["tick"]
                p_max = path[-1]["tick"]
                min_tick = p_min if min_tick is None else min(min_tick, p_min)
                max_tick = p_max if max_tick is None else max(max_tick, p_max)

        if min_tick is None:
            min_tick = 0
        if max_tick is None:
            max_tick = min_tick
        bounds[rn] = (min_tick, max_tick)

    return tracks, bounds


def _extract_round_bounds_from_meta(round_rows: list[dict[str, Any]]) -> dict[int, tuple[int, int]]:
    bounds = {}
    for row in round_rows:
        rn = _round_num(row)
        if rn <= 0:
            continue

        start = _safe_int(row.get("start"), 0)
        freeze_end = _safe_int(row.get("freeze_end"), 0)
        end = _safe_int(row.get("official_end"), 0)
        if end <= 0:
            end = _safe_int(row.get("end"), 0)

        if start <= 0:
            start = freeze_end
        if start > 0 and end >= start:
            bounds[rn] = (start, end)
    return bounds


def _infer_round_by_tick(tick: int, round_bounds_sorted: list[tuple[int, int, int]]) -> int:
    for rn, start, end in round_bounds_sorted:
        if start <= tick <= end:
            return rn
    # Do not force-map out-of-bound events to nearest round;
    # this can shift events early/late around round boundaries.
    return 0


def _extract_grenade_events(
    grenades: list[dict[str, Any]],
    round_bounds: dict[int, tuple[int, int]],
) -> dict[int, list[dict[str, Any]]]:
    events = defaultdict(list)
    sorted_bounds = sorted((rn, b[0], b[1]) for rn, b in round_bounds.items())

    for g in grenades:
        tick = _safe_int(g.get("tick"), 0)
        if tick <= 0:
            continue

        gtype = str(g.get("grenade_type", "unknown") or "unknown").lower()
        thrower = g.get("thrower_name", "?")
        rn = _round_num(g)
        if rn <= 0:
            rn = _infer_round_by_tick(tick, sorted_bounds)
        if rn <= 0:
            continue

        raw_path = g.get("nade_path", [])
        path = []
        if isinstance(raw_path, list):
            for pt in raw_path:
                if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                    px = _safe_float(pt[0])
                    py = _safe_float(pt[1])
                    if px is not None and py is not None:
                        path.append((px, py))

        start_x = _safe_float(g.get("nade_start_x"))
        start_y = _safe_float(g.get("nade_start_y"))
        end_x = _safe_float(g.get("nade_end_x", g.get("nade_x")))
        end_y = _safe_float(g.get("nade_end_y", g.get("nade_y")))

        if not path:
            if start_x is not None and start_y is not None:
                path.append((start_x, start_y))
            if end_x is not None and end_y is not None:
                path.append((end_x, end_y))

        if len(path) == 1:
            path.append(path[0])

        if not path:
            continue

        events[rn].append(
            {
                "tick": tick,
                "type": gtype,
                "thrower": thrower,
                "path": path,
                "end": (end_x, end_y) if end_x is not None and end_y is not None else path[-1],
            }
        )

    for rn in list(events.keys()):
        events[rn].sort(key=lambda x: x["tick"])

    return events


def _extract_bomb_events(
    bomb_events: list[dict[str, Any]],
    round_bounds: dict[int, tuple[int, int]],
) -> dict[int, list[dict[str, Any]]]:
    events = defaultdict(list)
    sorted_bounds = sorted((rn, b[0], b[1]) for rn, b in round_bounds.items())

    for ev in bomb_events:
        tick = _safe_int(ev.get("tick"), 0)
        if tick <= 0:
            continue

        rn = _round_num(ev)
        if rn <= 0:
            rn = _infer_round_by_tick(tick, sorted_bounds)
        if rn <= 0:
            continue

        event_name = str(ev.get("event", "unknown") or "unknown").lower()
        player = ev.get("player_name", ev.get("player", "?"))
        x = _safe_float(ev.get("x"))
        y = _safe_float(ev.get("y"))

        events[rn].append(
            {
                "tick": tick,
                "event": event_name,
                "player": player,
                "x": x,
                "y": y,
            }
        )

    for rn in list(events.keys()):
        events[rn].sort(key=lambda x: x["tick"])

    return events

def _interpolate_player_state(
    track: list[dict[str, Any]],
    track_ticks: list[int],
    tick: int,
    fallback_dir: tuple[float, float],
) -> dict[str, Any] | None:
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

    yaw = p1.get("yaw")
    if yaw is None:
        yaw = p2.get("yaw")

    if yaw is not None:
        rad = math.radians(yaw)
        dx = math.cos(rad)
        dy = -math.sin(rad)
    else:
        dx = p2["x"] - p1["x"]
        dy = -(p2["y"] - p1["y"])

    dir_x, dir_y = _unit_vector(dx, dy, fallback_dir)

    hp = p2.get("hp")
    if hp is None:
        hp = p1.get("hp")
    armor = p2.get("armor")
    if armor is None:
        armor = p1.get("armor")

    return {
        "x": x,
        "y": y,
        "side": p2.get("side", p1.get("side", "")),
        "dir_x": dir_x,
        "dir_y": dir_y,
        "hp": hp,
        "armor": armor,
    }


def _build_grenade_frame_layers(
    grenade_events: list[dict[str, Any]],
    frame_tick: int,
    map_name: str,
) -> tuple[list[tuple[float, float]], list[dict[str, Any]], list[dict[str, Any]]]:
    path_points = []
    flying_markers = []
    impact_markers = []

    for g in grenade_events:
        throw_tick = g["tick"]
        if frame_tick < throw_tick:
            continue

        gtype = g.get("type", "unknown")
        path = g.get("path", [])
        if not path:
            continue

        flight_ticks = GRENADE_FLIGHT_TICKS.get(gtype, GRENADE_FLIGHT_TICKS["unknown"])
        elapsed = frame_tick - throw_tick
        progress = min(1.0, elapsed / max(flight_ticks, 1))
        idx = int(round(progress * (len(path) - 1)))
        idx = max(0, min(idx, len(path) - 1))

        shown = path[: idx + 1]
        pix_points = []
        for wx, wy in shown:
            px, py = _game_to_pixel(wx, wy, map_name)
            if px is not None and py is not None:
                pix_points.append((px, py))

        if len(pix_points) >= 2:
            path_points.extend(pix_points)
            path_points.append((None, None))

        curr_wx, curr_wy = path[idx]
        curr_px, curr_py = _game_to_pixel(curr_wx, curr_wy, map_name)
        if curr_px is not None and curr_py is not None and progress < 1.0:
            flying_markers.append(
                {
                    "x": curr_px,
                    "y": curr_py,
                    "type": gtype,
                    "thrower": g.get("thrower", "?"),
                }
            )

        if progress >= 1.0:
            end_x, end_y = g.get("end", path[-1])
            imp_px, imp_py = _game_to_pixel(end_x, end_y, map_name)
            if imp_px is not None and imp_py is not None:
                impact_markers.append(
                    {
                        "x": imp_px,
                        "y": imp_py,
                        "type": gtype,
                        "thrower": g.get("thrower", "?"),
                    }
                )

    return path_points, flying_markers, impact_markers


def build_replay_data(
    parsed_data: dict[str, Any],
    map_name: str,
    max_frames_per_round: int = MAX_FRAMES_PER_ROUND,
) -> dict[str, Any]:
    player_positions = parsed_data.get("player_positions", [])
    kills = parsed_data.get("kills", [])
    grenades = parsed_data.get("grenades", [])
    bomb_events = parsed_data.get("bomb_events", [])
    rounds_meta = parsed_data.get("rounds", [])

    tracks_by_round, round_bounds = _build_round_tracks(player_positions)
    meta_round_bounds = _extract_round_bounds_from_meta(rounds_meta)

    event_round_bounds = dict(round_bounds)
    event_round_bounds.update(meta_round_bounds)
    grenade_by_round = _extract_grenade_events(grenades, event_round_bounds)
    bomb_by_round = _extract_bomb_events(bomb_events, event_round_bounds)

    kills_by_round = defaultdict(list)
    for ev in kills:
        rn = _round_num(ev)
        if rn <= 0:
            continue
        kills_by_round[rn].append(ev)

    rounds = {}

    for rn in sorted(tracks_by_round.keys()):
        players_tracks = tracks_by_round.get(rn, {})
        if not players_tracks:
            continue

        round_start, round_end = meta_round_bounds.get(rn, round_bounds.get(rn, (0, 0)))
        if round_end < round_start:
            round_end = round_start

        span = max(round_end - round_start, 1)
        step = max(1, math.ceil(span / max(max_frames_per_round - 1, 1)))
        frame_ticks = list(range(round_start, round_end + 1, step))
        if frame_ticks[-1] != round_end:
            frame_ticks.append(round_end)

        rkills = sorted(kills_by_round.get(rn, []), key=lambda x: _safe_int(x.get("tick"), 0))
        rgrenades = grenade_by_round.get(rn, [])
        rbombs = bomb_by_round.get(rn, [])

        players = sorted(players_tracks.keys())
        player_track_ticks = {p: [pt["tick"] for pt in t] for p, t in players_tracks.items()}
        alive_state = {p: True for p in players}
        recent_kills: list[dict[str, Any]] = []
        recent_bomb_events: list[dict[str, Any]] = []
        last_dirs = {p: (1.0, 0.0) for p in players}

        kill_ptr = 0
        bomb_ptr = 0
        frames = []

        for frame_tick in frame_ticks:
            while kill_ptr < len(rkills) and _safe_int(rkills[kill_ptr].get("tick"), 0) <= frame_tick:
                ev = rkills[kill_ptr]
                victim = ev.get("victim_name")
                attacker = ev.get("attacker_name")
                if victim in alive_state:
                    alive_state[victim] = False

                vx = _safe_float(ev.get("victim_x", ev.get("victim_X")))
                vy = _safe_float(ev.get("victim_y", ev.get("victim_Y")))
                px = py = None
                if vx is not None and vy is not None:
                    px, py = _game_to_pixel(vx, vy, map_name)

                recent_kills.append(
                    {
                        "tick": _safe_int(ev.get("tick"), 0),
                        "x": px,
                        "y": py,
                        "attacker": attacker,
                        "victim": victim,
                    }
                )
                kill_ptr += 1

            while bomb_ptr < len(rbombs) and _safe_int(rbombs[bomb_ptr].get("tick"), 0) <= frame_tick:
                bev = rbombs[bomb_ptr]
                bx = by = None
                if bev.get("x") is not None and bev.get("y") is not None:
                    bx, by = _game_to_pixel(bev["x"], bev["y"], map_name)

                recent_bomb_events.append(
                    {
                        "tick": _safe_int(bev.get("tick"), 0),
                        "event": bev.get("event", "unknown"),
                        "player": bev.get("player", "?"),
                        "x": bx,
                        "y": by,
                    }
                )
                bomb_ptr += 1

            recent_kills = [
                k for k in recent_kills if frame_tick - _safe_int(k.get("tick"), 0) <= KILL_MARKER_WINDOW_TICKS
            ]
            recent_bomb_events = [
                b for b in recent_bomb_events if frame_tick - _safe_int(b.get("tick"), 0) <= BOMB_MARKER_WINDOW_TICKS
            ]

            frame_players = []
            for player in players:
                state = _interpolate_player_state(
                    players_tracks.get(player, []),
                    player_track_ticks.get(player, []),
                    frame_tick,
                    fallback_dir=last_dirs.get(player, (1.0, 0.0)),
                )
                if not state:
                    continue

                gx, gy = state["x"], state["y"]
                px, py = _game_to_pixel(gx, gy, map_name)
                if px is None or py is None:
                    continue

                dir_x = state["dir_x"]
                dir_y = state["dir_y"]
                last_dirs[player] = (dir_x, dir_y)
                angle = math.degrees(math.atan2(dir_y, dir_x)) + 90.0

                frame_players.append(
                    {
                        "name": player,
                        "side": state["side"],
                        "x": px,
                        "y": py,
                        "alive": bool(alive_state.get(player, True)),
                        "angle": angle,
                        "hp": state.get("hp"),
                        "armor": state.get("armor"),
                    }
                )

            path_points, flying_markers, impact_markers = _build_grenade_frame_layers(
                rgrenades,
                frame_tick,
                map_name,
            )

            frames.append(
                {
                    "tick": frame_tick,
                    "elapsed_s": round((frame_tick - round_start) / 64.0, 2),
                    "players": frame_players,
                    "kills": [k for k in recent_kills if k.get("x") is not None and k.get("y") is not None],
                    "grenade_paths": path_points,
                    "grenade_flying": flying_markers,
                    "grenade_impacts": impact_markers,
                    "bomb_events": [b for b in recent_bomb_events if b.get("x") is not None and b.get("y") is not None],
                }
            )

        rounds[rn] = {
            "frames": frames,
            "events": rkills,
            "start_tick": round_start,
            "end_tick": round_end,
            "duration_s": round((round_end - round_start) / 64.0, 2),
        }

    return {
        "map": map_name,
        "rounds": rounds,
    }

def _build_site_annotations(map_name: str) -> list[dict[str, Any]]:
    labels = SITE_LABELS_WORLD.get(map_name, {})
    annotations = []
    for site_name, (wx, wy) in labels.items():
        px, py = _game_to_pixel(wx, wy, map_name)
        if px is None or py is None:
            continue
        annotations.append(
            {
                "x": px,
                "y": py,
                "text": site_name,
                "showarrow": False,
                "font": {"size": 18, "color": "#e2e8f0", "family": "Trebuchet MS, Segoe UI, sans-serif"},
                "bgcolor": "rgba(15,23,42,0.65)",
                "bordercolor": "rgba(148,163,184,0.45)",
                "borderwidth": 1,
                "borderpad": 3,
            }
        )
    return annotations


def _build_trail_paths(
    round_frames: list[dict[str, Any]],
    frame_idx: int,
    side_filter: str,
    trail_frames: int,
    show_dead_players: bool,
) -> tuple[list[float], list[float], list[float], list[float]]:
    start_idx = max(0, frame_idx - max(trail_frames, 1) + 1)

    history: dict[str, list[tuple[float, float]]] = defaultdict(list)
    player_side: dict[str, str] = {}
    player_alive: dict[str, bool] = {}

    for idx in range(start_idx, frame_idx + 1):
        frame = round_frames[idx]
        for p in frame.get("players", []):
            name = p.get("name")
            if not name:
                continue
            history[name].append((p.get("x"), p.get("y")))
            player_side[name] = p.get("side", "")
            player_alive[name] = bool(p.get("alive", True))

    ct_x, ct_y, t_x, t_y = [], [], [], []

    for name, points in history.items():
        side = player_side.get(name)
        alive = player_alive.get(name, True)

        if side_filter in ("T", "CT") and side != side_filter:
            continue
        if not show_dead_players and not alive:
            continue
        if len(points) < 2:
            continue

        tx, ty = (ct_x, ct_y) if side == "CT" else (t_x, t_y)
        for px, py in points:
            tx.append(px)
            ty.append(py)
        tx.append(None)
        ty.append(None)

    return ct_x, ct_y, t_x, t_y


def _frame_traces(
    round_frames: list[dict[str, Any]],
    frame_idx: int,
    side_filter: str = "ALL",
    show_labels: bool = True,
    show_direction: bool = True,
    show_grenades: bool = True,
    show_kills: bool = True,
    show_dead_players: bool = True,
    show_trails: bool = False,
    trail_frames: int = DEFAULT_TRAIL_FRAMES,
    show_bomb_events: bool = True,
) -> list[go.Scatter]:
    frame_idx = max(0, min(frame_idx, len(round_frames) - 1))
    frame = round_frames[frame_idx]

    ct_alive_x, ct_alive_y, ct_alive_t = [], [], []
    ct_dead_x, ct_dead_y, ct_dead_t = [], [], []
    t_alive_x, t_alive_y, t_alive_t = [], [], []
    t_dead_x, t_dead_y, t_dead_t = [], [], []

    ct_dir_x, ct_dir_y, ct_dir_a, ct_dir_t = [], [], [], []
    t_dir_x, t_dir_y, t_dir_a, t_dir_t = [], [], [], []

    for p in frame.get("players", []):
        side = p.get("side")
        if side_filter in ("T", "CT") and side != side_filter:
            continue

        x = p.get("x")
        y = p.get("y")
        name = p.get("name", "")
        angle = p.get("angle", 0.0)
        hp = p.get("hp")
        armor = p.get("armor")

        hp_str = "-" if hp is None else str(int(round(hp)))
        ar_str = "-" if armor is None else str(int(round(armor)))
        hover = f"{name} | HP {hp_str} | AR {ar_str}"

        if side == "CT":
            if p.get("alive"):
                ct_alive_x.append(x)
                ct_alive_y.append(y)
                ct_alive_t.append(hover)
                ct_dir_x.append(x)
                ct_dir_y.append(y)
                ct_dir_a.append(angle)
                ct_dir_t.append(name)
            elif show_dead_players:
                ct_dead_x.append(x)
                ct_dead_y.append(y)
                ct_dead_t.append(hover)
        elif side == "T":
            if p.get("alive"):
                t_alive_x.append(x)
                t_alive_y.append(y)
                t_alive_t.append(hover)
                t_dir_x.append(x)
                t_dir_y.append(y)
                t_dir_a.append(angle)
                t_dir_t.append(name)
            elif show_dead_players:
                t_dead_x.append(x)
                t_dead_y.append(y)
                t_dead_t.append(hover)

    frame_tick = _safe_int(frame.get("tick"), 0)

    kill_x, kill_y, kill_t, kill_opacity = [], [], [], []
    if show_kills:
        for k in frame.get("kills", []):
            age = frame_tick - _safe_int(k.get("tick"), frame_tick)
            alpha = max(0.2, 1.0 - (age / max(KILL_MARKER_WINDOW_TICKS, 1)))
            kill_x.append(k.get("x"))
            kill_y.append(k.get("y"))
            kill_t.append(f"{k.get('attacker', '?')} -> {k.get('victim', '?')}")
            kill_opacity.append(alpha)

    gp = frame.get("grenade_paths", []) if show_grenades else []
    gp_x = [p[0] for p in gp]
    gp_y = [p[1] for p in gp]

    gf = frame.get("grenade_flying", []) if show_grenades else []
    gf_x = [g.get("x") for g in gf]
    gf_y = [g.get("y") for g in gf]
    gf_c = [GRENADE_TYPE_COLORS.get(g.get("type", "unknown"), GRENADE_TYPE_COLORS["unknown"]) for g in gf]
    gf_t = [f"{g.get('type', 'nade')} ({g.get('thrower', '?')})" for g in gf]

    gi = frame.get("grenade_impacts", []) if show_grenades else []
    gi_x = [g.get("x") for g in gi]
    gi_y = [g.get("y") for g in gi]
    gi_c = [GRENADE_TYPE_COLORS.get(g.get("type", "unknown"), GRENADE_TYPE_COLORS["unknown"]) for g in gi]
    gi_t = [f"impact: {g.get('type', 'nade')} ({g.get('thrower', '?')})" for g in gi]

    bomb_x, bomb_y, bomb_t, bomb_sym, bomb_col = [], [], [], [], []
    if show_bomb_events:
        for b in frame.get("bomb_events", []):
            ev = str(b.get("event", "unknown") or "unknown").lower()
            symbol, color = BOMB_EVENT_STYLE.get(ev, BOMB_EVENT_STYLE["unknown"])
            bomb_x.append(b.get("x"))
            bomb_y.append(b.get("y"))
            bomb_t.append(f"bomb: {ev} ({b.get('player', '?')})")
            bomb_sym.append(symbol)
            bomb_col.append(color)

    ct_trail_x, ct_trail_y, t_trail_x, t_trail_y = [], [], [], []
    if show_trails:
        ct_trail_x, ct_trail_y, t_trail_x, t_trail_y = _build_trail_paths(
            round_frames,
            frame_idx,
            side_filter,
            trail_frames,
            show_dead_players,
        )

    text_mode = "markers+text" if show_labels else "markers"
    ct_labels = [_short_label(t.split(" | ")[0]) for t in ct_alive_t] if show_labels else None
    t_labels = [_short_label(t.split(" | ")[0]) for t in t_alive_t] if show_labels else None
    kill_size = [max(8.0, 17.0 - (1.0 - op) * 9.0) for op in kill_opacity] if kill_opacity else 16

    return [
        go.Scatter(
            x=ct_trail_x,
            y=ct_trail_y,
            mode="lines",
            name="CT Trail",
            line={"width": 2, "color": "rgba(59,130,246,0.34)"},
            hoverinfo="skip",
            showlegend=False,
        ),
        go.Scatter(
            x=t_trail_x,
            y=t_trail_y,
            mode="lines",
            name="T Trail",
            line={"width": 2, "color": "rgba(249,115,22,0.34)"},
            hoverinfo="skip",
            showlegend=False,
        ),
        go.Scatter(
            x=ct_alive_x,
            y=ct_alive_y,
            mode=text_mode,
            text=ct_labels,
            textposition="top center",
            textfont={"size": 10, "color": "#dbeafe"},
            name="CT Alive",
            marker={"size": 15, "color": "#3b82f6", "line": {"width": 1.8, "color": "#f8fafc"}},
            hovertext=ct_alive_t,
            hovertemplate="%{hovertext}<extra>CT alive</extra>",
        ),
        go.Scatter(
            x=t_alive_x,
            y=t_alive_y,
            mode=text_mode,
            text=t_labels,
            textposition="top center",
            textfont={"size": 10, "color": "#ffedd5"},
            name="T Alive",
            marker={"size": 15, "color": "#f97316", "line": {"width": 1.8, "color": "#fff7ed"}},
            hovertext=t_alive_t,
            hovertemplate="%{hovertext}<extra>T alive</extra>",
        ),
        go.Scatter(
            x=ct_dead_x if show_dead_players else [],
            y=ct_dead_y if show_dead_players else [],
            mode="markers",
            name="CT Dead",
            marker={"size": 10, "color": "#93c5fd", "opacity": 0.28, "line": {"width": 1.0, "color": "#dbeafe"}},
            hovertext=ct_dead_t,
            hovertemplate="%{hovertext}<extra>CT dead</extra>",
        ),
        go.Scatter(
            x=t_dead_x if show_dead_players else [],
            y=t_dead_y if show_dead_players else [],
            mode="markers",
            name="T Dead",
            marker={"size": 10, "color": "#fdba74", "opacity": 0.28, "line": {"width": 1.0, "color": "#ffedd5"}},
            hovertext=t_dead_t,
            hovertemplate="%{hovertext}<extra>T dead</extra>",
        ),
        go.Scatter(
            x=ct_dir_x if show_direction else [],
            y=ct_dir_y if show_direction else [],
            mode="markers",
            name="CT Direction",
            marker={"symbol": "triangle-up", "size": 11, "color": "#bfdbfe", "line": {"width": 0.6, "color": "#1e3a8a"}, "angle": ct_dir_a if show_direction else []},
            hovertext=ct_dir_t,
            hovertemplate="%{hovertext}<extra>CT direction</extra>",
            showlegend=False,
        ),
        go.Scatter(
            x=t_dir_x if show_direction else [],
            y=t_dir_y if show_direction else [],
            mode="markers",
            name="T Direction",
            marker={"symbol": "triangle-up", "size": 11, "color": "#fed7aa", "line": {"width": 0.6, "color": "#7c2d12"}, "angle": t_dir_a if show_direction else []},
            hovertext=t_dir_t,
            hovertemplate="%{hovertext}<extra>T direction</extra>",
            showlegend=False,
        ),
        go.Scatter(
            x=kill_x if show_kills else [],
            y=kill_y if show_kills else [],
            mode="markers",
            name="Kills",
            marker={"size": kill_size, "symbol": "x", "color": "#ef4444", "line": {"width": 1.2, "color": "#ffe4e6"}, "opacity": kill_opacity if kill_opacity else 1.0},
            hovertext=kill_t,
            hovertemplate="%{hovertext}<extra>Kill</extra>",
        ),
        go.Scatter(
            x=gp_x if show_grenades else [],
            y=gp_y if show_grenades else [],
            mode="lines",
            name="Nade Path",
            line={"width": 2.0, "color": "#a5f3fc"},
            opacity=0.48,
            hoverinfo="skip",
            showlegend=show_grenades,
        ),
        go.Scatter(
            x=gf_x if show_grenades else [],
            y=gf_y if show_grenades else [],
            mode="markers",
            name="Nades (Flying)",
            marker={"size": 9, "symbol": "diamond", "color": gf_c, "line": {"width": 0.7, "color": "#f8fafc"}},
            hovertext=gf_t,
            hovertemplate="%{hovertext}<extra>Grenade</extra>",
            showlegend=show_grenades,
        ),
        go.Scatter(
            x=gi_x if show_grenades else [],
            y=gi_y if show_grenades else [],
            mode="markers",
            name="Nades (Impact)",
            marker={"size": 10, "symbol": "hexagram", "color": gi_c, "line": {"width": 0.7, "color": "#f8fafc"}, "opacity": 0.82},
            hovertext=gi_t,
            hovertemplate="%{hovertext}<extra>Grenade impact</extra>",
            showlegend=show_grenades,
        ),
        go.Scatter(
            x=bomb_x if show_bomb_events else [],
            y=bomb_y if show_bomb_events else [],
            mode="markers",
            name="Bomb Events",
            marker={"size": 13, "symbol": bomb_sym if bomb_sym else "diamond", "color": bomb_col if bomb_col else "#a78bfa", "line": {"width": 1.0, "color": "#f8fafc"}},
            hovertext=bomb_t,
            hovertemplate="%{hovertext}<extra>Bomb</extra>",
            showlegend=show_bomb_events,
        ),
    ]

def _fmt_mmss(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    mm = seconds // 60
    ss = seconds % 60
    return f"{mm:02d}:{ss:02d}"


def _short_label(name: str, limit: int = 10) -> str:
    s = str(name or "")
    return s if len(s) <= limit else (s[: limit - 1] + "…")


def get_round_frame_summary(
    replay_data: dict[str, Any],
    round_num: int,
    frame_idx: int,
) -> dict[str, Any]:
    round_data = replay_data.get("rounds", {}).get(round_num, {})
    frames = round_data.get("frames", [])
    if not frames:
        return {
            "round": round_num,
            "frame_idx": 0,
            "frame_count": 0,
            "tick": 0,
            "elapsed_s": 0.0,
            "duration_s": 0.0,
            "remaining_s": 0.0,
            "ct_alive": 0,
            "t_alive": 0,
            "ct_total": 0,
            "t_total": 0,
            "kills_visible": 0,
            "grenades_visible": 0,
            "bomb_events_visible": 0,
            "header": f"Round {round_num}",
        }

    idx = max(0, min(frame_idx, len(frames) - 1))
    frame = frames[idx]

    players = frame.get("players", [])
    ct_players = [p for p in players if p.get("side") == "CT"]
    t_players = [p for p in players if p.get("side") == "T"]
    ct_alive = sum(1 for p in ct_players if p.get("alive"))
    t_alive = sum(1 for p in t_players if p.get("alive"))

    elapsed_s = float(frame.get("elapsed_s", 0.0))
    duration_s = float(round_data.get("duration_s", 0.0))
    remaining_s = max(0.0, duration_s - elapsed_s)

    header = (
        f"Round {round_num} | {_fmt_mmss(elapsed_s)} / {_fmt_mmss(duration_s)} "
        f"| CT {ct_alive}v{t_alive} T | Tick {frame.get('tick', 0)}"
    )

    return {
        "round": round_num,
        "frame_idx": idx,
        "frame_count": len(frames),
        "tick": _safe_int(frame.get("tick"), 0),
        "elapsed_s": elapsed_s,
        "duration_s": duration_s,
        "remaining_s": remaining_s,
        "ct_alive": ct_alive,
        "t_alive": t_alive,
        "ct_total": len(ct_players),
        "t_total": len(t_players),
        "kills_visible": len(frame.get("kills", [])),
        "grenades_visible": len(frame.get("grenade_flying", [])) + len(frame.get("grenade_impacts", [])),
        "bomb_events_visible": len(frame.get("bomb_events", [])),
        "header": header,
    }


def render_replay_frame(
    replay_data: dict[str, Any],
    round_num: int,
    frame_idx: int,
    map_name: str,
    side_filter: str = "ALL",
    show_labels: bool = True,
    show_direction: bool = True,
    show_grenades: bool = True,
    show_kills: bool = True,
    show_dead_players: bool = True,
    show_trails: bool = False,
    trail_frames: int = DEFAULT_TRAIL_FRAMES,
    show_bomb_events: bool = True,
    show_sites: bool = True,
) -> go.Figure:
    round_data = replay_data.get("rounds", {}).get(round_num, {})
    frames = round_data.get("frames", [])

    fig = go.Figure()
    if not frames:
        fig.update_layout(title=f"Round {round_num} icin replay verisi yok")
        return fig

    idx = max(0, min(frame_idx, len(frames) - 1))

    radar = _load_radar_img(map_name, grid_size=1024)
    if radar is not None:
        fig.add_layout_image(
            dict(
                source=_img_to_data_uri(radar),
                x=0,
                y=1024,
                sizex=1024,
                sizey=1024,
                sizing="stretch",
                layer="below",
                xref="x",
                yref="y",
                xanchor="left",
                yanchor="top",
                opacity=0.96,
            )
        )

    traces = _frame_traces(
        round_frames=frames,
        frame_idx=idx,
        side_filter=side_filter,
        show_labels=show_labels,
        show_direction=show_direction,
        show_grenades=show_grenades,
        show_kills=show_kills,
        show_dead_players=show_dead_players,
        show_trails=show_trails,
        trail_frames=trail_frames,
        show_bomb_events=show_bomb_events,
    )
    for tr in traces:
        fig.add_trace(tr)

    annotations = _build_site_annotations(map_name) if show_sites else []

    fig.update_layout(
        xaxis={
            "range": [0, 1024],
            "showgrid": False,
            "visible": False,
            "zeroline": False,
            "fixedrange": True,
            "constrain": "domain",
        },
        yaxis={
            "range": [1024, 0],
            "showgrid": False,
            "visible": False,
            "zeroline": False,
            "fixedrange": True,
            "scaleanchor": "x",
            "scaleratio": 1,
        },
        height=830,
        margin={"l": 6, "r": 6, "t": 6, "b": 6},
        plot_bgcolor="#0b1220",
        paper_bgcolor="#0b1220",
        font={"color": "#e5e7eb", "family": "Trebuchet MS, Segoe UI, sans-serif"},
        legend={
            "orientation": "h",
            "yanchor": "top",
            "y": 1.0,
            "x": 0.0,
            "bgcolor": "rgba(2,6,23,0.58)",
            "bordercolor": "rgba(148,163,184,0.35)",
            "borderwidth": 1,
            "font": {"size": 11},
        },
        uirevision="replay-frame-v3",
        annotations=annotations,
    )
    return fig


def render_replay_animation(
    replay_data: dict[str, Any],
    round_num: int,
    map_name: str,
    side_filter: str = "ALL",
    show_labels: bool = True,
    show_direction: bool = True,
    show_grenades: bool = True,
    show_kills: bool = True,
    show_dead_players: bool = True,
    show_trails: bool = False,
    trail_frames: int = DEFAULT_TRAIL_FRAMES,
    show_bomb_events: bool = True,
    show_sites: bool = True,
    frame_duration_ms: int = 85,
) -> go.Figure:
    """
    Client-side animated replay figure.
    Avoids Streamlit rerun-based autoplay stutter by using Plotly frames.
    """
    round_data = replay_data.get("rounds", {}).get(round_num, {})
    frames = round_data.get("frames", [])

    fig = go.Figure()
    if not frames:
        fig.update_layout(title=f"Round {round_num} icin replay verisi yok")
        return fig

    radar = _load_radar_img(map_name, grid_size=1024)
    if radar is not None:
        fig.add_layout_image(
            dict(
                source=_img_to_data_uri(radar),
                x=0,
                y=1024,
                sizex=1024,
                sizey=1024,
                sizing="stretch",
                layer="below",
                xref="x",
                yref="y",
                xanchor="left",
                yanchor="top",
                opacity=0.96,
            )
        )

    initial_traces = _frame_traces(
        round_frames=frames,
        frame_idx=0,
        side_filter=side_filter,
        show_labels=show_labels,
        show_direction=show_direction,
        show_grenades=show_grenades,
        show_kills=show_kills,
        show_dead_players=show_dead_players,
        show_trails=show_trails,
        trail_frames=trail_frames,
        show_bomb_events=show_bomb_events,
    )
    for tr in initial_traces:
        fig.add_trace(tr)

    trace_idx = list(range(len(initial_traces)))
    frame_defs = []
    step_defs = []
    for idx in range(len(frames)):
        frame_traces = _frame_traces(
            round_frames=frames,
            frame_idx=idx,
            side_filter=side_filter,
            show_labels=show_labels,
            show_direction=show_direction,
            show_grenades=show_grenades,
            show_kills=show_kills,
            show_dead_players=show_dead_players,
            show_trails=show_trails,
            trail_frames=trail_frames,
            show_bomb_events=show_bomb_events,
        )
        frame_defs.append(go.Frame(name=str(idx), data=frame_traces, traces=trace_idx))

        tick = _safe_int(frames[idx].get("tick"), 0)
        elapsed = float(frames[idx].get("elapsed_s", 0.0))
        label = _fmt_mmss(elapsed) if (idx % 10 == 0 or idx == len(frames) - 1) else ""
        step_defs.append(
            {
                "method": "animate",
                "label": label,
                "args": [
                    [str(idx)],
                    {
                        "mode": "immediate",
                        "frame": {"duration": frame_duration_ms, "redraw": True},
                        "transition": {"duration": 0},
                    },
                ],
                "value": str(tick),
            }
        )

    fig.frames = frame_defs

    annotations = _build_site_annotations(map_name) if show_sites else []
    fig.update_layout(
        xaxis={
            "range": [0, 1024],
            "showgrid": False,
            "visible": False,
            "zeroline": False,
            "fixedrange": True,
            "constrain": "domain",
        },
        yaxis={
            "range": [1024, 0],
            "showgrid": False,
            "visible": False,
            "zeroline": False,
            "fixedrange": True,
            "scaleanchor": "x",
            "scaleratio": 1,
        },
        height=860,
        margin={"l": 6, "r": 6, "t": 6, "b": 6},
        plot_bgcolor="#0b1220",
        paper_bgcolor="#0b1220",
        font={"color": "#e5e7eb", "family": "Trebuchet MS, Segoe UI, sans-serif"},
        legend={
            "orientation": "h",
            "yanchor": "top",
            "y": 1.0,
            "x": 0.0,
            "bgcolor": "rgba(2,6,23,0.58)",
            "bordercolor": "rgba(148,163,184,0.35)",
            "borderwidth": 1,
            "font": {"size": 11},
        },
        annotations=annotations,
        updatemenus=[
            {
                "type": "buttons",
                "direction": "left",
                "x": 0.0,
                "y": 0.0,
                "xanchor": "left",
                "yanchor": "top",
                "pad": {"r": 8, "t": 40},
                "showactive": False,
                "buttons": [
                    {
                        "label": "Play",
                        "method": "animate",
                        "args": [
                            None,
                            {
                                "frame": {"duration": frame_duration_ms, "redraw": True},
                                "fromcurrent": True,
                                "transition": {"duration": 0},
                            },
                        ],
                    },
                    {
                        "label": "Pause",
                        "method": "animate",
                        "args": [
                            [None],
                            {
                                "mode": "immediate",
                                "frame": {"duration": 0, "redraw": False},
                                "transition": {"duration": 0},
                            },
                        ],
                    },
                ],
            }
        ],
        sliders=[
            {
                "active": 0,
                "x": 0.1,
                "len": 0.88,
                "y": -0.03,
                "xanchor": "left",
                "yanchor": "top",
                "pad": {"b": 6, "t": 22},
                "currentvalue": {"prefix": "Frame time: ", "font": {"size": 12}},
                "steps": step_defs,
            }
        ],
        uirevision="replay-animation-v1",
    )
    return fig


def render_replay(
    replay_data: dict[str, Any],
    round_num: int,
    map_name: str,
    side_filter: str = "ALL",
    show_labels: bool = True,
    show_direction: bool = True,
    show_grenades: bool = True,
    frame_duration_ms: int = 55,
) -> go.Figure:
    """
    Backward-compatible wrapper.
    Returns animated round replay figure.
    """
    return render_replay_animation(
        replay_data=replay_data,
        round_num=round_num,
        map_name=map_name,
        side_filter=side_filter,
        show_labels=show_labels,
        show_direction=show_direction,
        show_grenades=show_grenades,
        frame_duration_ms=frame_duration_ms,
    )
