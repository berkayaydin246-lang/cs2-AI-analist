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
    rounds = parsed_data.get("rounds", [])
    total_rounds = parsed_data.get("total_rounds", 1)

    stats = _calculate_stats(kills, damages, grenades, player_name, total_rounds)
    findings = _rule_based_findings(stats, kills, player_name, total_rounds)

    return {
        "player": player_name,
        "map": parsed_data.get("map", "unknown"),
        "total_rounds": total_rounds,
        "stats": stats,
        "findings": findings,
    }


def _calculate_stats(kills, damages, grenades, player_name, total_rounds) -> dict:
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
