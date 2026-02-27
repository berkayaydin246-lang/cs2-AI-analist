"""
parser.py
CS2 demo dosyasını okur ve ham veriyi yapılandırılmış formata çevirir.
awpy 2.x API'sine göre yazılmıştır.
"""

from awpy import Demo
import pandas as pd
import json
from pathlib import Path


def parse_demo(demo_path: str) -> dict:
    """
    .dem dosyasını parse eder ve analiz için gerekli veriyi çıkarır.
    """
    print(f"[+] Demo yükleniyor: {demo_path}")

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
        try:
            return pd.DataFrame(val)
        except Exception:
            return pd.DataFrame()

    kills_df    = _to_df(demo.kills    if hasattr(demo, "kills")    else None)
    damages_df  = _to_df(demo.damages  if hasattr(demo, "damages")  else None)
    rounds_df   = _to_df(demo.rounds   if hasattr(demo, "rounds")   else None)
    grenades_df = _to_df(demo.grenades if hasattr(demo, "grenades") else None)

    total_rounds = len(rounds_df) if len(rounds_df) > 0 else 0

    print(f"[+] Harita      : {map_name}")
    print(f"[+] Round sayısı: {total_rounds}")
    print(f"[+] Kill sayısı : {len(kills_df)}")

    result = {
        "map":          map_name,
        "total_rounds": total_rounds,
        "kills":        _process_kills(kills_df),
        "damages":      _process_damages(damages_df),
        "grenades":     _process_grenades(grenades_df),
        "rounds":       _process_rounds(rounds_df),
        "players":      _get_player_list(kills_df),
    }

    return result


def _col(df: pd.DataFrame, *candidates):
    """DataFrame'de var olan ilk kolon adını döner."""
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
        })

    keep = [c for c in ["attacker_name", "victim_name", "weapon",
                         "headshot", "attacker_side", "victim_side",
                         "tick", "victim_x", "victim_y"] if c in df.columns]
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
        })

    keep = [c for c in ["attacker_name", "victim_name", "hp_damage", "weapon", "hitgroup"]
            if c in df.columns]
    return df[keep].fillna("").to_dict(orient="records")


def _normalize_grenade_type(raw_type: str) -> str:
    """awpy 2.x C++ sınıf adlarını basit grenade type isimlerine çevirir."""
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
        # Named columns — try common aliases
        rename_map = {}
        for col in df.columns:
            cl = str(col).lower()
            if cl in ("thrower_name", "player_name", "attacker_name", "thrower"):
                rename_map[col] = "thrower_name"
            elif cl in ("grenade_type", "type", "weapon", "grenade"):
                rename_map[col] = "grenade_type"
            elif cl == "tick":
                rename_map[col] = "tick"
        if rename_map:
            df = df.rename(columns=rename_map)

    if "thrower_name" not in df.columns or "grenade_type" not in df.columns:
        print(f"[!] Grenade kolonları bulunamadı: {list(df.columns)}")
        return []

    # Normalize grenade type names (CSmokeGrenade → smoke, CFlashbang → flash)
    df["grenade_type"] = df["grenade_type"].astype(str).apply(_normalize_grenade_type)

    # Projectile satırlarını filtrele — sadece "Grenade" (throw) eventlerini kullan,
    # ama koordinatlar genellikle Projectile'da olduğu için hepsini tut
    # Deduplikasyon: Aynı oyuncu + aynı tip için ardışık tick'leri (gap < 64) tek atış say
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

        # Her yeni atış, önceki tick'ten >64 fark olduğunda başlar
        throw_start_idx = 0
        for i in range(1, len(ticks)):
            if ticks[i] - ticks[i - 1] > 64:
                # Önceki atışı kaydet
                throw_rows = group[(group["tick"] >= ticks[throw_start_idx]) &
                                   (group["tick"] <= ticks[i - 1])]
                entry = {"thrower_name": player, "grenade_type": gtype, "tick": int(ticks[throw_start_idx])}
                _add_grenade_coords(entry, throw_rows)
                throws.append(entry)
                throw_start_idx = i

        # Son atış
        throw_rows = group[group["tick"] >= ticks[throw_start_idx]]
        entry = {"thrower_name": player, "grenade_type": gtype, "tick": int(ticks[throw_start_idx])}
        _add_grenade_coords(entry, throw_rows)
        throws.append(entry)

    print(f"[+] Grenade throws (deduplicated): {len(throws)}")
    from collections import Counter
    type_counts = Counter(t["grenade_type"] for t in throws)
    print(f"[+] Grenade type dağılımı: {dict(type_counts)}")
    return throws


def _add_grenade_coords(entry: dict, rows: pd.DataFrame):
    """Atışın başlangıç ve bitiş koordinatlarını (varsa) entry'ye ekler."""
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

    # Projectile tick noktalarından rota çıkar (tekrarlayan noktaları sadeleştir).
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

        # Geriye dönük uyumluluk için nade_x/nade_y bitiş noktası olarak tutuluyor.
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

    keep = [c for c in ["winner_side", "reason", "ct_eq_val", "t_eq_val",
                         "winnerSide", "endReason"]
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
        print("Kullanım: python src/parser.py demos/mymatch.dem")
        sys.exit(1)

    data = parse_demo(sys.argv[1])
    save_parsed_data(data, "outputs/parsed_demo.json")
    print(f"[+] Oyuncular: {data['players']}")
    print("[+] Parse tamamlandı!")
