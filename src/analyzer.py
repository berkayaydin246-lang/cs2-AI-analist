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


def _calculate_stats(kills, damages, grenades, shots, player_positions, player_name, total_rounds) -> dict:
    """Temel istatistikleri hesaplar."""

    # Kill istatistikleri
    player_kills = [k for k in kills if k.get("attacker_name") == player_name]
    player_deaths = [k for k in kills if k.get("victim_name") == player_name]

    kill_count = len(player_kills)
    death_count = len(player_deaths)
    assist_count = 0  # assist verisi varsa eklenebilir

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


def _side_specific_stats(kills, side_map, player_name) -> dict:
    """T-side ve CT-side ayrı istatistikler."""
    t_kills, t_deaths, t_hs = 0, 0, 0
    ct_kills, ct_deaths, ct_hs = 0, 0, 0
    t_rounds = sum(1 for s in side_map.values() if s == "T")
    ct_rounds = sum(1 for s in side_map.values() if s == "CT")

    for k in kills:
        rn = k.get("round_num")
        if rn in ("", None) or rn not in side_map:
            continue
        is_t = side_map[rn] == "T"

        if k.get("attacker_name") == player_name:
            if is_t:
                t_kills += 1
                if k.get("headshot"):
                    t_hs += 1
            else:
                ct_kills += 1
                if k.get("headshot"):
                    ct_hs += 1
        if k.get("victim_name") == player_name:
            if is_t:
                t_deaths += 1
            else:
                ct_deaths += 1

    return {
        "t_side": {
            "kills": t_kills, "deaths": t_deaths,
            "kd_ratio": round(t_kills / max(t_deaths, 1), 2),
            "hs_rate": round(t_hs / max(t_kills, 1) * 100, 1),
            "rounds": t_rounds,
        },
        "ct_side": {
            "kills": ct_kills, "deaths": ct_deaths,
            "kd_ratio": round(ct_kills / max(ct_deaths, 1), 2),
            "hs_rate": round(ct_hs / max(ct_kills, 1) * 100, 1),
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


def _advanced_analysis(kills, damages, grenades, shots, player_positions, rounds,
                       player_name, total_rounds, map_name) -> dict:
    """Tüm gelişmiş analizleri çalıştırır."""
    side_map = _get_player_side_per_round(kills, player_positions, player_name)

    return {
        "round_stats": _round_by_round_stats(kills, player_name),
        "side_stats": _side_specific_stats(kills, side_map, player_name),
        "clutches": _clutch_analysis(kills, player_name),
        "trade_stats": _trade_kill_analysis(kills, player_name),
        "multi_kills": _multi_kill_rounds(kills, player_name),
        "economy_stats": _economy_analysis(rounds, kills, side_map, player_name),
        "flash_stats": _flash_analysis(grenades, kills, player_name),
        "death_clusters": _death_cluster_analysis(kills, player_name),
        "spray_transfers": _spray_transfer_detection(kills, player_name),
        "kast": _kast_calculation(kills, player_name, total_rounds),
    }


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
