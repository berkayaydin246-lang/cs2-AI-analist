"""
coach.py
Analiz verisini Claude API'ye gönderir ve profesyonel coaching raporu üretir.
"""

import os
import json
import anthropic
from dotenv import load_dotenv

load_dotenv()


def get_coaching(analysis: dict) -> str:
    """
    Analiz verisini Claude'a gönderir, coaching raporu döner.
    """
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    player = analysis["player"]
    stats = analysis["stats"]
    findings = analysis["findings"]
    total_rounds = analysis["total_rounds"]
    harita = analysis["map"]

    findings_text = "\n".join(
        [f"- [{f['category']} / {f['severity'].upper()}] {f['message']}" for f in findings]
    )

    weapon_kills = stats.get("weapon_kills", {})
    weapon_str = ", ".join([f"{w}: {k} kill" for w, k in sorted(weapon_kills.items(), key=lambda x: -x[1])[:5]])

    # Advanced analysis data
    advanced = analysis.get("advanced", {})
    kast = advanced.get("kast", {})
    side_stats = advanced.get("side_stats", {})
    multi_kills = advanced.get("multi_kills", {})
    trade_stats = advanced.get("trade_stats", {})
    clutches = advanced.get("clutches", [])
    eco_stats = advanced.get("economy_stats", {})
    clutch_won = sum(1 for c in clutches if c.get("won"))
    clutch_total = len(clutches)

    # Pre-compute nested values for f-string safety
    t_side = side_stats.get("t_side", {})
    ct_side = side_stats.get("ct_side", {})
    t_kd = t_side.get("kd_ratio", 0)
    t_rounds = t_side.get("rounds", 0)
    ct_kd = ct_side.get("kd_ratio", 0)
    ct_rounds = ct_side.get("rounds", 0)
    eco_kd = eco_stats.get("eco", {}).get("kd_ratio", 0)
    full_buy_kd = eco_stats.get("full_buy", {}).get("kd_ratio", 0)

    grenade_str = json.dumps(stats.get("grenade_usage", {}), ensure_ascii=False)

    prompt = f"""
Sen deneyimli bir Counter-Strike 2 koçusun. Aşağıdaki maç verisini analiz et ve oyuncuya spesifik, uygulanabilir geri bildirim ver.

OYUNCU: {player}
HARİTA: {harita}
TOPLAM ROUND: {total_rounds}

--- İSTATİSTİKLER ---
K/D: {stats['kd_ratio']}
Kill: {stats['kills']} | Death: {stats['deaths']}
ADR (round başına hasar): {stats['adr']}
Headshot oranı: %{stats['hs_rate']}
Mermi doğruluğu: %{stats.get('accuracy', 0)} (isabetli atış: {stats.get('shots_hit', 0)} / toplam atış: {stats.get('shots_fired', 0)})
Opening duel: {stats.get('opening_kills', 0)} kill / {stats.get('opening_deaths', 0)} death (win rate %{stats.get('opening_win_rate', 0)})
Hareket davranışı: stationary ratio %{stats.get('stationary_ratio', 0)}, ortalama adım mesafesi {stats.get('avg_step_distance', 0)}
Toplam hasar: {stats['total_damage']}
Silah bazlı kill: {weapon_str}
Grenade kullanımı: {grenade_str}

--- GELİŞMİŞ ANALİZ ---
KAST: %{kast.get('kast_percentage', 0)}
T-side K/D: {t_kd} ({t_rounds} round) | CT-side K/D: {ct_kd} ({ct_rounds} round)
Multi-kill roundlar: {multi_kills.get('total_3k', 0)} 3K, {multi_kills.get('total_4k', 0)} 4K, {multi_kills.get('total_aces', 0)} ACE
Trade edilme oranı: %{trade_stats.get('traded_rate', 0)} | Trade kill oranı: %{trade_stats.get('trade_kill_rate', 0)}
Clutch: {clutch_total} durum, {clutch_won} kazanılan
Eco round K/D: {eco_kd} | Full buy K/D: {full_buy_kd}

--- KURAL TABANLI BULGULAR ---
{findings_text}

---

Lütfen aşağıdaki formatta bir coaching raporu yaz:

🎯 GENEL DEĞERLENDİRME
(Bu oyuncunun maçtaki genel performansını 2-3 cümlede özetle)

❌ KRİTİK HATALAR (En fazla 3 tane)
Her hata için:
- Hata neydi?
- Neden hata?
- Nasıl düzeltmeli?

✅ İYİ YAPILAN ŞEYLER
(1-2 pozitif nokta, eğer varsa)

🏋️ BU HAFTA ÇALIŞILMASI GEREKEN 1 ŞEY
(Tek, odaklı bir gelişim önerisi. Pratik yapılabilir olsun.)

⚡ HIZLI İPUCU
(Bu oyuncu tipi için 1 cümlelik keskin tavsiye)

Türkçe yaz. Genel tavsiyeler değil, bu maça ve bu istatistiklere özel konuş.
"""

    print("[+] Claude'a gönderiliyor...")
    
    message = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )

    return message.content[0].text


def get_scouting_report(team_analysis: dict, target_team: str = "team2") -> str:
    """
    Team analysis verisinden rakip scouting raporu uretir.
    """
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    teams = team_analysis.get("teams", {})
    target = teams.get(target_team, {})
    target_name = target.get("name", target_team)
    target_players = target.get("players", [])
    target_agg = target.get("aggregate", {})

    scoreboard = [
        row for row in team_analysis.get("scoreboard", [])
        if row.get("team") == target_team
    ]
    scoreboard_str = "\n".join(
        [
            (
                f"- {r.get('player', '?')}: "
                f"R={r.get('rating', 0)} | K/D={r.get('kd_ratio', 0)} | "
                f"ADR={r.get('adr', 0)} | KAST={r.get('kast', 0)}"
            )
            for r in scoreboard
        ]
    ) or "- Veri yok"

    ct_setups = [
        s for s in team_analysis.get("ct_setups", [])
        if s.get("team") == target_team
    ]
    setups_str = "\n".join(
        [
            f"- R{s.get('round')}: {s.get('setup_type')} | A: {s.get('a_players', '-') } | B: {s.get('b_players', '-')}"
            for s in ct_setups[:12]
        ]
    ) or "- Veri yok"

    executes = [
        e for e in team_analysis.get("t_executes", [])
        if e.get("team") == target_team
    ]
    exec_str = "\n".join(
        [
            (
                f"- R{e.get('round')}: {e.get('site')} execute | "
                f"site_kills={e.get('site_kills', 0)} | contact={e.get('first_contact_s', 0)}s"
            )
            for e in executes[:12]
        ]
    ) or "- Veri yok"

    coord = team_analysis.get("coordination", {}).get(target_team, {})
    tags = team_analysis.get("round_tags", [])
    tag_summary = {}
    for row in tags:
        for t in row.get("all_tags", row.get("tags", [])):
            tag_summary[t] = tag_summary.get(t, 0) + 1
    tag_str = ", ".join([f"{k}:{v}" for k, v in sorted(tag_summary.items(), key=lambda x: -x[1])[:10]]) or "Yok"

    prompt = f"""
Sen profesyonel bir CS2 analyst/coachsın. Aşağıdaki tek demo scouting datasına göre rakip takım analizi çıkar.

HEDEF TAKIM: {target_name} ({target_team})
OYUNCULAR: {", ".join(target_players) if target_players else "Veri yok"}
HARİTA: {team_analysis.get("map", "unknown")}
ROUND: {team_analysis.get("total_rounds", 0)}

-- TEAM AGGREGATE --
Kills: {target_agg.get("kills", 0)}
Deaths: {target_agg.get("deaths", 0)}
Team ADR: {target_agg.get("team_adr", 0)}
Team KAST: {target_agg.get("team_kast", 0)}
Avg Rating: {target_agg.get("avg_rating", 0)}
Opening Kills/Deaths: {target_agg.get("opening_kills", 0)}/{target_agg.get("opening_deaths", 0)}

-- SCOREBOARD --
{scoreboard_str}

-- CT SETUPS --
{setups_str}

-- T EXECUTES --
{exec_str}

-- COORDINATION --
Coordination score: {coord.get("coordination_score", 0)}
Traded rate: %{coord.get("traded_rate", 0)}
Avg refrag: {coord.get("avg_refrag_ms", 0)} ms
Flash combo rate: %{coord.get("flash_combo_rate", 0)}

-- ROUND TAG SUMMARY --
{tag_str}

Lütfen şu formatta ve Türkçe cevap ver:
1) Rakibin oyun kimliği (3-5 madde)
2) En güçlü 3 oyuncu ve nedenleri
3) Zayıf noktalar / kırılabilir patternler
4) Bizim için anti-strat planı (T ve CT için ayrı)
5) Maç içi hızlı adaptasyon checklist'i (6-8 kısa madde)

Analiz teknik ve uygulanabilir olsun; genel geçer cümlelerden kaçın.
"""

    message = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )

    return message.content[0].text


def save_report(report: str, player_name: str, output_dir: str = "outputs"):
    """Raporu txt dosyası olarak kaydeder."""
    import os
    from datetime import datetime

    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{output_dir}/coaching_{player_name}_{timestamp}.txt"

    with open(filename, "w", encoding="utf-8") as f:
        f.write(f"CS2 Coaching Raporu — {player_name}\n")
        f.write("=" * 50 + "\n\n")
        f.write(report)

    print(f"[+] Rapor kaydedildi: {filename}")
    return filename
