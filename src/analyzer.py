"""
analyzer.py
Ham demo verisini analiz eder, oyuncuya özel istatistikler ve kural tabanlı bulgular çıkarır.
"""

import pandas as pd
from collections import defaultdict


def analyze_player(parsed_data: dict, player_name: str) -> dict:
    """
    Belirli bir oyuncu için tüm analizleri çalıştırır.
    """
    print(f"[+] Analiz ediliyor: {player_name}")

    kills = parsed_data.get("kills", [])
    damages = parsed_data.get("damages", [])
    grenades = parsed_data.get("grenades", [])
    shots = parsed_data.get("shots", [])
    player_positions = parsed_data.get("player_positions", [])
    rounds = parsed_data.get("rounds", [])
    total_rounds = parsed_data.get("total_rounds", 1)

    map_name = parsed_data.get("map", "unknown")

    stats = _calculate_stats(
        kills, damages, grenades, shots, player_positions, player_name, total_rounds
    )
    findings = _rule_based_findings(stats, kills, player_name, total_rounds)

    advanced = _advanced_analysis(
        kills, damages, grenades, shots, player_positions, rounds,
        player_name, total_rounds, map_name
    )

    pro = _pro_metrics(
        kills,
        damages,
        grenades,
        shots,
        player_positions,
        player_name,
        total_rounds,
        stats,
        advanced,
    )
    advanced["pro_metrics"] = pro

    findings.extend(_advanced_findings(advanced, stats, player_name))

    return {
        "player": player_name,
        "map": map_name,
        "total_rounds": total_rounds,
        "stats": stats,
        "findings": findings,
        "advanced": advanced,
    }


def _normalize_weapon_name(weapon: str) -> str:
    w = str(weapon or "").lower().strip()
    return w.replace("weapon_", "")


def _is_bullet_weapon(weapon: str) -> bool:
    w = _normalize_weapon_name(weapon)
    if not w:
        return False
    banned = (
        "knife", "bayonet", "grenade", "molotov", "incendiary",
        "flash", "smoke", "decoy", "c4", "bomb", "taser", "zeus",
    )
    return not any(tok in w for tok in banned)


def _is_sniper_weapon(weapon: str) -> bool:
    w = _normalize_weapon_name(weapon)
    if not w:
        return False
    sniper_tokens = ("awp", "ssg08", "scout", "scar20", "g3sg1")
    return any(tok in w for tok in sniper_tokens)


def _calculate_stats(kills, damages, grenades, shots, player_positions, player_name, total_rounds) -> dict:
    """Temel istatistikleri hesaplar."""

    # Kill istatistikleri
    player_kills = [k for k in kills if k.get("attacker_name") == player_name]
    player_deaths = [k for k in kills if k.get("victim_name") == player_name]

    kill_count = len(player_kills)
    death_count = len(player_deaths)
    assist_count = sum(
        1
        for k in kills
        if k.get("assister_name") == player_name
        and k.get("attacker_name") != player_name
        and k.get("victim_name") != player_name
    )

    headshots = [k for k in player_kills if k.get("headshot") == True]
    hs_rate = round(len(headshots) / kill_count * 100, 1) if kill_count > 0 else 0.0

    kd_ratio = round(kill_count / max(death_count, 1), 2)
    kills_per_round = round(kill_count / max(total_rounds, 1), 2)
    deaths_per_round = round(death_count / max(total_rounds, 1), 2)

    # Hasar istatistikleri
    player_damages = [d for d in damages if d.get("attacker_name") == player_name]
    total_damage = sum(d.get("hp_damage", 0) for d in player_damages)
    adr = round(total_damage / max(total_rounds, 1), 1)

    # Mermi doğruluğu (bullet weapons): isabetli atış tick'i / toplam atış
    player_shots = [s for s in shots if s.get("shooter_name") == player_name and _is_bullet_weapon(s.get("weapon"))]
    shot_count = len(player_shots)
    player_bullet_hits = [d for d in player_damages if _is_bullet_weapon(d.get("weapon"))]
    hit_ticks = {d.get("tick") for d in player_bullet_hits if d.get("tick") not in ("", None)}
    hit_shots = len(hit_ticks)
    accuracy = round((hit_shots / shot_count) * 100, 1) if shot_count > 0 else 0.0

    # Utility istatistikleri (parser artık deduplicated throw listesi döner)
    player_grenades = [g for g in grenades if g.get("thrower_name") == player_name]
    print(f"[+] Toplam grenade throws: {len(grenades)}, oyuncu '{player_name}': {len(player_grenades)}")
    grenade_counts = defaultdict(int)
    for g in player_grenades:
        grenade_counts[g.get("grenade_type", "unknown")] += 1
    print(f"[+] Grenade dağılımı: {dict(grenade_counts)}")

    # Silah istatistikleri
    weapon_kills = defaultdict(int)
    for k in player_kills:
        weapon_kills[k.get("weapon", "unknown")] += 1

    # Opening duel istatistikleri (round başına ilk kill olayı)
    opening_kills = 0
    opening_deaths = 0
    by_round = defaultdict(list)
    for k in kills:
        round_num = k.get("round_num")
        if round_num not in ("", None):
            by_round[round_num].append(k)
    for _, rkills in by_round.items():
        first = min(rkills, key=lambda x: x.get("tick", float("inf")))
        if first.get("attacker_name") == player_name:
            opening_kills += 1
        if first.get("victim_name") == player_name:
            opening_deaths += 1
    opening_total = opening_kills + opening_deaths
    opening_win_rate = round((opening_kills / opening_total) * 100, 1) if opening_total > 0 else 0.0

    # Hareket metrikleri: örnekler arası yer değişimi
    player_pos = [p for p in player_positions if p.get("player_name") == player_name]
    movement_samples = len(player_pos)
    distances = []
    for i in range(1, movement_samples):
        px, py = player_pos[i - 1].get("x"), player_pos[i - 1].get("y")
        cx, cy = player_pos[i].get("x"), player_pos[i].get("y")
        try:
            dist = ((float(cx) - float(px)) ** 2 + (float(cy) - float(py)) ** 2) ** 0.5
            distances.append(dist)
        except (TypeError, ValueError):
            continue
    avg_step_distance = round(sum(distances) / len(distances), 2) if distances else 0.0
    stationary_threshold = 8.0
    stationary_ratio = round(
        (sum(1 for d in distances if d <= stationary_threshold) / len(distances)) * 100, 1
    ) if distances else 0.0

    return {
        "kills": kill_count,
        "deaths": death_count,
        "assists": assist_count,
        "kd_ratio": kd_ratio,
        "hs_rate": hs_rate,
        "adr": adr,
        "kills_per_round": kills_per_round,
        "deaths_per_round": deaths_per_round,
        "total_damage": total_damage,
        "shots_fired": shot_count,
        "shots_hit": hit_shots,
        "accuracy": accuracy,
        "opening_kills": opening_kills,
        "opening_deaths": opening_deaths,
        "opening_win_rate": opening_win_rate,
        "movement_samples": movement_samples,
        "avg_step_distance": avg_step_distance,
        "stationary_ratio": stationary_ratio,
        "grenade_usage": dict(grenade_counts),
        "weapon_kills": dict(weapon_kills),
    }


def _rule_based_findings(stats, kills, player_name, total_rounds) -> list:
    """
    İstatistiklere bakarak kural tabanlı bulgular üretir.
    Her bulgu: mesaj + ciddiyet seviyesi (high / medium / low)
    """
    findings = []

    hs_rate = stats["hs_rate"]
    kd_ratio = stats["kd_ratio"]
    adr = stats["adr"]
    deaths = stats["deaths"]
    deaths_per_round = stats["deaths_per_round"]
    accuracy = stats.get("accuracy", 0.0)
    opening_win_rate = stats.get("opening_win_rate", 0.0)
    grenades = stats["grenade_usage"]
    # Normalize edilmiş isimler: smoke, flash, molotov, incendiary, he_grenade, decoy
    smokes = grenades.get("smoke", 0)
    flashes = grenades.get("flash", 0)
    mollies = grenades.get("molotov", 0) + grenades.get("incendiary", 0)

    # Headshot oranı
    if hs_rate < 30:
        findings.append({
            "category": "Aim",
            "severity": "high",
            "message": f"Headshot oranın %{hs_rate} — bu oldukça düşük. Crosshair placement'ına odaklan, her zaman baş hizasına bak.",
        })
    elif hs_rate > 65:
        findings.append({
            "category": "Aim",
            "severity": "low",
            "message": f"Headshot oranın %{hs_rate} — çok iyi! Aim tutarlılığını korumaya devam et.",
        })

    # K/D oranı
    if kd_ratio < 0.75:
        findings.append({
            "category": "Survival",
            "severity": "high",
            "message": f"K/D oranın {kd_ratio} — her 4 rounddan fazlasında ölüyorsun. Daha pozisyonel ve temkinli oyna.",
        })
    elif kd_ratio > 1.5:
        findings.append({
            "category": "Survival",
            "severity": "low",
            "message": f"K/D oranın {kd_ratio} — maçı domine ettin, güzel iş.",
        })

    # ADR
    if adr < 60:
        findings.append({
            "category": "Impact",
            "severity": "high",
            "message": f"ADR'ın {adr} — round başına hasar düşük. Daha agresif veya daha iyi pozisyonlardan engage et.",
        })
    elif adr > 100:
        findings.append({
            "category": "Impact",
            "severity": "low",
            "message": f"ADR'ın {adr} — çok yüksek etki yarattın.",
        })

    # Utility kullanımı
    util_total = smokes + flashes + mollies
    util_per_round = util_total / max(total_rounds, 1)

    if util_per_round < 0.3:
        findings.append({
            "category": "Utility",
            "severity": "medium",
            "message": f"Utility kullanımın çok az ({util_total} toplam). Smoke, flash ve molotov/incendiary daha sık kullan — utility kazanma şansını ciddi artırır.",
        })

    if smokes == 0:
        findings.append({
            "category": "Utility",
            "severity": "medium",
            "message": "Hiç smoke atmamışsın. Smoke grenade kritik konumları kapatmak için vazgeçilmez, özellikle bombayı plant ederken.",
        })

    if flashes == 0:
        findings.append({
            "category": "Utility",
            "severity": "medium",
            "message": "Hiç flash kullanmamışsın. Flash ile peek etmek veya takım arkadaşını cover etmek büyük avantaj sağlar.",
        })

    # Aim doğruluğu
    if accuracy < 20:
        findings.append({
            "category": "Aim",
            "severity": "high",
            "message": f"Mermi doğruluğun %{accuracy}. Spray kontrolü ve ilk mermi isabeti için aim rutinleri ekle.",
        })
    elif accuracy > 35:
        findings.append({
            "category": "Aim",
            "severity": "low",
            "message": f"Mermi doğruluğun %{accuracy}. İsabet kaliten iyi, pozisyonel avantajla birleştir.",
        })

    # Opening duel
    if stats.get("opening_kills", 0) + stats.get("opening_deaths", 0) >= 3:
        if opening_win_rate < 45:
            findings.append({
                "category": "Entry",
                "severity": "medium",
                "message": f"Opening duel kazanma oranın %{opening_win_rate}. İlk temasta daha güvenli açı ve flash desteği dene.",
            })
        elif opening_win_rate > 60:
            findings.append({
                "category": "Entry",
                "severity": "low",
                "message": f"Opening duel kazanma oranın %{opening_win_rate}. Erken round impact'in güçlü.",
            })

    # Ölüm sıklığı
    if deaths_per_round > 0.7:
        findings.append({
            "category": "Survival",
            "severity": "high",
            "message": f"Neredeyse her roundda ölüyorsun (round başına {deaths_per_round:.2f} ölüm). Trade almak yerine hayatta kalmaya odaklan.",
        })

    if not findings:
        findings.append({
            "category": "Genel",
            "severity": "low",
            "message": "İstatistikler genel olarak dengeli görünüyor. Detaylı AI analizi için bir sonraki adıma geç.",
        })

    return findings


# ──────────────────────────────────────────────────────────
# Advanced Analysis Functions
# ──────────────────────────────────────────────────────────

def _normalize_side(side_val) -> str:
    """Side değerini 'T' veya 'CT' olarak normalize eder."""
    s = str(side_val).strip().lower()
    if s in ("2", "t", "terrorist"):
        return "T"
    if s in ("3", "ct", "counter-terrorist", "counterterrorist"):
        return "CT"
    return s.upper()


def _get_player_side_per_round(kills, player_positions, player_name) -> dict:
    """Her round için oyuncunun side bilgisini döner."""
    side_map = {}
    for p in player_positions:
        if p.get("player_name") == player_name:
            rn = p.get("round_num")
            s = p.get("side")
            if rn not in ("", None) and s:
                side_map[rn] = _normalize_side(s)
    for k in kills:
        rn = k.get("round_num")
        if rn in ("", None) or rn in side_map:
            continue
        if k.get("attacker_name") == player_name and k.get("attacker_side"):
            side_map[rn] = _normalize_side(k["attacker_side"])
        elif k.get("victim_name") == player_name and k.get("victim_side"):
            side_map[rn] = _normalize_side(k["victim_side"])
    return side_map


def _round_by_round_stats(kills, player_name) -> list:
    """Round bazlı kill/death istatistikleri."""
    data = {}
    for k in kills:
        rn = k.get("round_num")
        if rn in ("", None):
            continue
        try:
            rn_key = int(rn)
        except (TypeError, ValueError):
            continue
        if rn_key not in data:
            data[rn_key] = {"round": rn_key, "kills": 0, "deaths": 0, "hs_kills": 0}
        if k.get("attacker_name") == player_name:
            data[rn_key]["kills"] += 1
            if k.get("headshot"):
                data[rn_key]["hs_kills"] += 1
        if k.get("victim_name") == player_name:
            data[rn_key]["deaths"] += 1
    return [data[rn] for rn in sorted(data.keys())]


def _side_specific_stats(kills, damages, side_map, player_name) -> dict:
    """T-side ve CT-side ayrı istatistikler."""
    def _rk(v):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None

    norm_side_map = {}
    for rn, side in side_map.items():
        rk = _rk(rn)
        if rk is not None and side in ("T", "CT"):
            norm_side_map[rk] = side

    t_kills = t_deaths = t_hs = t_assists = t_damage = t_sniper_kills = 0
    ct_kills = ct_deaths = ct_hs = ct_assists = ct_damage = ct_sniper_kills = 0
    t_open_kills = t_open_deaths = 0
    ct_open_kills = ct_open_deaths = 0

    t_rounds = sum(1 for s in norm_side_map.values() if s == "T")
    ct_rounds = sum(1 for s in norm_side_map.values() if s == "CT")

    by_round = defaultdict(list)
    for k in kills:
        rk = _rk(k.get("round_num"))
        if rk is None or rk not in norm_side_map:
            continue
        by_round[rk].append(k)
        is_t = norm_side_map[rk] == "T"

        if k.get("attacker_name") == player_name:
            if is_t:
                t_kills += 1
                if k.get("headshot"):
                    t_hs += 1
                if _is_sniper_weapon(k.get("weapon")):
                    t_sniper_kills += 1
            else:
                ct_kills += 1
                if k.get("headshot"):
                    ct_hs += 1
                if _is_sniper_weapon(k.get("weapon")):
                    ct_sniper_kills += 1

        if k.get("victim_name") == player_name:
            if is_t:
                t_deaths += 1
            else:
                ct_deaths += 1

        if (
            k.get("assister_name") == player_name
            and k.get("attacker_name") != player_name
            and k.get("victim_name") != player_name
        ):
            if is_t:
                t_assists += 1
            else:
                ct_assists += 1

    for rk, rkills in by_round.items():
        if not rkills:
            continue
        first = min(rkills, key=lambda x: x.get("tick", float("inf")))
        is_t = norm_side_map[rk] == "T"
        if first.get("attacker_name") == player_name:
            if is_t:
                t_open_kills += 1
            else:
                ct_open_kills += 1
        if first.get("victim_name") == player_name:
            if is_t:
                t_open_deaths += 1
            else:
                ct_open_deaths += 1

    for d in damages:
        rk = _rk(d.get("round_num"))
        if rk is None or rk not in norm_side_map:
            continue
        if d.get("attacker_name") != player_name:
            continue
        try:
            dmg = float(d.get("hp_damage", 0))
        except (TypeError, ValueError):
            dmg = 0.0
        if norm_side_map[rk] == "T":
            t_damage += dmg
        else:
            ct_damage += dmg

    return {
        "t_side": {
            "kills": t_kills,
            "deaths": t_deaths,
            "assists": t_assists,
            "adr": round(t_damage / max(t_rounds, 1), 1),
            "kd_ratio": round(t_kills / max(t_deaths, 1), 2),
            "hs_rate": round(t_hs / max(t_kills, 1) * 100, 1),
            "kpr": round(t_kills / max(t_rounds, 1), 2),
            "dpr": round(t_deaths / max(t_rounds, 1), 2),
            "apr": round(t_assists / max(t_rounds, 1), 2),
            "opening_kills": t_open_kills,
            "opening_deaths": t_open_deaths,
            "opening_success": round(t_open_kills / max(t_open_kills + t_open_deaths, 1) * 100, 1),
            "sniper_kills": t_sniper_kills,
            "sniper_kills_per_round": round(t_sniper_kills / max(t_rounds, 1), 2),
            "rounds": t_rounds,
        },
        "ct_side": {
            "kills": ct_kills,
            "deaths": ct_deaths,
            "assists": ct_assists,
            "adr": round(ct_damage / max(ct_rounds, 1), 1),
            "kd_ratio": round(ct_kills / max(ct_deaths, 1), 2),
            "hs_rate": round(ct_hs / max(ct_kills, 1) * 100, 1),
            "kpr": round(ct_kills / max(ct_rounds, 1), 2),
            "dpr": round(ct_deaths / max(ct_rounds, 1), 2),
            "apr": round(ct_assists / max(ct_rounds, 1), 2),
            "opening_kills": ct_open_kills,
            "opening_deaths": ct_open_deaths,
            "opening_success": round(ct_open_kills / max(ct_open_kills + ct_open_deaths, 1) * 100, 1),
            "sniper_kills": ct_sniper_kills,
            "sniper_kills_per_round": round(ct_sniper_kills / max(ct_rounds, 1), 2),
            "rounds": ct_rounds,
        },
    }

def _clutch_analysis(kills, player_name) -> list:
    """1vX clutch durumlarını tespit eder."""
    by_round = defaultdict(list)
    for k in kills:
        rn = k.get("round_num")
        if rn not in ("", None):
            by_round[rn].append(k)

    clutches = []
    for rn, round_kills in by_round.items():
        sorted_kills = sorted(round_kills, key=lambda x: x.get("tick", 0))

        player_side = None
        for k in sorted_kills:
            if k.get("attacker_name") == player_name:
                player_side = _normalize_side(k.get("attacker_side", ""))
                break
            if k.get("victim_name") == player_name:
                player_side = _normalize_side(k.get("victim_side", ""))
                break
        if not player_side:
            continue

        team_alive = 5
        enemy_alive = 5
        player_dead = False
        clutch_detected = False
        clutch_vs = 0
        clutch_kills = 0

        for k in sorted_kills:
            victim_side = _normalize_side(k.get("victim_side", ""))
            victim_name = k.get("victim_name")
            is_teammate = victim_side == player_side

            if victim_name == player_name:
                player_dead = True
                team_alive -= 1
            elif is_teammate:
                team_alive -= 1
            else:
                enemy_alive -= 1
                if clutch_detected and not player_dead and k.get("attacker_name") == player_name:
                    clutch_kills += 1

            if not player_dead and team_alive == 1 and enemy_alive > 0 and not clutch_detected:
                clutch_detected = True
                clutch_vs = enemy_alive

        if clutch_detected:
            won = not player_dead and enemy_alive == 0
            clutches.append({
                "round": rn,
                "vs": clutch_vs,
                "won": won,
                "kills": clutch_kills,
            })

    return clutches


def _trade_kill_analysis(kills, player_name) -> dict:
    """Trade kill oranını hesaplar."""
    TRADE_WINDOW = 320  # ~5 saniye (64 tick)

    by_round = defaultdict(list)
    for k in kills:
        rn = k.get("round_num")
        if rn not in ("", None):
            by_round[rn].append(k)

    player_deaths = 0
    traded_deaths = 0
    player_kills_count = 0
    trade_kills = 0

    for rn, round_kills in by_round.items():
        sorted_kills = sorted(round_kills, key=lambda x: x.get("tick", 0))

        for i, k in enumerate(sorted_kills):
            tick_i = k.get("tick", 0)
            try:
                tick_i = int(tick_i)
            except (TypeError, ValueError):
                tick_i = 0

            if k.get("victim_name") == player_name:
                player_deaths += 1
                killer = k.get("attacker_name")
                for j in range(i + 1, len(sorted_kills)):
                    k2 = sorted_kills[j]
                    try:
                        tick_j = int(k2.get("tick", 0))
                    except (TypeError, ValueError):
                        continue
                    if tick_j - tick_i > TRADE_WINDOW:
                        break
                    if k2.get("victim_name") == killer:
                        traded_deaths += 1
                        break

            if k.get("attacker_name") == player_name:
                player_kills_count += 1
                for j in range(i - 1, -1, -1):
                    k2 = sorted_kills[j]
                    try:
                        tick_j = int(k2.get("tick", 0))
                    except (TypeError, ValueError):
                        continue
                    if tick_i - tick_j > TRADE_WINDOW:
                        break
                    if k2.get("attacker_name") == k.get("victim_name"):
                        trade_kills += 1
                        break

    return {
        "player_deaths": player_deaths,
        "traded_deaths": traded_deaths,
        "traded_rate": round(traded_deaths / max(player_deaths, 1) * 100, 1),
        "player_kills": player_kills_count,
        "trade_kills": trade_kills,
        "trade_kill_rate": round(trade_kills / max(player_kills_count, 1) * 100, 1),
    }


def _multi_kill_rounds(kills, player_name) -> dict:
    """3K, 4K, ACE roundlarını tespit eder."""
    by_round = defaultdict(int)
    for k in kills:
        if k.get("attacker_name") == player_name:
            rn = k.get("round_num")
            if rn not in ("", None):
                by_round[rn] += 1

    rounds_3k = [rn for rn, c in by_round.items() if c == 3]
    rounds_4k = [rn for rn, c in by_round.items() if c == 4]
    aces = [rn for rn, c in by_round.items() if c >= 5]

    return {
        "rounds_3k": sorted(rounds_3k, key=lambda x: int(x) if str(x).isdigit() else 0),
        "rounds_4k": sorted(rounds_4k, key=lambda x: int(x) if str(x).isdigit() else 0),
        "aces": sorted(aces, key=lambda x: int(x) if str(x).isdigit() else 0),
        "total_3k": len(rounds_3k),
        "total_4k": len(rounds_4k),
        "total_aces": len(aces),
    }


def _economy_analysis(rounds, kills, side_map, player_name) -> dict:
    """Ekonomi bazlı performans analizi."""
    if not rounds:
        return {"eco": {}, "force": {}, "full_buy": {}}

    kills_by_round = defaultdict(lambda: {"kills": 0, "deaths": 0})
    for k in kills:
        rn = k.get("round_num")
        if rn in ("", None):
            continue
        if k.get("attacker_name") == player_name:
            kills_by_round[rn]["kills"] += 1
        if k.get("victim_name") == player_name:
            kills_by_round[rn]["deaths"] += 1

    eco = {"kills": 0, "deaths": 0, "count": 0}
    force = {"kills": 0, "deaths": 0, "count": 0}
    full_buy = {"kills": 0, "deaths": 0, "count": 0}

    for i, r in enumerate(rounds):
        rn_candidates = [i, i + 1]
        player_side = None
        matched_rn = None
        for rn in rn_candidates:
            if rn in side_map:
                player_side = side_map[rn]
                matched_rn = rn
                break
        if not player_side:
            continue

        eq_key = "t_eq_val" if player_side == "T" else "ct_eq_val"
        eq_val = r.get(eq_key, 0)
        try:
            eq_val = int(eq_val)
        except (TypeError, ValueError):
            continue

        if eq_val < 8000:
            tier = eco
        elif eq_val < 20000:
            tier = force
        else:
            tier = full_buy

        tier["count"] += 1
        rd = kills_by_round.get(matched_rn, {"kills": 0, "deaths": 0})
        tier["kills"] += rd["kills"]
        tier["deaths"] += rd["deaths"]

    def _tier_summary(t):
        return {
            "rounds": t["count"],
            "kills": t["kills"],
            "deaths": t["deaths"],
            "kd_ratio": round(t["kills"] / max(t["deaths"], 1), 2) if t["count"] > 0 else 0,
        }

    return {
        "eco": _tier_summary(eco),
        "force": _tier_summary(force),
        "full_buy": _tier_summary(full_buy),
    }


def _flash_analysis(grenades, kills, player_name) -> dict:
    """Flash grenade kullanım ve etkinlik analizi."""
    FLASH_WINDOW = 192  # ~3 saniye (64 tick)

    player_flashes = [
        g for g in grenades
        if g.get("thrower_name") == player_name and g.get("grenade_type") == "flash"
    ]
    flash_count = len(player_flashes)

    if flash_count == 0:
        return {"flash_count": 0, "flash_assists": 0, "flash_assist_rate": 0}

    team_kills = [k for k in kills if k.get("attacker_name") != player_name]

    flash_assists = 0
    for flash in player_flashes:
        try:
            flash_tick = int(flash.get("tick", 0))
        except (TypeError, ValueError):
            continue
        if not flash_tick:
            continue
        for k in team_kills:
            try:
                kill_tick = int(k.get("tick", 0))
            except (TypeError, ValueError):
                continue
            if 0 < kill_tick - flash_tick < FLASH_WINDOW:
                flash_assists += 1
                break

    return {
        "flash_count": flash_count,
        "flash_assists": flash_assists,
        "flash_assist_rate": round(flash_assists / flash_count * 100, 1),
    }


def _death_cluster_analysis(kills, player_name) -> dict:
    """Ölüm pozisyonlarını kümeleyerek hotspot bölgeleri tespit eder."""
    positions = []
    for k in kills:
        if k.get("victim_name") == player_name:
            x = k.get("victim_x")
            y = k.get("victim_y")
            if x not in ("", None) and y not in ("", None):
                try:
                    positions.append((float(x), float(y)))
                except (TypeError, ValueError):
                    continue

    if len(positions) < 3:
        return {"clusters": [], "total": len(positions)}

    coords = list(positions)
    used = [False] * len(coords)
    clusters = []
    DIST_THRESHOLD = 400

    for i in range(len(coords)):
        if used[i]:
            continue
        cluster = [coords[i]]
        used[i] = True
        for j in range(i + 1, len(coords)):
            if used[j]:
                continue
            cx = sum(p[0] for p in cluster) / len(cluster)
            cy = sum(p[1] for p in cluster) / len(cluster)
            dist = ((coords[j][0] - cx) ** 2 + (coords[j][1] - cy) ** 2) ** 0.5
            if dist < DIST_THRESHOLD:
                cluster.append(coords[j])
                used[j] = True
        cx = sum(p[0] for p in cluster) / len(cluster)
        cy = sum(p[1] for p in cluster) / len(cluster)
        clusters.append({"center_x": round(cx, 1), "center_y": round(cy, 1), "count": len(cluster)})

    clusters.sort(key=lambda c: -c["count"])
    return {"clusters": clusters[:5], "total": len(positions)}


def _spray_transfer_detection(kills, player_name) -> list:
    """Hızlı ardışık kill (spray transfer / flick) tespiti."""
    QUICK_KILL_TICKS = 80  # ~1.25 saniye

    by_round = defaultdict(list)
    for k in kills:
        if k.get("attacker_name") == player_name:
            rn = k.get("round_num")
            if rn not in ("", None):
                by_round[rn].append(k)

    transfers = []
    for rn, round_kills in by_round.items():
        if len(round_kills) < 2:
            continue
        sorted_kills = sorted(round_kills, key=lambda x: x.get("tick", 0))
        for i in range(1, len(sorted_kills)):
            try:
                tick_diff = int(sorted_kills[i].get("tick", 0)) - int(sorted_kills[i - 1].get("tick", 0))
            except (TypeError, ValueError):
                continue
            if 0 < tick_diff <= QUICK_KILL_TICKS:
                transfers.append({
                    "round": rn,
                    "victim1": sorted_kills[i - 1].get("victim_name", "?"),
                    "victim2": sorted_kills[i].get("victim_name", "?"),
                    "tick_diff": tick_diff,
                    "time_ms": round(tick_diff / 64 * 1000),
                    "weapon": sorted_kills[i].get("weapon", "?"),
                })
    return transfers


def _kast_calculation(kills, player_name, total_rounds) -> dict:
    """KAST (Kill/Assist/Survive/Trade) metriğini hesaplar."""
    TRADE_WINDOW = 320

    by_round = defaultdict(list)
    for k in kills:
        rn = k.get("round_num")
        if rn not in ("", None):
            by_round[rn].append(k)

    kast_rounds = 0
    total_counted = len(by_round)

    for rn, round_kills in by_round.items():
        sorted_kills = sorted(round_kills, key=lambda x: x.get("tick", 0))

        has_kill = False
        has_survive = True
        has_trade = False

        for k in sorted_kills:
            if k.get("attacker_name") == player_name:
                has_kill = True
            if k.get("victim_name") == player_name:
                has_survive = False
                try:
                    death_tick = int(k.get("tick", 0))
                except (TypeError, ValueError):
                    continue
                killer = k.get("attacker_name")
                for k2 in sorted_kills:
                    try:
                        t2 = int(k2.get("tick", 0))
                    except (TypeError, ValueError):
                        continue
                    if t2 > death_tick and t2 - death_tick <= TRADE_WINDOW:
                        if k2.get("victim_name") == killer:
                            has_trade = True
                            break

        if has_kill or has_survive or has_trade:
            kast_rounds += 1

    return {
        "kast_rounds": kast_rounds,
        "total_rounds": total_counted,
        "kast_percentage": round(kast_rounds / max(total_counted, 1) * 100, 1),
    }


def _duel_analysis(kills, player_name) -> dict:
    """1v1 duel kazanma oranını hesaplar (aynı round içinde karşılıklı öldürme çiftleri)."""
    DUEL_WINDOW = 192  # ~3 saniye (64 tick)

    by_round = defaultdict(list)
    for k in kills:
        rn = k.get("round_num")
        if rn not in ("", None):
            by_round[rn].append(k)

    duels_won = 0
    duels_lost = 0

    for rn, round_kills in by_round.items():
        sorted_kills = sorted(round_kills, key=lambda x: x.get("tick", 0))
        used = set()

        for i, k in enumerate(sorted_kills):
            if i in used:
                continue
            if k.get("attacker_name") != player_name and k.get("victim_name") != player_name:
                continue

            try:
                tick_i = int(k.get("tick", 0))
            except (TypeError, ValueError):
                continue

            opponent = None
            if k.get("attacker_name") == player_name:
                opponent = k.get("victim_name")
                player_won_first = True
            elif k.get("victim_name") == player_name:
                opponent = k.get("attacker_name")
                player_won_first = False
            if not opponent:
                continue

            # Karşılıklı duel mi kontrol et
            is_duel = False
            for j in range(i + 1, len(sorted_kills)):
                if j in used:
                    continue
                k2 = sorted_kills[j]
                try:
                    tick_j = int(k2.get("tick", 0))
                except (TypeError, ValueError):
                    continue
                if tick_j - tick_i > DUEL_WINDOW:
                    break
                # Karşı tarafın öldürdüğü veya öldüğü durum
                if (k2.get("attacker_name") == opponent and k2.get("victim_name") == player_name) or \
                   (k2.get("attacker_name") == player_name and k2.get("victim_name") == opponent):
                    is_duel = True
                    used.add(j)
                    break

            if not is_duel:
                continue

            used.add(i)
            if player_won_first:
                duels_won += 1
            else:
                duels_lost += 1

    total_duels = duels_won + duels_lost
    return {
        "duels_won": duels_won,
        "duels_lost": duels_lost,
        "total_duels": total_duels,
        "duel_win_rate": round(duels_won / total_duels * 100, 1) if total_duels > 0 else None,
    }


def _utility_effectiveness(grenades, damages, kills, player_name) -> dict:
    """Utility etkinlik skoru: hasar/utility, flash assist oranı, smoke kullanım yoğunluğu."""
    player_grenades = [g for g in grenades if g.get("thrower_name") == player_name]
    total_util = len(player_grenades)

    if total_util == 0:
        return {"total_utility": 0, "utility_score": 0.0, "damage_per_util": 0.0,
                "flash_efficiency": 0.0, "smoke_count": 0}

    # Utility ile verilen hasar (molotov, incendiary, he_grenade)
    util_damage = sum(
        d.get("hp_damage", 0) for d in damages
        if d.get("attacker_name") == player_name
        and any(t in str(d.get("weapon", "")).lower() for t in ("molotov", "incendiary", "hegrenade", "he_grenade"))
    )

    # Flash asist
    FLASH_WINDOW = 192
    player_flashes = [g for g in player_grenades if g.get("grenade_type") == "flash"]
    flash_count = len(player_flashes)
    flash_assists = 0
    team_kills = [k for k in kills if k.get("attacker_name") != player_name]
    for flash in player_flashes:
        try:
            flash_tick = int(flash.get("tick", 0))
        except (TypeError, ValueError):
            continue
        if not flash_tick:
            continue
        for k in team_kills:
            try:
                kill_tick = int(k.get("tick", 0))
            except (TypeError, ValueError):
                continue
            if 0 < kill_tick - flash_tick < FLASH_WINDOW:
                flash_assists += 1
                break

    smoke_count = sum(1 for g in player_grenades if g.get("grenade_type") == "smoke")

    damage_per_util = round(util_damage / max(total_util, 1), 1)
    flash_efficiency = round(flash_assists / max(flash_count, 1) * 100, 1)

    # Bileşik skor: normalize edilmiş ağırlıklı ortalama (0-100 arası)
    score = min(100.0, round(
        (damage_per_util / 20.0) * 30 +  # hasar katkısı (max ~30)
        (flash_efficiency / 100.0) * 40 +  # flash etkinliği (max 40)
        min(smoke_count / 5.0, 1.0) * 30   # smoke kullanımı (max 30)
    , 1))

    return {
        "total_utility": total_util,
        "utility_damage": util_damage,
        "damage_per_util": damage_per_util,
        "flash_count": flash_count,
        "flash_assists": flash_assists,
        "flash_efficiency": flash_efficiency,
        "smoke_count": smoke_count,
        "utility_score": score,
    }


def _round_key(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _filter_by_rounds(events, round_set):
    if not round_set:
        return []
    out = []
    for ev in events:
        rk = _round_key(ev.get("round_num"))
        if rk is not None and rk in round_set:
            out.append(ev)
    return out


def _scope_stats(kills, damages, shots, player_name, total_rounds) -> dict:
    player_kills = [k for k in kills if k.get("attacker_name") == player_name]
    player_deaths = [k for k in kills if k.get("victim_name") == player_name]
    assist_count = sum(
        1
        for k in kills
        if k.get("assister_name") == player_name
        and k.get("attacker_name") != player_name
        and k.get("victim_name") != player_name
    )

    kill_count = len(player_kills)
    death_count = len(player_deaths)
    hs_count = sum(1 for k in player_kills if k.get("headshot"))

    total_damage = 0.0
    for d in damages:
        if d.get("attacker_name") != player_name:
            continue
        try:
            total_damage += float(d.get("hp_damage", 0))
        except (TypeError, ValueError):
            continue

    player_shots = [s for s in shots if s.get("shooter_name") == player_name and _is_bullet_weapon(s.get("weapon"))]
    player_bullet_hits = [d for d in damages if d.get("attacker_name") == player_name and _is_bullet_weapon(d.get("weapon"))]
    hit_ticks = {d.get("tick") for d in player_bullet_hits if d.get("tick") not in ("", None)}
    shot_count = len(player_shots)
    hit_shots = len(hit_ticks)
    accuracy = round((hit_shots / shot_count) * 100, 1) if shot_count > 0 else 0.0

    return {
        "rounds": int(total_rounds),
        "kills": kill_count,
        "deaths": death_count,
        "assists": assist_count,
        "hs_rate": round(hs_count / max(kill_count, 1) * 100, 1),
        "kd_ratio": round(kill_count / max(death_count, 1), 2),
        "total_damage": round(total_damage, 1),
        "adr": round(total_damage / max(total_rounds, 1), 1),
        "kpr": round(kill_count / max(total_rounds, 1), 2),
        "dpr": round(death_count / max(total_rounds, 1), 2),
        "apr": round(assist_count / max(total_rounds, 1), 2),
        "accuracy": accuracy,
    }


def _impact_rating(multi_kills, clutches, opening_kills, total_rounds) -> float:
    clutch_wins = sum(1 for c in clutches if c.get("won"))
    multi_count = (
        multi_kills.get("total_3k", 0)
        + multi_kills.get("total_4k", 0) * 1.5
        + multi_kills.get("total_aces", 0) * 2
    )
    impact_raw = (opening_kills * 0.15 + multi_count * 0.3 + clutch_wins * 0.2) / max(total_rounds, 1)
    return round(min(impact_raw * 10, 3.0), 2)


def _hltv_approx_rating(kast_pct, kpr, dpr, impact_rating, adr) -> float:
    return round(
        0.0073 * kast_pct +
        0.3591 * kpr -
        0.5329 * dpr +
        0.2372 * impact_rating +
        0.0032 * adr +
        0.1587,
        2,
    )


def _opening_metrics(kills, player_name, total_rounds) -> dict:
    by_round = defaultdict(list)
    for k in kills:
        rn = k.get("round_num")
        if rn not in ("", None):
            by_round[rn].append(k)

    opening_kills = 0
    opening_deaths = 0
    opening_attempts = opening_kills + opening_deaths
    rounds_with_kill = 0
    rounds_with_multi = 0

    for _, rkills in by_round.items():
        if not rkills:
            continue
        first = min(rkills, key=lambda x: x.get("tick", float("inf")))
        if first.get("attacker_name") == player_name:
            opening_kills += 1
        if first.get("victim_name") == player_name:
            opening_deaths += 1

        p_kills = sum(1 for ev in rkills if ev.get("attacker_name") == player_name)
        if p_kills >= 1:
            rounds_with_kill += 1
        if p_kills >= 2:
            rounds_with_multi += 1

    opening_attempts = opening_kills + opening_deaths
    opening_success = round(opening_kills / max(opening_attempts, 1) * 100, 1)
    opening_kpr = round(opening_kills / max(total_rounds, 1), 2)
    opening_dpr = round(opening_deaths / max(total_rounds, 1), 2)

    return {
        "opening_kills": opening_kills,
        "opening_deaths": opening_deaths,
        "opening_attempts": opening_attempts,
        "opening_success": opening_success,
        "opening_kills_per_round": opening_kpr,
        "opening_deaths_per_round": opening_dpr,
        "opening_attempts_per_round": round(opening_attempts / max(total_rounds, 1), 2),
        "rounds_with_kill_pct": round(rounds_with_kill / max(total_rounds, 1) * 100, 1),
        "rounds_with_multi_kill_pct": round(rounds_with_multi / max(total_rounds, 1) * 100, 1),
    }


def _sniping_metrics(kills, player_name, total_rounds) -> dict:
    by_round = defaultdict(list)
    player_kills = []
    sniper_kills = []
    sniper_opening_kills = 0

    for k in kills:
        rn = k.get("round_num")
        if rn not in ("", None):
            by_round[rn].append(k)
        if k.get("attacker_name") == player_name:
            player_kills.append(k)
            if _is_sniper_weapon(k.get("weapon")):
                sniper_kills.append(k)

    sniper_round_kills = defaultdict(int)
    for k in sniper_kills:
        rn = k.get("round_num")
        if rn in ("", None):
            continue
        sniper_round_kills[rn] += 1

    for _, rkills in by_round.items():
        first = min(rkills, key=lambda x: x.get("tick", float("inf")))
        if first.get("attacker_name") == player_name and _is_sniper_weapon(first.get("weapon")):
            sniper_opening_kills += 1

    total_kills = len(player_kills)
    sniper_count = len(sniper_kills)
    rounds_with_sniper_kill = len(sniper_round_kills)
    sniper_multi_rounds = sum(1 for v in sniper_round_kills.values() if v >= 2)

    return {
        "sniper_kills": sniper_count,
        "sniper_kills_per_round": round(sniper_count / max(total_rounds, 1), 2),
        "sniper_kill_percentage": round(sniper_count / max(total_kills, 1) * 100, 1),
        "rounds_with_sniper_kill_percentage": round(rounds_with_sniper_kill / max(total_rounds, 1) * 100, 1),
        "sniper_multi_kill_rounds": sniper_multi_rounds,
        "sniper_multi_kill_rounds_per_round": round(sniper_multi_rounds / max(total_rounds, 1), 2),
        "sniper_opening_kills": sniper_opening_kills,
        "sniper_opening_kills_per_round": round(sniper_opening_kills / max(total_rounds, 1), 2),
    }


def _pro_metrics(kills, damages, grenades, shots, player_positions, player_name, total_rounds, stats, advanced) -> dict:
    """HLTV Rating 2.0 (approx), Impact Rating, Entry Success, Duel Win Rate, Utility Score."""

    def _scope_metrics(scope_kills, scope_damages, scope_grenades, scope_shots, scope_rounds, kast_override=None):
        scope_stats = _scope_stats(scope_kills, scope_damages, scope_shots, player_name, scope_rounds)
        opening = _opening_metrics(scope_kills, player_name, scope_rounds)
        sniping = _sniping_metrics(scope_kills, player_name, scope_rounds)
        duels = _duel_analysis(scope_kills, player_name)
        util_eff = _utility_effectiveness(scope_grenades, scope_damages, scope_kills, player_name)
        trading = _trade_kill_analysis(scope_kills, player_name)
        multi_kills = _multi_kill_rounds(scope_kills, player_name)
        clutches = _clutch_analysis(scope_kills, player_name)
        clutch_won = sum(1 for c in clutches if c.get("won"))

        spr = (scope_rounds - scope_stats["deaths"]) / max(scope_rounds, 1)
        dpr = scope_stats["deaths"] / max(scope_rounds, 1)
        if kast_override is not None:
            kast_pct = float(kast_override)
        else:
            kast_pct = _kast_calculation(scope_kills, player_name, scope_rounds).get("kast_percentage", 0.0)
        impact_rating = _impact_rating(multi_kills, clutches, opening["opening_kills"], scope_rounds)
        hltv_rating = _hltv_approx_rating(kast_pct, scope_stats["kpr"], dpr, impact_rating, scope_stats["adr"])

        return {
            **scope_stats,
            "spr": round(spr, 2),
            "hltv_rating": hltv_rating,
            "impact_rating": impact_rating,
            "kast_percentage": round(kast_pct, 1),
            "entry_success_rate": opening["opening_success"],
            "duels": duels,
            "utility_effectiveness": util_eff,
            "trading": trading,
            "opening": opening,
            "sniping": sniping,
            "multi_kills": multi_kills,
            "clutches": {
                "attempts": len(clutches),
                "won": clutch_won,
                "win_rate": round(clutch_won / max(len(clutches), 1) * 100, 1) if clutches else 0.0,
            },
        }

    global_scope = _scope_metrics(
        kills,
        damages,
        grenades,
        shots,
        total_rounds,
        advanced.get("kast", {}).get("kast_percentage", 70.0),
    )

    side_map = _get_player_side_per_round(kills, player_positions, player_name)
    norm_side_map = {}
    for rn, side in side_map.items():
        rk = _round_key(rn)
        if rk is not None and side in ("T", "CT"):
            norm_side_map[rk] = side

    t_rounds = {rk for rk, side in norm_side_map.items() if side == "T"}
    ct_rounds = {rk for rk, side in norm_side_map.items() if side == "CT"}

    t_kills = _filter_by_rounds(kills, t_rounds)
    t_damages = _filter_by_rounds(damages, t_rounds)
    t_grenades = _filter_by_rounds(grenades, t_rounds)
    t_shots = _filter_by_rounds(shots, t_rounds)

    ct_kills = _filter_by_rounds(kills, ct_rounds)
    ct_damages = _filter_by_rounds(damages, ct_rounds)
    ct_grenades = _filter_by_rounds(grenades, ct_rounds)
    ct_shots = _filter_by_rounds(shots, ct_rounds)

    t_scope = _scope_metrics(t_kills, t_damages, t_grenades, t_shots, max(len(t_rounds), 0))
    ct_scope = _scope_metrics(ct_kills, ct_damages, ct_grenades, ct_shots, max(len(ct_rounds), 0))

    return {
        "hltv_rating": global_scope["hltv_rating"],
        "impact_rating": global_scope["impact_rating"],
        "kpr": global_scope["kpr"],
        "spr": global_scope["spr"],
        "dpr": global_scope["dpr"],
        "entry_success_rate": global_scope["entry_success_rate"],
        "duels": global_scope["duels"],
        "utility_effectiveness": global_scope["utility_effectiveness"],
        "opening": global_scope["opening"],
        "sniping": global_scope["sniping"],
        "sides": {
            "both": global_scope,
            "t": t_scope,
            "ct": ct_scope,
        },
    }


def _advanced_analysis(kills, damages, grenades, shots, player_positions, rounds,
                       player_name, total_rounds, map_name) -> dict:
    """Tüm gelişmiş analizleri çalıştırır."""
    side_map = _get_player_side_per_round(kills, player_positions, player_name)

    result = {
        "round_stats": _round_by_round_stats(kills, player_name),
        "side_stats": _side_specific_stats(kills, damages, side_map, player_name),
        "clutches": _clutch_analysis(kills, player_name),
        "trade_stats": _trade_kill_analysis(kills, player_name),
        "multi_kills": _multi_kill_rounds(kills, player_name),
        "economy_stats": _economy_analysis(rounds, kills, side_map, player_name),
        "flash_stats": _flash_analysis(grenades, kills, player_name),
        "death_clusters": _death_cluster_analysis(kills, player_name),
        "spray_transfers": _spray_transfer_detection(kills, player_name),
        "kast": _kast_calculation(kills, player_name, total_rounds),
    }

    return result


def _advanced_findings(advanced, stats, player_name) -> list:
    """Gelişmiş analizlerden kural tabanlı bulgular üretir."""
    findings = []

    # Side balance
    ss = advanced.get("side_stats", {})
    t_s = ss.get("t_side", {})
    ct_s = ss.get("ct_side", {})
    if t_s.get("rounds", 0) >= 3 and ct_s.get("rounds", 0) >= 3:
        t_kd = t_s.get("kd_ratio", 0)
        ct_kd = ct_s.get("kd_ratio", 0)
        if abs(t_kd - ct_kd) > 0.5:
            weak = "T-side" if t_kd < ct_kd else "CT-side"
            findings.append({
                "category": "Side Balance",
                "severity": "medium",
                "message": f"{weak} performansın belirgin şekilde düşük (T K/D: {t_kd}, CT K/D: {ct_kd}). Bu tarafta pozisyon ve strateji çalış.",
            })

    # Clutch
    clutches = advanced.get("clutches", [])
    if clutches:
        won = sum(1 for c in clutches if c["won"])
        total = len(clutches)
        pct = round(won / total * 100)
        findings.append({
            "category": "Clutch",
            "severity": "low" if won > total / 2 else "medium",
            "message": f"{total} clutch durumuna girdin, {won} tanesini kazandın (%{pct}). "
                       + ("Clutch performansın güçlü!" if won > total / 2 else "Clutch'larda daha sakin ve taktiksel oyna."),
        })

    # Trade
    ts = advanced.get("trade_stats", {})
    if ts.get("player_deaths", 0) >= 3 and ts.get("traded_rate", 0) < 30:
        findings.append({
            "category": "Teamplay",
            "severity": "medium",
            "message": f"Ölümlerinin sadece %{ts['traded_rate']}'i trade edilmiş. Takım arkadaşlarınla daha yakın pozisyon al.",
        })

    # Multi-kills
    mk = advanced.get("multi_kills", {})
    total_multi = mk.get("total_3k", 0) + mk.get("total_4k", 0) + mk.get("total_aces", 0)
    if total_multi > 0:
        parts = []
        if mk.get("total_aces"):
            parts.append(f"{mk['total_aces']} ACE")
        if mk.get("total_4k"):
            parts.append(f"{mk['total_4k']} 4K")
        if mk.get("total_3k"):
            parts.append(f"{mk['total_3k']} 3K")
        findings.append({
            "category": "Impact",
            "severity": "low",
            "message": f"Multi-kill roundlar: {', '.join(parts)}. Yüksek etkili roundlar üretebiliyorsun.",
        })

    # KAST
    kast = advanced.get("kast", {})
    kast_pct = kast.get("kast_percentage", 0)
    if kast.get("total_rounds", 0) >= 5:
        if kast_pct < 60:
            findings.append({
                "category": "Consistency",
                "severity": "high",
                "message": f"KAST oranın %{kast_pct} — roundların çoğunda etkisiz kalıyorsun. Her roundda en az bir katkı hedefle.",
            })
        elif kast_pct > 75:
            findings.append({
                "category": "Consistency",
                "severity": "low",
                "message": f"KAST oranın %{kast_pct} — tutarlı bir performans sergiliyorsun.",
            })

    # Flash
    fs = advanced.get("flash_stats", {})
    if fs.get("flash_count", 0) > 0 and fs.get("flash_assist_rate", 0) > 30:
        findings.append({
            "category": "Utility",
            "severity": "low",
            "message": f"Flash'larının %{fs['flash_assist_rate']}'i kill ile sonuçlandı. Flash kullanımın etkili.",
        })

    # Spray transfers
    transfers = advanced.get("spray_transfers", [])
    if len(transfers) >= 2:
        findings.append({
            "category": "Mechanics",
            "severity": "low",
            "message": f"{len(transfers)} hızlı spray transfer/flick tespit edildi. Mekanik becerin güçlü.",
        })

    # Death clusters
    dc = advanced.get("death_clusters", {})
    clusters = dc.get("clusters", [])
    if clusters and clusters[0].get("count", 0) >= 3:
        top = clusters[0]
        findings.append({
            "category": "Positioning",
            "severity": "medium",
            "message": f"Bir bölgede {top['count']} kez öldün (koordinat: {top['center_x']:.0f}, {top['center_y']:.0f}). Bu pozisyonu değiştirmeyi veya farklı açı kullanmayı dene.",
        })

    # Economy
    eco_stats = advanced.get("economy_stats", {})
    eco_tier = eco_stats.get("eco", {})
    full_tier = eco_stats.get("full_buy", {})
    if eco_tier.get("rounds", 0) >= 2 and eco_tier.get("kd_ratio", 0) > 1.0:
        findings.append({
            "category": "Economy",
            "severity": "low",
            "message": f"Eco roundlarda K/D: {eco_tier['kd_ratio']} — düşük bütçeyle bile etkili olabiliyorsun.",
        })
    if full_tier.get("rounds", 0) >= 3 and full_tier.get("kd_ratio", 0) < 0.8:
        findings.append({
            "category": "Economy",
            "severity": "medium",
            "message": f"Full buy roundlarda K/D: {full_tier['kd_ratio']} — tam ekipmanla daha iyi performans beklenir. Pozisyon seçimini gözden geçir.",
        })

    return findings

