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
Toplam hasar: {stats['total_damage']}
Silah bazlı kill: {weapon_str}
Grenade kullanımı: {grenade_str}

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
