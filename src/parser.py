"""
parser.py
CS2 demo dosyasÄ±nÄ± okur ve ham veriyi yapÄ±landÄ±rÄ±lmÄ±ÅŸ formata Ã§evirir.
awpy 2.x API'sine gÃ¶re yazÄ±lmÄ±ÅŸtÄ±r.
"""

from awpy import Demo
import pandas as pd
import json
from pathlib import Path


# Ekstra oyuncu alanlari — tick verisine yaw/armor/silah/kit bilgisi ekler.
# awpy 2.x bunlari ancak player_props ile parse edilirse cikarir.
PLAYER_PROPS = ["yaw", "armor_value", "active_weapon_name", "has_defuser", "has_helmet"]


def parse_demo(demo_path: str) -> dict:
    """
    .dem dosyasÄ±nÄ± parse eder ve analiz iÃ§in gerekli veriyi Ã§Ä±karÄ±r.
    """
    print(f"[+] Demo yÃ¼kleniyor: {demo_path}")

    demo = Demo(demo_path)
    try:
        demo.parse(player_props=PLAYER_PROPS)
    except Exception as exc:
        print(f"[!] player_props ile parse basarisiz ({exc}); duz parse deneniyor")
        demo = Demo(demo_path)
        demo.parse()

    # awpy 2.x header
    map_name = "unknown"
    if hasattr(demo, "header") and demo.header:
        map_name = demo.header.get("map_name", "unknown")

    def _to_df(val):
        if val is None:
            return pd.DataFrame()
        if isinstance(val, pd.DataFrame):
            return val
        # awpy 2.x Polars DataFrame donusumu
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

    def _attr_df(name: str) -> pd.DataFrame:
        """awpy lazy-property'lerinden guvenli DataFrame cikarir (eksik event -> bos)."""
        try:
            return _to_df(getattr(demo, name, None))
        except Exception:
            return pd.DataFrame()

    kills_df    = _attr_df("kills")
    damages_df  = _attr_df("damages")
    rounds_df   = _attr_df("rounds")
    grenades_df = _attr_df("grenades")
    shots_df    = _attr_df("shots")
    ticks_df    = _attr_df("ticks")
    smokes_df   = _attr_df("smokes")
    infernos_df = _attr_df("infernos")
    bomb_df     = _attr_df("bomb")
    if bomb_df.empty:
        bomb_df = _attr_df("bomb_events")

    def _event_df(event_name: str) -> pd.DataFrame:
        try:
            events = getattr(demo, "events", None) or {}
            return _to_df(events.get(event_name))
        except Exception:
            return pd.DataFrame()

    flash_det_df = _event_df("flashbang_detonate")
    he_det_df    = _event_df("hegrenade_detonate")

    def _optional_event_df(event_name: str) -> pd.DataFrame:
        """awpy'nin event listesinde olmayan eventleri demoparser2'den dogrudan ceker."""
        try:
            raw_parser = getattr(demo, "parser", None)
            if raw_parser is None:
                return pd.DataFrame()
            res = raw_parser.parse_event(event_name)
            if isinstance(res, pd.DataFrame):
                return res
        except Exception:
            pass
        return pd.DataFrame()

    blind_df   = _optional_event_df("player_blind")
    explode_df = _optional_event_df("bomb_exploded")

    total_rounds = len(rounds_df) if len(rounds_df) > 0 else 0

    print(f"[+] Harita      : {map_name}")
    print(f"[+] Round sayÄ±sÄ±: {total_rounds}")
    print(f"[+] Kill sayÄ±sÄ± : {len(kills_df)}")

    player_positions = _process_ticks(ticks_df)
    # Process kills first so we can use attacker/victim steamid columns for identity mapping
    kills_processed = _process_kills(kills_df)
    player_identities = _build_player_identities(player_positions, kills=kills_processed)

    bomb_events = _process_bomb_events(bomb_df)
    bomb_events.extend(_explode_events(explode_df, bomb_events))

    shots = _process_shots(shots_df)
    _enrich_shots_with_yaw(shots, player_positions)

    result = {
        "schema_version": 11,
        "map":          map_name,
        "total_rounds": total_rounds,
        "map_bounds":   _extract_map_bounds(ticks_df),
        "kills":        kills_processed,
        "damages":      _process_damages(damages_df),
        "grenades":     _process_grenades(grenades_df),
        "bomb_events":  bomb_events,
        "shots":        shots,
        "effects":      _process_effects(smokes_df, infernos_df, flash_det_df, he_det_df, blind_df),
        "player_positions": player_positions,
        "player_identities": player_identities,
        "rounds":       _process_rounds(rounds_df),
        "players":      _get_player_list(kills_df),
    }

    return result


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
            elif cl in ("x", "shot_x", "player_x"):
                rename_map[col] = "shot_x"
            elif cl in ("y", "shot_y", "player_y"):
                rename_map[col] = "shot_y"
            elif cl in ("weapon", "weapon_name"):
                rename_map[col] = "weapon"
            elif cl in ("tick",):
                rename_map[col] = "tick"
            elif cl in ("round", "round_num"):
                rename_map[col] = "round_num"
            elif cl in ("side", "shooter_side", "player_side"):
                rename_map[col] = "shooter_side"
        if rename_map:
            df = df.rename(columns=rename_map)

    keep = [c for c in ["shooter_name", "shooter_side", "weapon",
                        "tick", "round_num", "shot_x", "shot_y"]
            if c in df.columns]
    return df[keep].fillna("").to_dict(orient="records")


def _enrich_shots_with_yaw(shots: list, player_positions: list) -> None:
    """Her atisa, atis aninda atan oyuncunun bakis yonunu (yaw) ekler.

    Tick verisi ornekleme ile seyreltildigi icin en yakin ornek kullanilir
    (maks ~8 tick / 125 ms sapma — 2D harita icin yeterli).
    """
    if not shots or not player_positions:
        return
    pos_df = pd.DataFrame(player_positions)
    if not {"player_name", "tick", "yaw"}.issubset(pos_df.columns):
        return
    pos_df = pos_df.dropna(subset=["tick", "yaw"])
    if pos_df.empty:
        return

    import numpy as np
    by_player: dict[str, tuple] = {}
    for name, g in pos_df.groupby("player_name"):
        g = g.sort_values("tick")
        by_player[str(name)] = (g["tick"].to_numpy(dtype=float), g["yaw"].to_numpy(dtype=float))

    enriched = 0
    for s in shots:
        name = str(s.get("shooter_name") or "")
        try:
            tick = float(s.get("tick"))
        except (TypeError, ValueError):
            continue
        entry = by_player.get(name)
        if entry is None:
            continue
        ticks_arr, yaw_arr = entry
        idx = int(np.searchsorted(ticks_arr, tick))
        # En yakin ornegi sec (once/sonra)
        best = idx
        if idx >= len(ticks_arr):
            best = len(ticks_arr) - 1
        elif idx > 0 and (tick - ticks_arr[idx - 1]) < (ticks_arr[idx] - tick):
            best = idx - 1
        s["yaw"] = float(yaw_arr[best])
        enriched += 1
    print(f"[+] Shots yaw ile eslesti: {enriched}/{len(shots)}")


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
            elif cl in ("armor", "armour", "armor_value", "player_armor", "player_armour"):
                rename_map[col] = "armor"
            elif cl in ("active_weapon_name", "active_weapon", "weapon_name", "weapon"):
                rename_map[col] = "weapon"
            elif cl in ("has_defuser", "has_defuse_kit"):
                rename_map[col] = "has_defuser"
            elif cl in ("has_helmet",):
                rename_map[col] = "has_helmet"
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

    keep = [c for c in ["player_name", "steamid", "x", "y", "side", "round_num", "tick",
                        "yaw", "hp", "armor", "weapon", "has_defuser", "has_helmet"] if c in sampled.columns]
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

    keep = [c for c in ["event", "tick", "round_num", "player_name", "x", "y"] if c in df.columns]
    if not keep:
        return []
    out = df[keep].copy()
    if "tick" in out.columns:
        out = out.dropna(subset=["tick"])
        out = out[out["tick"] > 0]
        out["tick"] = out["tick"].astype(int)
    return out.fillna("").to_dict(orient="records")


def _explode_events(df: pd.DataFrame, bomb_events: list) -> list:
    """bomb_exploded eventlerini plant konumlariyla eslestirerek explode kaydi uretir."""
    if df is None or len(df) == 0 or "tick" not in df.columns:
        return []

    plants = [b for b in bomb_events if b.get("event") == "plant" and b.get("tick")]
    out = []
    for _, r in df.iterrows():
        try:
            tick = int(float(r.get("tick")))
        except (TypeError, ValueError):
            continue
        if tick <= 0:
            continue
        entry: dict = {"event": "explode", "tick": tick}
        # Patlama konumu = son plant konumu (bomba plant edildigi yerde patlar)
        prior = [p for p in plants if int(p["tick"]) <= tick]
        if prior:
            last = max(prior, key=lambda p: int(p["tick"]))
            for key in ("x", "y", "round_num"):
                if last.get(key) not in (None, ""):
                    entry[key] = last[key]
        out.append(entry)
    if out:
        print(f"[+] Bomb explode eventleri: {len(out)}")
    return out


def _process_effects(smokes_df: pd.DataFrame, infernos_df: pd.DataFrame,
                     flash_df: pd.DataFrame, he_df: pd.DataFrame,
                     blind_df: pd.DataFrame) -> list:
    """Kalici alan efektlerini (smoke/molotov) ve patlama efektlerini (flash/he) cikarir.

    - smoke/molotov: awpy smokes/infernos tablolari — start_tick/end_tick araligi boyunca
      haritada alan olarak cizilir.
    - flash: flashbang_detonate eventi + player_blind eventi (entityid ile join) —
      patlama ani + kor olan oyuncular ve sureleri.
    - he: hegrenade_detonate eventi — patlama ani.
    """
    effects: list = []

    def _f(v, default=None):
        try:
            f = float(v)
            if f != f:  # NaN
                return default
            return f
        except (TypeError, ValueError):
            return default

    # ── Alan efektleri: smoke (~18 s) ve molotov (~7 s) ───────────────────────
    for df, etype, default_life in ((smokes_df, "smoke", 18 * 64),
                                    (infernos_df, "molotov", 7 * 64)):
        if df is None or len(df) == 0:
            continue
        if not {"X", "Y", "start_tick"}.issubset(set(df.columns)):
            continue
        for _, r in df.iterrows():
            start = _f(r.get("start_tick"))
            x = _f(r.get("X"))
            y = _f(r.get("Y"))
            if not start or start <= 0 or x is None or y is None:
                continue
            end = _f(r.get("end_tick"))
            if end is None or end <= start:
                end = start + default_life
            entry = {
                "type": etype,
                "x": x,
                "y": y,
                "start_tick": int(start),
                "end_tick": int(end),
                "thrower": str(r.get("thrower_name") or ""),
            }
            rn = _f(r.get("round_num"))
            if rn and rn > 0:
                entry["round_num"] = int(rn)
            effects.append(entry)

    # ── Blind sureleri: flash entityid -> etkilenen oyuncular ─────────────────
    blinded_by_entity: dict[int, list] = {}
    if blind_df is not None and len(blind_df) > 0 and "entityid" in blind_df.columns:
        for _, r in blind_df.iterrows():
            eid = _f(r.get("entityid"))
            dur = _f(r.get("blind_duration")) or 0.0
            name = str(r.get("user_name") or "")
            if eid is None or not name or dur <= 0:
                continue
            blinded_by_entity.setdefault(int(eid), []).append(
                {"player": name, "duration_s": round(dur, 3)}
            )

    # ── Patlama efektleri: flash ve HE ────────────────────────────────────────
    for df, etype in ((flash_df, "flash"), (he_df, "he")):
        if df is None or len(df) == 0:
            continue
        if not {"x", "y", "tick"}.issubset(set(df.columns)):
            continue
        for _, r in df.iterrows():
            tick = _f(r.get("tick"))
            x = _f(r.get("x"))
            y = _f(r.get("y"))
            if not tick or tick <= 0 or x is None or y is None:
                continue
            entry = {
                "type": etype,
                "x": x,
                "y": y,
                "tick": int(tick),
                "thrower": str(r.get("user_name") or ""),
            }
            if etype == "flash":
                eid = _f(r.get("entityid"))
                entry["blinded"] = blinded_by_entity.get(int(eid), []) if eid is not None else []
            effects.append(entry)

    from collections import Counter
    counts = Counter(e["type"] for e in effects)
    print(f"[+] Effects: {dict(counts)}")
    return effects


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

    # Primary index: steamid64 → most-common name for that steamid64
    by_steamid: dict[str, dict] = {}
    for (name, sid), cnt in pair_counts.items():
        prev = by_steamid.get(sid)
        if prev is None or cnt > prev["appearances"]:
            by_steamid[sid] = {"player_name": name, "appearances": cnt}

    # Reverse index: player_name → steamid64 (highest-vote steamid64 per name)
    name_best: dict[str, tuple[str, int]] = {}
    for (name, sid), cnt in pair_counts.items():
        prev = name_best.get(name)
        if prev is None or cnt > prev[1]:
            name_best[name] = (sid, cnt)
    by_name: dict[str, str] = {name: sid for name, (sid, _) in name_best.items()}

    # Diagnostic log: show every resolved identity so wrong-mapping is easy to spot
    kills_contributed = any(k.get("attacker_steamid") or k.get("victim_steamid") for k in (kills or []))
    print(f"[+] Player identities: {len(by_steamid)} SteamID64s  (kills_source={kills_contributed})")
    for sid, info in sorted(by_steamid.items(), key=lambda kv: -kv[1]["appearances"]):
        print(f"    SteamID64={sid}  name={info['player_name']!r}  score={info['appearances']}")

    return {"by_steamid": by_steamid, "by_name": by_name}


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
