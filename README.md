# CS2 AI Coach

> Counter-Strike 2 demo analiz aracı / CS2 demo analysis tool
> Powered by [awpy](https://github.com/pnxenopoulos/awpy) + [Anthropic Claude](https://anthropic.com)

---

## Table of Contents / İçindekiler

1. [Project Overview (English)](#1-project-overview-english)
2. [Proje Açıklaması (Türkçe)](#2-proje-açıklaması-türkçe)
3. [Usage / Kullanım](#3-usage--kullanım)
4. [Repository Structure](#4-repository-structure)
5. [Changelog / Sürüm Notları](#5-changelog--sürüm-notları)

---

## 1) Project Overview (English)

CS2 AI Coach is a Streamlit application that analyzes Counter-Strike 2 demo files (`.dem`) and produces professional coaching reports powered by Anthropic Claude.

### Features (v0.3 — current)

**Demo Parsing**
- Parses `.dem` files via `awpy 2.x` (Polars DataFrame backend)
- Extracts kills, damages, grenades, shots, player positions, and round data

**Statistics & Analytics**
- Basic: K/D, ADR, headshot %, accuracy, weapon breakdown
- **KAST** (Kill / Assist / Survive / Trade) percentage
- **T-side vs CT-side** split: separate K/D, kills, deaths, HS% per side
- **Multi-kill rounds**: 3K, 4K, ACE detection
- **Trade analysis**: traded-death rate and trade-kill rate (5-second window)
- **Clutch detection**: 1vX situations with win/loss outcomes
- **Economy analysis**: eco / force-buy / full-buy round K/D
- **Flash analysis**: flash assists and self-flash count
- **Death clustering**: recurring death spots grouped by proximity
- **Spray transfer detection**: multi-kill sequences within 2 seconds
- **Round-by-round timeline**: K/D bar chart across all rounds

**Visualizations**
- **Movement heatmap** (T-side / CT-side split) — Gaussian-smoothed, RGBA PNG masked
- **Death map** — death positions as numbered X markers on radar
- **Utility map** — grenade landing spots color-coded by type, with trajectory lines
- **Round route GIF** — animated route per round with plasma gradient path and death marker

**AI Coaching**
- Optional Claude-powered coaching report (requires Anthropic API key)
- Advanced stats injected into prompt for data-driven, match-specific feedback

### How It Works
1. Parse demo with `awpy` → structured JSON
2. Run player analysis (basic + advanced) in `src/analyzer.py`
3. Generate coaching report in `src/coach.py` (Claude Opus 4.6) — optional
4. Display results in Streamlit (`app.py`)

---

## 2) Proje Açıklaması (Türkçe)

CS2 AI Coach, Counter-Strike 2 demo dosyalarını analiz eden ve Anthropic Claude ile profesyonel koçluk raporu üreten bir Streamlit uygulamasıdır.

### Özellikler (v0.3 — güncel)

**Demo Parsing**
- `.dem` dosyaları `awpy 2.x` ile parse edilir (Polars DataFrame backend)
- Kill, hasar, grenade, atış, oyuncu pozisyonu ve round verisi çıkarılır

**İstatistik ve Analiz**
- Temel: K/D, ADR, headshot %, isabet oranı, silah dağılımı
- **KAST** (Kill / Assist / Survive / Trade) yüzdesi
- **T-side / CT-side ayrımı**: taraf bazlı K/D, kill, death, HS%
- **Multi-kill roundlar**: 3K, 4K ve ACE tespiti
- **Trade analizi**: trade edilme oranı ve trade kill oranı (5 saniyelik pencere)
- **Clutch tespiti**: 1vX durumları ve kazanma/kaybetme sonuçları
- **Ekonomi analizi**: eco / force-buy / full-buy round bazlı K/D
- **Flash analizi**: flash asist ve self-flash sayısı
- **Ölüm kümeleme**: tekrarlayan ölüm noktaları mesafe bazlı gruplandırılır
- **Spray transfer tespiti**: 2 saniye içinde ardışık kill dizileri
- **Round zaman çizelgesi**: tüm roundlar boyunca K/D bar grafiği

**Görselleştirme**
- **Hareket ısı haritası** (T-side / CT-side ayrımı) — Gaussian düzleştirme, RGBA PNG maskesi
- **Ölüm haritası** — ölüm pozisyonları numaralı X işaretleriyle radar üzerinde
- **Utility haritası** — grenade iniş noktaları türe göre renkli, trajectory çizgileriyle
- **Round rota GIF** — her round için plasma gradyanlı animasyonlu rota + ölüm işaretçisi

**AI Koçluk**
- İsteğe bağlı Claude koçluk raporu (Anthropic API key gerektirir)
- Gelişmiş istatistikler Claude promptuna enjekte edilir — maça özel geri bildirim

### Çalışma Akışı
1. Demo `awpy` ile parse edilir → yapılandırılmış JSON
2. `src/analyzer.py` ile temel + gelişmiş oyuncu analizi
3. `src/coach.py` ile AI raporu üretilir (Claude Opus 4.6) — isteğe bağlı
4. Sonuçlar `app.py` üzerinden Streamlit arayüzünde gösterilir

---

## 3) Usage / Kullanım

### Requirements / Gereksinimler
- Python 3.11+
- Anthropic API key (AI coaching için / for AI coaching — optional)

### Setup / Kurulum

```bash
python -m venv venv
```

Windows:
```bash
venv\Scripts\activate
```

macOS/Linux:
```bash
source venv/bin/activate
```

Install dependencies / Bağımlılıkları yükle:
```bash
pip install -r requirements.txt
```

Download map radar files / Harita radar dosyalarını indir (heatmap için gerekli):
```bash
awpy get maps
```

Create `.env` / `.env` oluştur:
```env
ANTHROPIC_API_KEY=sk-ant-...
```

Put your demo file into `demos/` / Demo dosyanı `demos/` klasörüne koy.

### Run / Çalıştır

```bash
streamlit run app.py
```

Open / Aç: `http://localhost:8501`

---

## 4) Repository Structure

```text
cs2-coach/
├── .streamlit/
│   └── config.toml
├── demos/               # Place .dem files here / .dem dosyaları buraya
├── outputs/             # Generated reports & visuals / Üretilen raporlar ve görseller
├── README.md
├── app.py               # Streamlit UI
├── requirements.txt
└── src/
    ├── __init__.py
    ├── analyzer.py      # Basic + advanced player analysis
    ├── coach.py         # Claude API coaching report generation
    ├── parser.py        # awpy demo parser (Polars-compatible)
    └── utils.py         # Heatmap, death map, utility map, GIF, coordinate helpers
```

> Map radar images are downloaded by `awpy get maps` to `~/.awpy/maps/` — no need to copy them into the project directory.
> Harita radar görselleri `awpy get maps` ile `~/.awpy/maps/` dizinine indirilir, proje dizinine kopyalanması gerekmez.

---

## 5) Changelog / Sürüm Notları

### v0.3 — Death & Utility Maps + Parser Fix
**EN**
- Fixed critical bug: `victim_X`/`victim_Y` (awpy Polars uppercase columns) were silently dropped, causing empty death maps — now correctly normalized
- Fixed grenade coordinate extraction: `X`/`Y`/`Z` columns now mapped to `nade_x`/`nade_y`/`nade_z`
- **Death map**: death positions displayed as numbered red X markers on radar (separate from utility)
- **Utility map**: grenade landing spots color-coded by type (smoke/flash/HE/molotov/decoy) with trajectory lines — separate map
- Schema version bumped to 5 (triggers automatic re-parse of cached demos)
- Shared helpers added to `utils.py`: `_MAP_INFO`, `_load_radar_img`, `_game_to_pixel`

**TR**
- Kritik hata düzeltildi: `victim_X`/`victim_Y` (awpy Polars büyük harf kolonlar) sessizce düşürülüyordu, ölüm haritaları boş geliyordu — artık doğru normalize ediliyor
- Grenade koordinat çıkarımı düzeltildi: `X`/`Y`/`Z` kolonları artık `nade_x`/`nade_y`/`nade_z`'ye doğru eşleniyor
- **Ölüm haritası**: ölüm pozisyonları numaralı kırmızı X işaretleriyle radar üzerinde gösteriliyor (utility'den ayrı)
- **Utility haritası**: grenade iniş noktaları türe göre renkli (smoke/flash/HE/molotov/decoy) + trajectory çizgileri — ayrı harita
- Schema versiyonu 5'e yükseltildi (önbelleğe alınmış demolar otomatik yeniden parse edilir)

---

### v0.2.1 — Round Route Animation GIF
**EN**
- Animated GIF showing player route for each round on the radar map
- Plasma gradient colored path (start → end), growing frame by frame
- Death position marked with red X on the final frame
- Side filter (T / CT / All) and speed/frame controls in the UI
- GIF download button

**TR**
- Her round için oyuncunun rotasını radar harita üzerinde gösteren animasyonlu GIF
- Plasma gradyanlı renkli rota (başlangıç → bitiş), kare kare büyüyerek çizilir
- Son karede kırmızı X ile ölüm pozisyonu gösterilir
- Arayüzde taraf filtresi (T / CT / Tümü) ve hız/kare kontrolü
- GIF indirme butonu

---

### v0.2 — Advanced Analytics + T/CT Heatmap Split
**EN**
- **KAST** metric (Kill / Assist / Survive / Trade percentage)
- **T-side vs CT-side** split stats: separate K/D, kills, deaths, HS% per side
- **Multi-kill detection**: 3K, 4K, ACE rounds
- **Trade analysis**: traded-death rate and trade-kill rate (5-second window)
- **Clutch detection**: 1vX situations with outcomes
- **Economy analysis**: K/D breakdown for eco / force-buy / full-buy rounds
- **Flash analysis**: flash assists and self-flash tracking
- **Death clustering**: recurring death spots grouped by proximity
- **Spray transfer detection**: rapid multi-kills within 2-second window
- **Round timeline**: K/D bar chart per round
- Movement heatmap split into T-side and CT-side maps
- awpy RGBA PNG used as pixel-accurate map mask (fixes B site / T-spawn masking)
- Advanced stats injected into Claude coaching prompt

**TR**
- **KAST** metriği (Kill / Assist / Survive / Trade yüzdesi)
- **T-side / CT-side ayrımı**: taraf bazlı K/D, kill, death, HS%
- **Multi-kill tespiti**: 3K, 4K, ACE roundlar
- **Trade analizi**: trade edilme ve trade kill oranları (5 saniyelik pencere)
- **Clutch tespiti**: 1vX durumları ve sonuçları
- **Ekonomi analizi**: eco / force-buy / full-buy round bazlı K/D
- **Flash analizi**: flash asist ve self-flash takibi
- **Ölüm kümeleme**: tekrarlayan ölüm noktaları mesafe bazlı gruplandırma
- **Spray transfer tespiti**: 2 saniye içinde ardışık kill dizileri
- **Round zaman çizelgesi**: round başına K/D bar grafiği
- Hareket ısı haritası T-side ve CT-side olarak iki ayrı haritaya bölündü
- awpy RGBA PNG ile piksel hassasiyetinde harita maskesi (B site ve T spawn dahil)
- Gelişmiş istatistikler Claude koçluk promptuna eklendi

---

### v0.1 — Initial Release
**EN**
- Parse CS2 demo files via `awpy`
- Basic player stats: K/D, ADR, headshot %, accuracy, weapon kill breakdown
- Rule-based findings (low K/D, poor accuracy, etc.)
- Optional Claude AI coaching report
- Streamlit UI with demo file upload and player selector

**TR**
- `awpy` ile CS2 demo dosyası parse etme
- Temel oyuncu istatistikleri: K/D, ADR, headshot %, isabet oranı, silah kill dağılımı
- Kural tabanlı bulgular (düşük K/D, zayıf isabet vb.)
- İsteğe bağlı Claude AI koçluk raporu
- Demo yükleme ve oyuncu seçimi içeren Streamlit arayüzü
