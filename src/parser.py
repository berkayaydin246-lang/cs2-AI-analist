"""
parser.py
CS2 demo dosyasÄ±nÄ± okur ve ham veriyi yapÄ±landÄ±rÄ±lmÄ±ÅŸ formata Ã§evirir.
awpy 2.x API'sine gÃ¶re yazÄ±lmÄ±ÅŸtÄ±r.
"""

from awpy import Demo
import pandas as pd
import json
from datetime import datetime, timezone
from pathlib import Path

from src.highlights import detect_highlights
from src.clip_planner import build_clip_plans


SCHEMA_VERSION = 12
PARSER_VERSION = "1.2.0"




def parse_demo(demo_path: str) -> dict:
    """
    .dem dosyasini parse eder ve analiz icin gerekli veriyi cikarir.
    """
    ingestion = _ingest_demo(demo_path)
    frames = ingestion["frames"]
    map_name = ingestion["map_name"]
    total_rounds = ingestion["total_rounds"]

    print(f"[+] Harita      : {map_name}")
    print(f"[+] Round sayisi: {total_rounds}")
    print(f"[+] Kill sayisi : {len(frames['kills'])}")

    rounds = _normalize_round_records(_process_rounds(frames["rounds"]))
    round_lookup = _build_round_lookup(rounds)

    player_positions = _normalize_player_positions(_process_ticks(frames["ticks"]), round_lookup)
    kills_processed = _normalize_kill_records(_process_kills(frames["kills"]), round_lookup)
    damages_processed = _normalize_damage_records(_process_damages(frames["damages"]), round_lookup)
    grenades_processed = _normalize_grenade_records(_process_grenades(frames["grenades"]), round_lookup)
    bomb_events_processed = _normalize_bomb_event_records(_process_bomb_events(frames["bomb"]), round_lookup)
    shots_processed = _normalize_shot_records(_process_shots(frames["shots"]), round_lookup)
    player_identities = _build_player_identities(player_positions, kills=kills_processed)
    player_entities = _build_player_entities(player_positions, kills_processed, player_identities)
    generated_at = datetime.now(timezone.utc).isoformat()
    validation = _build_validation_summary(
        rounds=rounds,
        kills=kills_processed,
        damages=damages_processed,
        grenades=grenades_processed,
        bomb_events=bomb_events_processed,
        shots=shots_processed,
        player_positions=player_positions,
        players=player_entities,
        player_identities=player_identities,
    )

    result = {
        "schema_version": SCHEMA_VERSION,
        "parser_version": PARSER_VERSION,
        "generated_at": generated_at,
        "map": map_name,
        "total_rounds": total_rounds,
        "map_bounds": _extract_map_bounds(frames["ticks"]),
        "kills": kills_processed,
        "damages": damages_processed,
        "grenades": grenades_processed,
        "bomb_events": bomb_events_processed,
        "shots": shots_processed,
        "player_positions": player_positions,
        "player_identities": player_identities,
        "rounds": rounds,
        "players": [player["player_name"] for player in player_entities],
        "demo": {
            "path": ingestion["demo_path"],
            "filename": Path(ingestion["demo_path"]).name,
            "metadata": {
                "map_name": map_name,
                "tick_rate": ingestion["tick_rate"],
                "total_rounds": total_rounds,
                "schema_version": SCHEMA_VERSION,
                "parser_version": PARSER_VERSION,
                "generated_at": generated_at,
                "header": ingestion["header"],
            },
        },
        "entities": {
            "players": player_entities,
            "player_identities": player_identities,
        },
        "events": {
            "kills": kills_processed,
            "damages": damages_processed,
            "grenades": grenades_processed,
            "bomb_events": bomb_events_processed,
            "shots": shots_processed,
            "player_positions": player_positions,
        },
        "validation": validation,
    }

    highlight_bundle = detect_highlights(result)
    result["highlights"] = highlight_bundle.get("highlights", [])
    result["highlight_summary"] = {
        "schema_version": highlight_bundle.get("schema_version"),
        "generated_at": highlight_bundle.get("generated_at"),
        "total_highlights": highlight_bundle.get("total_highlights", 0),
        "counts_by_type": highlight_bundle.get("counts_by_type", {}),
        "counts_by_round": highlight_bundle.get("counts_by_round", {}),
        "warnings": highlight_bundle.get("warnings", []),
    }

    clip_plan_bundle = build_clip_plans(result)
    result["clip_plans"] = clip_plan_bundle.get("clip_plans", [])
    result["clip_plan_summary"] = {
        "schema_version": clip_plan_bundle.get("schema_version"),
        "generated_at": clip_plan_bundle.get("generated_at"),
        "total_clip_plans": clip_plan_bundle.get("total_clip_plans", 0),
        "counts_by_highlight_type": clip_plan_bundle.get("counts_by_highlight_type", {}),
        "counts_by_pov_mode": clip_plan_bundle.get("counts_by_pov_mode", {}),
        "warnings": clip_plan_bundle.get("warnings", []),
    }

    _log_parse_summary(result)
    return result



def _ingest_demo(demo_path: str) -> dict:
    normalized_path = str(Path(demo_path).expanduser().resolve())
    path = Path(normalized_path)
    if not path.exists():
        raise FileNotFoundError(f"Demo file not found: {normalized_path}")
    if path.suffix.lower() != ".dem":
        raise ValueError(f"Expected a .dem file, got: {path.name}")

    print(f"[+] Demo yukleniyor: {normalized_path}")
    demo = Demo(normalized_path)
    demo.parse()

    header = {}
    if hasattr(demo, "header") and demo.header:
        if isinstance(demo.header, dict):
            header = dict(demo.header)
        else:
            try:
                header = dict(demo.header)
            except Exception:
                header = {}

    map_name = str(header.get("map_name") or "unknown")
    tick_rate = _extract_tick_rate(header)
    frames = {
        "kills": _to_df(demo.kills if hasattr(demo, "kills") else None),
        "damages": _to_df(demo.damages if hasattr(demo, "damages") else None),
        "rounds": _to_df(demo.rounds if hasattr(demo, "rounds") else None),
        "grenades": _to_df(demo.grenades if hasattr(demo, "grenades") else None),
        "shots": _to_df(demo.shots if hasattr(demo, "shots") else None),
        "ticks": _to_df(demo.ticks if hasattr(demo, "ticks") else None),
        "bomb": _to_df(
            demo.bomb if hasattr(demo, "bomb") else (
                demo.bomb_events if hasattr(demo, "bomb_events") else None
            )
        ),
    }
    total_rounds = len(frames["rounds"]) if not frames["rounds"].empty else 0
    return {
        "demo_path": normalized_path,
        "header": header,
        "map_name": map_name,
        "tick_rate": tick_rate,
        "total_rounds": total_rounds,
        "frames": frames,
    }


def _to_df(val):
    if val is None:
        return pd.DataFrame()
    if isinstance(val, pd.DataFrame):
        return val
    try:
        import polars as pl
        if isinstance(val, pl.DataFrame):
            return val.to_pandas()
    except Exception:
        pass
    try:
        return pd.DataFrame(val)
    except Exception:
        return pd.DataFrame()


def _extract_tick_rate(header: dict) -> int | None:
    for key in ("tick_rate", "tickrate", "tick_interval", "tick_interval_ms"):
        value = header.get(key)
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if numeric <= 0:
            continue
        if "interval" in key:
            return int(round(1000.0 / numeric)) if numeric >= 1 else int(round(1.0 / numeric))
        return int(round(numeric))
    return None


def _safe_int(value, default: int | None = None) -> int | None:
    if value is None or value == "":
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _safe_float(value, default: float | None = None) -> float | None:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clean_text(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalize_side(value) -> str:
    side = _clean_text(value).upper()
    if side in {"CT", "COUNTERTERRORIST", "COUNTER-TERRORIST", "COUNTER_TERRORIST"}:
        return "CT"
    if side in {"T", "TERRORIST", "TERRORISTS"}:
        return "T"
    return side


def _coerce_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return _clean_text(value).lower() in {"1", "true", "yes", "y"}


def _build_round_lookup(rounds: list[dict]) -> list[dict]:
    lookup = []
    for round_info in rounds:
        round_num = _safe_int(round_info.get("round_num"), None)
        start_tick = _safe_int(round_info.get("start_tick"), None)
        end_tick = _safe_int(round_info.get("end_tick"), None)
        if round_num is None or start_tick is None or end_tick is None or end_tick < start_tick:
            continue
        lookup.append({"round_num": round_num, "start_tick": start_tick, "end_tick": end_tick})
    lookup.sort(key=lambda item: item["start_tick"])
    return lookup


def _assign_round_num(record: dict, round_lookup: list[dict]) -> int | None:
    existing = _safe_int(record.get("round_num"), None)
    if existing is not None and existing > 0:
        return existing
    tick = _safe_int(record.get("tick"), None)
    if tick is None:
        return None
    for round_info in round_lookup:
        if round_info["start_tick"] <= tick <= round_info["end_tick"]:
            return round_info["round_num"]
    return None


def _col(df: pd.DataFrame, *candidates):
    """DataFrame'de var olan ilk kolon adÄ±nÄ± dÃ¶ner."""
    for c in candidates:
        if c in df.columns:
            return c
    return None


def _process_kills(df: pd.DataFrame) -> list:
    if df is None or len(df) == 0:
        return []

    if not isinstance(df.columns[0], str):
        df = df.rename(columns={
            6: "victim_name",
            14: "attacker_name",
            41: "weapon",
            24: "headshot",
            8:  "victim_side",
            16: "attacker_side",
            32: "tick",
            1:  "victim_x",
            2:  "victim_y",
            46: "round_num",
        })

    else:
        rename_map = {}
        for col in df.columns:
            cl = str(col).lower()
            if cl in ("attacker_name", "attacker", "killer_name", "killer"):
                rename_map[col] = "attacker_name"
            elif cl in ("victim_name", "victim", "dead_player_name", "killed_name"):
                rename_map[col] = "victim_name"
            elif cl in ("assister_name", "assister", "assistant_name", "assist_player_name"):
                rename_map[col] = "assister_name"
            elif cl in ("weapon", "weapon_name"):
                rename_map[col] = "weapon"
            elif cl in ("headshot", "is_headshot", "head_shot"):
                rename_map[col] = "headshot"
            elif cl in ("attacker_side", "attacker_team", "killer_side", "killer_team"):
                rename_map[col] = "attacker_side"
            elif cl in ("victim_side", "victim_team"):
                rename_map[col] = "victim_side"
            elif cl in ("tick", "game_tick"):
                rename_map[col] = "tick"
            elif cl in ("round", "round_num"):
                rename_map[col] = "round_num"
            elif cl in ("victim_x", "victim_xpos", "victim_x_position"):
                rename_map[col] = "victim_x"
            elif cl in ("victim_y", "victim_ypos", "victim_y_position"):
                rename_map[col] = "victim_y"
            # awpy Polars can use uppercase coordinate fields
            elif col == "victim_X":
                rename_map[col] = "victim_x"
            elif col == "victim_Y":
                rename_map[col] = "victim_y"
            # SteamID64 per participant — used for identity mapping
            elif cl in ("attacker_steamid", "attacker_steam_id", "killer_steamid",
                        "attacker_id", "attacker_player_id"):
                rename_map[col] = "attacker_steamid"
            elif cl in ("victim_steamid", "victim_steam_id", "dead_steamid",
                        "victim_id", "victim_player_id"):
                rename_map[col] = "victim_steamid"
            elif cl in ("assister_steamid", "assister_steam_id", "assist_steamid"):
                rename_map[col] = "assister_steamid"
        if rename_map:
            df = df.rename(columns=rename_map)

    keep = [c for c in [
        "attacker_name", "victim_name", "assister_name", "weapon",
        "headshot", "attacker_side", "victim_side",
        "tick", "victim_x", "victim_y", "round_num",
        # SteamID64 columns — kept for identity resolution
        "attacker_steamid", "victim_steamid", "assister_steamid",
    ] if c in df.columns]
    return df[keep].fillna("").to_dict(orient="records")


def _process_damages(df: pd.DataFrame) -> list:
    if df is None or len(df) == 0:
        return []

    if not isinstance(df.columns[0], str):
        df = df.rename(columns={
            6:  "victim_name",
            21: "attacker_name",
            11: "hp_damage",
            24: "weapon",
            13: "hitgroup",
            1:  "victim_x",
            2:  "victim_y",
            16: "attacker_x",
            17: "attacker_y",
            15: "tick",
        })
    else:
        # Named columns — normalize field names for awpy 2.x variants
        rename_map = {}
        for col in df.columns:
            cl = str(col).lower()
            if cl in ("attacker_name", "attacker", "player_name"):
                rename_map[col] = "attacker_name"
            elif cl in ("victim_name", "victim"):
                rename_map[col] = "victim_name"
            elif cl in ("hp_damage", "hp_dmg", "damage_health", "damage_health_real",
                        "dmg_health", "dmg", "damage"):
                rename_map[col] = "hp_damage"
            elif cl in ("weapon", "weapon_name"):
                rename_map[col] = "weapon"
            elif cl in ("hitgroup", "hit_group"):
                rename_map[col] = "hitgroup"
            elif cl in ("tick", "game_tick"):
                rename_map[col] = "tick"
            elif cl in ("round", "round_num"):
                rename_map[col] = "round_num"
        if rename_map:
            df = df.rename(columns=rename_map)

    # Ensure hp_damage is numeric
    if "hp_damage" in df.columns:
        df["hp_damage"] = pd.to_numeric(df["hp_damage"], errors="coerce").fillna(0)

    keep = [c for c in ["attacker_name", "victim_name", "hp_damage", "weapon", "hitgroup",
                        "victim_x", "victim_y", "attacker_x", "attacker_y", "tick", "round_num"]
            if c in df.columns]
    return df[keep].fillna("").to_dict(orient="records")


def _process_shots(df: pd.DataFrame) -> list:
    if df is None or len(df) == 0:
        return []

    if not isinstance(df.columns[0], str):
        df = df.rename(columns={
            3:  "tick",
            4:  "shot_x",
            5:  "shot_y",
            9:  "shooter_name",
            11: "shooter_side",
            12: "weapon",
            13: "round_num",
        })
    else:
        rename_map = {}
        for col in df.columns:
            cl = str(col).lower()
            if cl in ("player_name", "shooter_name", "attacker_name", "name"):
                rename_map[col] = "shooter_name"
            elif cl in ("x", "shot_x"):
                rename_map[col] = "shot_x"
            elif cl in ("y", "shot_y"):
                rename_map[col] = "shot_y"
            elif cl in ("weapon", "weapon_name"):
                rename_map[col] = "weapon"
            elif cl in ("tick",):
                rename_map[col] = "tick"
            elif cl in ("round", "round_num"):
                rename_map[col] = "round_num"
            elif cl in ("side", "shooter_side"):
                rename_map[col] = "shooter_side"
        if rename_map:
            df = df.rename(columns=rename_map)

    keep = [c for c in ["shooter_name", "shooter_side", "weapon",
                        "tick", "round_num", "shot_x", "shot_y"]
            if c in df.columns]
    return df[keep].fillna("").to_dict(orient="records")


def _process_ticks(df: pd.DataFrame, sample_step: int = 8) -> list:
    """Oyuncu hareket verisini hafifletilmis sekilde cikarir."""
    if df is None or len(df) == 0:
        return []

    def _is_probable_steamid64_series(series: pd.Series) -> bool:
        vals = pd.to_numeric(series, errors="coerce").dropna()
        if len(vals) < 50:
            return False
        vals = vals.astype("int64")
        # SteamID64 values for players are typically in this broad range.
        in_range = (vals >= 76561100000000000) & (vals <= 76561399999999999)
        ratio = float(in_range.mean()) if len(vals) else 0.0
        return ratio >= 0.75 and int(vals.nunique()) >= 5

    if not isinstance(df.columns[0], str):
        rename_map = {
            2: "side",
            3: "x",
            4: "y",
            8: "player_name",
            9: "round_num",
        }
        if 0 in df.columns and _is_probable_steamid64_series(df[0]):
            rename_map[0] = "steamid"
        # Bazi schema'larda tick kolonu farkli bir integer index'te gelebilir.
        # Ilk bulunan kolonu almak steamid gibi alanlari yanlis secmeye neden olur.
        tick_col = None
        tick_score = None
        reserved = {2, 3, 4, 8, 9, 0}
        for col in df.columns:
            if col in reserved:
                continue
            series = pd.to_numeric(df[col], errors="coerce").dropna()
            if len(series) < 100:
                continue
            n_unique = int(series.nunique())
            if n_unique < 50:
                continue
            max_val = float(series.max())
            if max_val <= 0:
                continue
            # tick icin genelde genis range + yuksek unique degeri olur.
            score = max_val + (n_unique * 10.0)
            if tick_score is None or score > tick_score:
                tick_score = score
                tick_col = col
        if tick_col is not None:
            rename_map[tick_col] = "tick"
        df = df.rename(columns=rename_map)
    else:
        rename_map = {}
        for col in df.columns:
            cl = str(col).lower()
            if cl in ("player_name", "name", "player"):
                rename_map[col] = "player_name"
            elif cl in ("x", "player_x"):
                rename_map[col] = "x"
            elif cl in ("y", "player_y"):
                rename_map[col] = "y"
            elif cl in ("z", "player_z"):
                rename_map[col] = "z"
            elif cl in ("side",):
                rename_map[col] = "side"
            elif cl in ("round", "round_num"):
                rename_map[col] = "round_num"
            elif cl in ("tick", "game_tick", "tick_id", "gametick"):
                rename_map[col] = "tick"
            elif cl in ("yaw", "view_yaw", "eye_yaw", "eye_angle_y", "view_x"):
                rename_map[col] = "yaw"
            elif cl in ("hp", "health", "player_health"):
                rename_map[col] = "hp"
            elif cl in ("armor", "armour", "player_armor", "player_armour"):
                rename_map[col] = "armor"
            elif cl in ("steamid", "steam_id", "steamid64", "steam64", "steam_id64", "player_steamid"):
                rename_map[col] = "steamid"
        if rename_map:
            df = df.rename(columns=rename_map)

    needed = {"player_name", "x", "y"}
    if not needed.issubset(set(df.columns)):
        print(f"[!] Tick kolonlari bulunamadi: {list(df.columns)}")
        return []

    step = max(sample_step, 1)
    if "tick" in df.columns:
        df["tick"] = pd.to_numeric(df["tick"], errors="coerce")

    sampled_parts = []
    for _, player_df in df.groupby("player_name", sort=False):
        if "tick" in player_df.columns:
            player_df = player_df.sort_values("tick")
        sampled_parts.append(player_df.iloc[::step])

    sampled = pd.concat(sampled_parts, ignore_index=True) if sampled_parts else df.iloc[0:0].copy()
    sampled["x"] = pd.to_numeric(sampled["x"], errors="coerce")
    sampled["y"] = pd.to_numeric(sampled["y"], errors="coerce")
    if "z" in sampled.columns:
        sampled["z"] = pd.to_numeric(sampled["z"], errors="coerce")
    sampled = sampled.dropna(subset=["x", "y", "player_name"])

    if "round_num" in sampled.columns:
        sampled["round_num"] = pd.to_numeric(sampled["round_num"], errors="coerce")
    if "yaw" in sampled.columns:
        sampled["yaw"] = pd.to_numeric(sampled["yaw"], errors="coerce")
    if "hp" in sampled.columns:
        sampled["hp"] = pd.to_numeric(sampled["hp"], errors="coerce")
    if "armor" in sampled.columns:
        sampled["armor"] = pd.to_numeric(sampled["armor"], errors="coerce")
    if "steamid" in sampled.columns:
        sampled["steamid"] = pd.to_numeric(sampled["steamid"], errors="coerce").astype("Int64")

    # Tick yoksa veya tamamen bossa oyuncu bazli fallback timeline uret.
    if "tick" not in sampled.columns:
        sampled["tick"] = sampled.groupby("player_name").cumcount() * step
    else:
        sampled["tick"] = pd.to_numeric(sampled["tick"], errors="coerce")
        if sampled["tick"].isna().all():
            sampled["tick"] = sampled.groupby("player_name").cumcount() * step
        else:
            sampled["tick"] = sampled.groupby("player_name")["tick"].transform(
                lambda s: s.interpolate(limit_direction="both")
            )

    keep = [c for c in ["player_name", "steamid", "x", "y", "z", "side", "round_num", "tick", "yaw", "hp", "armor"] if c in sampled.columns]
    rows = sampled[keep].to_dict(orient="records")
    print(f"[+] Player positions (sampled): {len(rows)}")
    return rows


def _process_bomb_events(df: pd.DataFrame) -> list:
    """Bomb eventlerini parse eder (varsa). Yoksa bos liste doner."""
    if df is None or len(df) == 0:
        return []

    if not isinstance(df.columns[0], str):
        # Unknown integer schema: safely skip to preserve compatibility.
        return []

    rename_map = {}
    for col in df.columns:
        cl = str(col).lower()
        if cl in ("tick", "game_tick", "tick_id", "gametick"):
            rename_map[col] = "tick"
        elif cl in ("round", "round_num"):
            rename_map[col] = "round_num"
        elif cl in ("event", "action", "bomb_action", "type"):
            rename_map[col] = "event"
        elif cl in ("player", "player_name", "user_name", "name"):
            rename_map[col] = "player_name"
        elif cl in ("x", "bomb_x", "player_x", "site_x"):
            rename_map[col] = "x"
        elif cl in ("y", "bomb_y", "player_y", "site_y"):
            rename_map[col] = "y"
        elif cl in ("z", "bomb_z", "player_z", "site_z"):
            rename_map[col] = "z"
    if rename_map:
        df = df.rename(columns=rename_map)

    if "event" not in df.columns:
        return []

    def _norm_event(v: str) -> str:
        s = str(v or "").strip().lower()
        if "plant" in s and ("begin" in s or "start" in s):
            return "plant_start"
        if "plant" in s:
            return "plant"
        if "defus" in s and ("begin" in s or "start" in s):
            return "defuse_start"
        if "defus" in s:
            return "defuse"
        if "drop" in s:
            return "drop"
        if "pick" in s:
            return "pickup"
        if "explod" in s:
            return "explode"
        return s or "unknown"

    df["event"] = df["event"].astype(str).apply(_norm_event)
    if "tick" in df.columns:
        df["tick"] = pd.to_numeric(df["tick"], errors="coerce")
    if "round_num" in df.columns:
        df["round_num"] = pd.to_numeric(df["round_num"], errors="coerce")
    if "x" in df.columns:
        df["x"] = pd.to_numeric(df["x"], errors="coerce")
    if "y" in df.columns:
        df["y"] = pd.to_numeric(df["y"], errors="coerce")
    if "z" in df.columns:
        df["z"] = pd.to_numeric(df["z"], errors="coerce")

    keep = [c for c in ["event", "tick", "round_num", "player_name", "x", "y", "z"] if c in df.columns]
    if not keep:
        return []
    out = df[keep].copy()
    if "tick" in out.columns:
        out = out.dropna(subset=["tick"])
        out = out[out["tick"] > 0]
        out["tick"] = out["tick"].astype(int)
    return out.fillna("").to_dict(orient="records")


def _extract_map_bounds(df: pd.DataFrame) -> dict:
    """Tick verisinden harita koordinat sÄ±nÄ±rlarÄ±nÄ± Ã§Ä±karÄ±r."""
    if df is None or len(df) == 0:
        return {}

    x_col = 3 if 3 in df.columns else ("x" if "x" in df.columns else None)
    y_col = 4 if 4 in df.columns else ("y" if "y" in df.columns else None)
    if x_col is None or y_col is None:
        return {}

    tmp = pd.DataFrame({
        "x": pd.to_numeric(df[x_col], errors="coerce"),
        "y": pd.to_numeric(df[y_col], errors="coerce"),
    }).dropna()

    if tmp.empty:
        return {}

    return {
        "x_min": float(tmp["x"].min()),
        "x_max": float(tmp["x"].max()),
        "y_min": float(tmp["y"].min()),
        "y_max": float(tmp["y"].max()),
    }


def _normalize_grenade_type(raw_type: str) -> str:
    """awpy 2.x C++ sÄ±nÄ±f adlarÄ±nÄ± basit grenade type isimlerine Ã§evirir."""
    t = raw_type.lower().replace("projectile", "").replace("grenade", "").strip("c").strip()
    mapping = {
        "smoke": "smoke",
        "flashbang": "flash",
        "flash": "flash",
        "he": "he_grenade",
        "molotov": "molotov",
        "incendiary": "incendiary",
        "decoy": "decoy",
    }
    for key, val in mapping.items():
        if key in t:
            return val
    return raw_type


def _process_grenades(df: pd.DataFrame) -> list:
    if df is None or len(df) == 0:
        return []

    print(f"[DEBUG] Grenade columns: {list(df.columns)}")
    print(f"[DEBUG] Grenade rows (raw per-tick): {len(df)}")

    # awpy 2.x integer column mapping:
    # 0=steamid, 1=thrower_name, 2=grenade_type, 3=tick, 4=x, 5=y, 6=z
    if not isinstance(df.columns[0], str):
        col_map = {}
        if 1 in df.columns:
            col_map[1] = "thrower_name"
        if 2 in df.columns:
            col_map[2] = "grenade_type"
        if 3 in df.columns:
            col_map[3] = "tick"
        if 4 in df.columns:
            col_map[4] = "nade_x"
        if 5 in df.columns:
            col_map[5] = "nade_y"
        if 6 in df.columns:
            col_map[6] = "nade_z"
        df = df.rename(columns=col_map)
    else:
        # Named columns â€” try common aliases
        rename_map = {}
        for col in df.columns:
            cl = str(col).lower()
            if cl in ("thrower_name", "player_name", "attacker_name", "thrower"):
                rename_map[col] = "thrower_name"
            elif cl in ("grenade_type", "type", "weapon", "grenade"):
                rename_map[col] = "grenade_type"
            elif cl == "tick":
                rename_map[col] = "tick"
            elif cl in ("x", "nade_x"):
                rename_map[col] = "nade_x"
            elif cl in ("y", "nade_y"):
                rename_map[col] = "nade_y"
            elif cl in ("z", "nade_z"):
                rename_map[col] = "nade_z"
            elif cl in ("round", "round_num"):
                rename_map[col] = "round_num"
        if rename_map:
            df = df.rename(columns=rename_map)

    if "thrower_name" not in df.columns or "grenade_type" not in df.columns:
        print(f"[!] Grenade kolonlarÄ± bulunamadÄ±: {list(df.columns)}")
        return []

    # Normalize grenade type names (CSmokeGrenade â†’ smoke, CFlashbang â†’ flash)
    df["grenade_type"] = df["grenade_type"].astype(str).apply(_normalize_grenade_type)

    # Tick/round normalize et.
    if "tick" in df.columns:
        df["tick"] = pd.to_numeric(df["tick"], errors="coerce")
        df = df.dropna(subset=["tick"])
        df["tick"] = df["tick"].astype(int)
        df = df[df["tick"] > 0]
    if "round_num" in df.columns:
        df["round_num"] = pd.to_numeric(df["round_num"], errors="coerce")

    # Deduplikasyon: AynÄ± oyuncu + aynÄ± tip iÃ§in ardÄ±ÅŸÄ±k tick'leri (gap < 64) tek atÄ±ÅŸ say
    df = df.sort_values("tick") if "tick" in df.columns else df

    throws = []
    grouped = df.groupby(["thrower_name", "grenade_type"])
    for (player, gtype), group in grouped:
        if "tick" not in group.columns:
            throws.append({
                "thrower_name": player,
                "grenade_type": gtype,
            })
            continue

        ticks = group["tick"].dropna().sort_values().tolist()
        if not ticks:
            continue

        # Her yeni atÄ±ÅŸ, Ã¶nceki tick'ten >64 fark olduÄŸunda baÅŸlar
        throw_start_idx = 0
        for i in range(1, len(ticks)):
            if ticks[i] - ticks[i - 1] > 64:
                # Ã–nceki atÄ±ÅŸÄ± kaydet
                throw_rows = group[(group["tick"] >= ticks[throw_start_idx]) &
                                   (group["tick"] <= ticks[i - 1])]
                entry = {"thrower_name": player, "grenade_type": gtype, "tick": int(ticks[throw_start_idx])}
                _add_grenade_coords(entry, throw_rows)
                if "round_num" in throw_rows.columns:
                    rn_vals = pd.to_numeric(throw_rows["round_num"], errors="coerce").dropna()
                    if not rn_vals.empty:
                        entry["round_num"] = int(rn_vals.iloc[0])
                throws.append(entry)
                throw_start_idx = i

        # Son atÄ±ÅŸ
        throw_rows = group[group["tick"] >= ticks[throw_start_idx]]
        entry = {"thrower_name": player, "grenade_type": gtype, "tick": int(ticks[throw_start_idx])}
        _add_grenade_coords(entry, throw_rows)
        if "round_num" in throw_rows.columns:
            rn_vals = pd.to_numeric(throw_rows["round_num"], errors="coerce").dropna()
            if not rn_vals.empty:
                entry["round_num"] = int(rn_vals.iloc[0])
        throws.append(entry)

    print(f"[+] Grenade throws (deduplicated): {len(throws)}")
    from collections import Counter
    type_counts = Counter(t["grenade_type"] for t in throws)
    print(f"[+] Grenade type daÄŸÄ±lÄ±mÄ±: {dict(type_counts)}")
    return throws


def _add_grenade_coords(entry: dict, rows: pd.DataFrame):
    """AtÄ±ÅŸÄ±n baÅŸlangÄ±Ã§ ve bitiÅŸ koordinatlarÄ±nÄ± (varsa) entry'ye ekler."""
    if "nade_x" not in rows.columns or "nade_y" not in rows.columns:
        return

    with_coords = rows.copy()
    with_coords["nade_x"] = pd.to_numeric(with_coords["nade_x"], errors="coerce")
    with_coords["nade_y"] = pd.to_numeric(with_coords["nade_y"], errors="coerce")
    with_coords = with_coords.dropna(subset=["nade_x", "nade_y"])

    if with_coords.empty:
        return

    first = with_coords.iloc[0]
    last = with_coords.iloc[-1]

    # Projectile tick noktalarÄ±ndan rota Ã§Ä±kar (tekrarlayan noktalarÄ± sadeleÅŸtir).
    path_points = []
    for _, r in with_coords.iterrows():
        x = float(r["nade_x"])
        y = float(r["nade_y"])
        if not path_points or path_points[-1][0] != x or path_points[-1][1] != y:
            path_points.append([x, y])

    try:
        start_x = float(first["nade_x"])
        start_y = float(first["nade_y"])
        end_x = float(last["nade_x"])
        end_y = float(last["nade_y"])

        # Geriye dÃ¶nÃ¼k uyumluluk iÃ§in nade_x/nade_y bitiÅŸ noktasÄ± olarak tutuluyor.
        entry["nade_x"] = end_x
        entry["nade_y"] = end_y
        entry["nade_start_x"] = start_x
        entry["nade_start_y"] = start_y
        entry["nade_end_x"] = end_x
        entry["nade_end_y"] = end_y
        if len(path_points) >= 2:
            entry["nade_path"] = path_points
    except (ValueError, TypeError):
        pass


def _process_rounds(df: pd.DataFrame) -> list:
    if df is None or df.empty:
        return []

    if isinstance(df.columns[0], str):
        rename_map = {}
        for col in df.columns:
            cl = str(col).lower()
            if cl in ("round", "round_number"):
                rename_map[col] = "round_num"
            elif cl in ("start_tick", "round_start", "starttick"):
                rename_map[col] = "start"
            elif cl in ("freeze_end_tick", "freezeend", "freezetime_end"):
                rename_map[col] = "freeze_end"
            elif cl in ("end_tick", "round_end"):
                rename_map[col] = "end"
            elif cl in ("official_end_tick", "officialend"):
                rename_map[col] = "official_end"
            elif cl in ("winning_side",):
                rename_map[col] = "winner_side"
            elif cl in ("endreason",):
                rename_map[col] = "endReason"
        if rename_map:
            df = df.rename(columns=rename_map)

    keep = [c for c in [
                         "round_num",
                         "start",
                         "freeze_end",
                         "end",
                         "official_end",
                         "winner",
                         "bomb_plant",
                         "bomb_site",
                         "winner_side",
                         "reason",
                         "ct_eq_val",
                         "t_eq_val",
                         "winnerSide",
                         "endReason",
                        ]
            if c in df.columns]

    subset = df[keep] if keep else df
    return subset.fillna("").to_dict(orient="records")


def _get_player_list(df: pd.DataFrame) -> list:
    if df is None or len(df) == 0:
        return []

    players = set()
    if not isinstance(df.columns[0], str):
        for idx in [6, 14]:
            if idx in df.columns:
                players.update(df[idx].dropna().unique())
    else:
        for col in ["attacker_name", "attacker", "victim_name", "victim"]:
            if col in df.columns:
                players.update(df[col].dropna().unique())

    players = {p for p in players if isinstance(p, str) and 2 < len(p) < 40 and not p.isdigit()}
    return sorted(players)


def _normalize_steamid64(value) -> str | None:
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
    sval = str(int_val)
    if len(sval) < 16 or len(sval) > 20:
        return None
    return sval


def _build_player_identities(player_positions: list, kills: list | None = None) -> dict:
    """Build a dual-index identity structure keyed by SteamID64 (primary).

    Returns:
        {
            "by_steamid": {steamid64: {"player_name": str, "appearances": int}},
            "by_name":    {player_name: steamid64}   # reverse lookup; most-appeared wins
        }

    Data sources (in ascending reliability order):
    1. player_positions (ticks) — many rows, steamid field may be missing in some awpy builds
    2. kills — each row DIRECTLY pairs attacker/victim name with their steamid64,
               much more reliable than ticks. Weighted 20x to dominate the vote.

    Using steamid64 as primary key avoids wrong-profile issues when:
    - players change names during a match
    - multiple players share the same display name
    - tick data has misaligned steamid columns
    """
    pair_counts: dict[tuple[str, str], int] = {}

    # ── Source 1: player_positions (ticks) ────────────────────────────────────
    for row in (player_positions or []):
        name = str(row.get("player_name") or "").strip()
        sid = _normalize_steamid64(row.get("steamid"))
        if not name or not sid:
            continue
        pair_counts[(name, sid)] = pair_counts.get((name, sid), 0) + 1

    # ── Source 2: kills (primary — directly pairs name+steamid per event) ────
    # Each kill event gives us two guaranteed (name, steamid64) pairs.
    # Weight them much higher so they dominate over ticks when both are available.
    KILL_WEIGHT = 20
    for k in (kills or []):
        for name_key, sid_key in (
            ("attacker_name", "attacker_steamid"),
            ("victim_name",   "victim_steamid"),
            ("assister_name", "assister_steamid"),
        ):
            name = str(k.get(name_key) or "").strip()
            sid = _normalize_steamid64(k.get(sid_key))
            if not name or not sid:
                continue
            pair_counts[(name, sid)] = pair_counts.get((name, sid), 0) + KILL_WEIGHT

    if not pair_counts:
        return {"by_steamid": {}, "by_name": {}}

    # Reverse index: player_name -> steamid64 (highest-vote steamid64 per name)
    name_best: dict[str, tuple[str, int]] = {}
    for (name, sid), cnt in pair_counts.items():
        prev = name_best.get(name)
        if prev is None or cnt > prev[1]:
            name_best[name] = (sid, cnt)
    by_name: dict[str, str] = {name: sid for name, (sid, _) in name_best.items()}

    # Canonical primary index: keep only the winning steamid per player name.
    by_steamid: dict[str, dict] = {}
    for name, (sid, cnt) in name_best.items():
        prev = by_steamid.get(sid)
        if prev is None or cnt > prev["appearances"]:
            by_steamid[sid] = {"player_name": name, "appearances": cnt}

    kills_contributed = any(k.get("attacker_steamid") or k.get("victim_steamid") for k in (kills or []))
    print(f"[+] Player identities: {len(by_steamid)} SteamID64s  (kills_source={kills_contributed})")

    return {"by_steamid": by_steamid, "by_name": by_name}



def _normalize_kill_records(records: list[dict], round_lookup: list[dict]) -> list[dict]:
    normalized = []
    for record in records or []:
        normalized.append({
            "tick": _safe_int(record.get("tick"), None),
            "round_num": _assign_round_num(record, round_lookup),
            "attacker_name": _clean_text(record.get("attacker_name")),
            "victim_name": _clean_text(record.get("victim_name")),
            "assister_name": _clean_text(record.get("assister_name")),
            "weapon": _clean_text(record.get("weapon")),
            "headshot": _coerce_bool(record.get("headshot")),
            "attacker_side": _normalize_side(record.get("attacker_side")),
            "victim_side": _normalize_side(record.get("victim_side")),
            "victim_x": _safe_float(record.get("victim_x"), None),
            "victim_y": _safe_float(record.get("victim_y"), None),
            "attacker_steamid": _normalize_steamid64(record.get("attacker_steamid")),
            "victim_steamid": _normalize_steamid64(record.get("victim_steamid")),
            "assister_steamid": _normalize_steamid64(record.get("assister_steamid")),
        })
    return normalized


def _normalize_damage_records(records: list[dict], round_lookup: list[dict]) -> list[dict]:
    normalized = []
    for record in records or []:
        normalized.append({
            "tick": _safe_int(record.get("tick"), None),
            "round_num": _assign_round_num(record, round_lookup),
            "attacker_name": _clean_text(record.get("attacker_name")),
            "victim_name": _clean_text(record.get("victim_name")),
            "hp_damage": _safe_int(record.get("hp_damage"), 0) or 0,
            "weapon": _clean_text(record.get("weapon")),
            "hitgroup": _clean_text(record.get("hitgroup")),
            "victim_x": _safe_float(record.get("victim_x"), None),
            "victim_y": _safe_float(record.get("victim_y"), None),
            "attacker_x": _safe_float(record.get("attacker_x"), None),
            "attacker_y": _safe_float(record.get("attacker_y"), None),
        })
    return normalized


def _normalize_grenade_records(records: list[dict], round_lookup: list[dict]) -> list[dict]:
    normalized = []
    for record in records or []:
        item = {
            "tick": _safe_int(record.get("tick"), None),
            "round_num": _assign_round_num(record, round_lookup),
            "thrower_name": _clean_text(record.get("thrower_name")),
            "player_name": _clean_text(record.get("thrower_name")),
            "grenade_type": _clean_text(record.get("grenade_type")),
            "event_type": "throw",
            "nade_x": _safe_float(record.get("nade_x"), None),
            "nade_y": _safe_float(record.get("nade_y"), None),
            "nade_start_x": _safe_float(record.get("nade_start_x"), None),
            "nade_start_y": _safe_float(record.get("nade_start_y"), None),
            "nade_end_x": _safe_float(record.get("nade_end_x"), None),
            "nade_end_y": _safe_float(record.get("nade_end_y"), None),
            "nade_path": record.get("nade_path") if isinstance(record.get("nade_path"), list) else [],
        }
        if "nade_start_z" in record:
            item["nade_start_z"] = _safe_float(record.get("nade_start_z"), None)
        if "nade_end_z" in record:
            item["nade_end_z"] = _safe_float(record.get("nade_end_z"), None)
        normalized.append(item)
    return normalized


def _normalize_bomb_event_records(records: list[dict], round_lookup: list[dict]) -> list[dict]:
    normalized = []
    for record in records or []:
        event_type = _clean_text(record.get("event"))
        normalized.append({
            "tick": _safe_int(record.get("tick"), None),
            "round_num": _assign_round_num(record, round_lookup),
            "event": event_type,
            "event_type": event_type,
            "player_name": _clean_text(record.get("player_name")),
            "x": _safe_float(record.get("x"), None),
            "y": _safe_float(record.get("y"), None),
            "z": _safe_float(record.get("z"), None),
        })
    return normalized


def _normalize_shot_records(records: list[dict], round_lookup: list[dict]) -> list[dict]:
    normalized = []
    for record in records or []:
        normalized.append({
            "tick": _safe_int(record.get("tick"), None),
            "round_num": _assign_round_num(record, round_lookup),
            "shooter_name": _clean_text(record.get("shooter_name")),
            "shooter_side": _normalize_side(record.get("shooter_side")),
            "weapon": _clean_text(record.get("weapon")),
            "shot_x": _safe_float(record.get("shot_x"), None),
            "shot_y": _safe_float(record.get("shot_y"), None),
        })
    return normalized


def _normalize_player_positions(records: list[dict], round_lookup: list[dict]) -> list[dict]:
    normalized = []
    for record in records or []:
        hp = _safe_int(record.get("hp"), None)
        normalized.append({
            "tick": _safe_int(record.get("tick"), None),
            "round_num": _assign_round_num(record, round_lookup),
            "player_name": _clean_text(record.get("player_name")),
            "steamid": _normalize_steamid64(record.get("steamid")),
            "x": _safe_float(record.get("x"), None),
            "y": _safe_float(record.get("y"), None),
            "z": _safe_float(record.get("z"), None),
            "side": _normalize_side(record.get("side")),
            "yaw": _safe_float(record.get("yaw"), None),
            "hp": hp,
            "armor": _safe_int(record.get("armor"), None),
            "alive": None if hp is None else hp > 0,
        })
    return normalized


def _normalize_round_records(records: list[dict]) -> list[dict]:
    normalized = []
    for idx, record in enumerate(records or [], start=1):
        round_num = _safe_int(record.get("round_num"), idx) or idx
        start_tick = _safe_int(record.get("start"), None)
        freeze_end_tick = _safe_int(record.get("freeze_end"), None)
        official_end = _safe_int(record.get("official_end"), None)
        end_tick = official_end if official_end is not None else _safe_int(record.get("end"), None)
        reason = _clean_text(record.get("reason") or record.get("endReason"))
        winner_side = _normalize_side(record.get("winner_side") or record.get("winner") or record.get("winnerSide"))
        normalized.append({
            "round_num": round_num,
            "start": start_tick,
            "freeze_end": freeze_end_tick,
            "end": _safe_int(record.get("end"), None),
            "official_end": official_end,
            "winner": _clean_text(record.get("winner")),
            "bomb_plant": _clean_text(record.get("bomb_plant")),
            "bomb_site": _clean_text(record.get("bomb_site")),
            "winner_side": winner_side,
            "reason": reason,
            "ct_eq_val": _safe_int(record.get("ct_eq_val"), None),
            "t_eq_val": _safe_int(record.get("t_eq_val"), None),
            "start_tick": start_tick,
            "freeze_end_tick": freeze_end_tick,
            "end_tick": end_tick,
            "end_reason": reason,
        })
    return normalized


def _build_player_entities(player_positions: list[dict], kills: list[dict], player_identities: dict) -> list[dict]:
    players: dict[str, dict] = {}

    def ensure(name: str) -> dict | None:
        player_name = _clean_text(name)
        if not player_name:
            return None
        if player_name not in players:
            players[player_name] = {
                "player_name": player_name,
                "steamid64": None,
                "side": "",
                "sides": set(),
                "identity": {"display_name": player_name},
                "position_samples": 0,
                "kill_events": 0,
                "death_events": 0,
                "assist_events": 0,
            }
        return players[player_name]

    for row in player_positions or []:
        player = ensure(row.get("player_name"))
        if not player:
            continue
        sid = _normalize_steamid64(row.get("steamid"))
        if sid and not player["steamid64"]:
            player["steamid64"] = sid
        side = _normalize_side(row.get("side"))
        if side:
            player["sides"].add(side)
        player["position_samples"] += 1

    for kill in kills or []:
        for role, name_key, sid_key, side_key in (
            ("kill_events", "attacker_name", "attacker_steamid", "attacker_side"),
            ("death_events", "victim_name", "victim_steamid", "victim_side"),
            ("assist_events", "assister_name", "assister_steamid", None),
        ):
            player = ensure(kill.get(name_key))
            if not player:
                continue
            player[role] += 1
            sid = _normalize_steamid64(kill.get(sid_key))
            if sid and not player["steamid64"]:
                player["steamid64"] = sid
            if side_key:
                side = _normalize_side(kill.get(side_key))
                if side:
                    player["sides"].add(side)

    by_name = (player_identities or {}).get("by_name", {})
    for player in players.values():
        if not player["steamid64"]:
            player["steamid64"] = _normalize_steamid64(by_name.get(player["player_name"]))
        known_sides = sorted(player["sides"])
        player["side"] = known_sides[0] if known_sides else ""
        player["identity"] = {
            "display_name": player["player_name"],
            "steamid64": player["steamid64"],
            "known_sides": known_sides,
        }
        player.pop("sides", None)

    return sorted(players.values(), key=lambda item: item["player_name"].lower())


def _build_validation_summary(rounds: list[dict], kills: list[dict], damages: list[dict], grenades: list[dict], bomb_events: list[dict], shots: list[dict], player_positions: list[dict], players: list[dict], player_identities: dict) -> dict:
    missing_round_assignments = {
        "kills": sum(1 for row in kills if row.get("round_num") is None),
        "damages": sum(1 for row in damages if row.get("round_num") is None),
        "grenades": sum(1 for row in grenades if row.get("round_num") is None),
        "bomb_events": sum(1 for row in bomb_events if row.get("round_num") is None),
        "shots": sum(1 for row in shots if row.get("round_num") is None),
        "player_positions": sum(1 for row in player_positions if row.get("round_num") is None),
    }
    warnings = []
    if not rounds:
        warnings.append("rounds_missing")
    if not players:
        warnings.append("players_missing")
    if missing_round_assignments["kills"]:
        warnings.append("kills_without_round")
    if missing_round_assignments["player_positions"]:
        warnings.append("positions_without_round")
    return {
        "is_valid": len(warnings) == 0,
        "warnings": warnings,
        "counts": {
            "players": len(players),
            "rounds": len(rounds),
            "kills": len(kills),
            "damages": len(damages),
            "grenades": len(grenades),
            "bomb_events": len(bomb_events),
            "shots": len(shots),
            "player_positions": len(player_positions),
            "resolved_steamids": len((player_identities or {}).get("by_steamid", {})),
            "missing_steamids": sum(1 for player in players if not player.get("steamid64")),
        },
        "missing_round_assignments": missing_round_assignments,
    }


def _log_parse_summary(parsed: dict) -> None:
    counts = (parsed.get("validation") or {}).get("counts", {})
    highlight_total = ((parsed.get("highlight_summary") or {}).get("total_highlights", 0))
    clip_plan_total = ((parsed.get("clip_plan_summary") or {}).get("total_clip_plans", 0))
    print(
        "[+] Parse summary:"
        f" players={counts.get('players', 0)}"
        f" rounds={counts.get('rounds', 0)}"
        f" kills={counts.get('kills', 0)}"
        f" damages={counts.get('damages', 0)}"
        f" grenades={counts.get('grenades', 0)}"
        f" bomb_events={counts.get('bomb_events', 0)}"
        f" positions={counts.get('player_positions', 0)}"
        f" missing_steamids={counts.get('missing_steamids', 0)}"
        f" highlights={highlight_total}"
        f" clip_plans={clip_plan_total}"
    )


def save_parsed_data(data: dict, output_path: str):
    """Parse edilen veriyi JSON olarak kaydeder."""
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    print(f"[+] Veri kaydedildi: {output_path}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("KullanÄ±m: python src/parser.py demos/mymatch.dem")
        sys.exit(1)

    data = parse_demo(sys.argv[1])
    save_parsed_data(data, "outputs/parsed_demo.json")
    print(f"[+] Oyuncular: {data['players']}")
    print("[+] Parse tamamlandÄ±!")
