"""
parser.py
CS2 demo dosyasÄ±nÄ± okur ve ham veriyi yapÄ±landÄ±rÄ±lmÄ±ÅŸ formata Ã§evirir.
awpy 2.x API'sine gÃ¶re yazÄ±lmÄ±ÅŸtÄ±r.
"""

from awpy import Demo
import pandas as pd
import json
from pathlib import Path


def parse_demo(demo_path: str) -> dict:
    """
    .dem dosyasÄ±nÄ± parse eder ve analiz iÃ§in gerekli veriyi Ã§Ä±karÄ±r.
    """
    print(f"[+] Demo yÃ¼kleniyor: {demo_path}")

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

    kills_df    = _to_df(demo.kills    if hasattr(demo, "kills")    else None)
    damages_df  = _to_df(demo.damages  if hasattr(demo, "damages")  else None)
    rounds_df   = _to_df(demo.rounds   if hasattr(demo, "rounds")   else None)
    grenades_df = _to_df(demo.grenades if hasattr(demo, "grenades") else None)
    shots_df    = _to_df(demo.shots    if hasattr(demo, "shots")    else None)
    ticks_df    = _to_df(demo.ticks    if hasattr(demo, "ticks")    else None)
    bomb_df     = _to_df(
        demo.bomb if hasattr(demo, "bomb") else (
            demo.bomb_events if hasattr(demo, "bomb_events") else None
        )
    )

    total_rounds = len(rounds_df) if len(rounds_df) > 0 else 0

    print(f"[+] Harita      : {map_name}")
    print(f"[+] Round sayÄ±sÄ±: {total_rounds}")
    print(f"[+] Kill sayÄ±sÄ± : {len(kills_df)}")

    result = {
        "schema_version": 8,
        "map":          map_name,
        "total_rounds": total_rounds,
        "map_bounds":   _extract_map_bounds(ticks_df),
        "kills":        _process_kills(kills_df),
        "damages":      _process_damages(damages_df),
        "grenades":     _process_grenades(grenades_df),
        "bomb_events":  _process_bomb_events(bomb_df),
        "shots":        _process_shots(shots_df),
        "player_positions": _process_ticks(ticks_df),
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
        # awpy Polars uses uppercase X/Y coords â€” normalize to lowercase
        coord_rename = {}
        for col in df.columns:
            if col == "victim_X":   coord_rename[col] = "victim_x"
            elif col == "victim_Y": coord_rename[col] = "victim_y"
        if coord_rename:
            df = df.rename(columns=coord_rename)

    keep = [c for c in ["attacker_name", "victim_name", "weapon",
                         "headshot", "attacker_side", "victim_side",
                         "tick", "victim_x", "victim_y", "round_num"] if c in df.columns]
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

    if not isinstance(df.columns[0], str):
        rename_map = {
            2: "side",
            3: "x",
            4: "y",
            8: "player_name",
            9: "round_num",
        }
        # Bazi schema'larda tick kolonu farkli bir integer index'te gelebilir.
        # Ilk bulunan kolonu almak steamid gibi alanlari yanlis secmeye neden olur.
        tick_col = None
        tick_score = None
        reserved = {2, 3, 4, 8, 9}
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
            elif cl in ("armor", "armour", "player_armor", "player_armour"):
                rename_map[col] = "armor"
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

    keep = [c for c in ["player_name", "x", "y", "side", "round_num", "tick", "yaw", "hp", "armor"] if c in sampled.columns]
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
        if cl in ("tick",):
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
    return df[keep].fillna("").to_dict(orient="records")


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

    # Projectile satÄ±rlarÄ±nÄ± filtrele â€” sadece "Grenade" (throw) eventlerini kullan,
    # ama koordinatlar genellikle Projectile'da olduÄŸu iÃ§in hepsini tut
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
                throws.append(entry)
                throw_start_idx = i

        # Son atÄ±ÅŸ
        throw_rows = group[group["tick"] >= ticks[throw_start_idx]]
        entry = {"thrower_name": player, "grenade_type": gtype, "tick": int(ticks[throw_start_idx])}
        _add_grenade_coords(entry, throw_rows)
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
