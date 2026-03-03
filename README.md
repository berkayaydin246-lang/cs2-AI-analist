# CS2 AI Coach

## 1) Project Overview (English)

CS2 AI Coach is a Streamlit application that analyzes Counter-Strike 2 demo files (`.dem`) and produces professional coaching reports powered by Anthropic Claude.

### v0.2 — What's New

**Advanced Analytics Engine**
- **KAST** (Kill / Assist / Survive / Trade) metric per match
- **T-side vs CT-side** split stats: separate K/D ratios and round counts per side
- **Multi-kill rounds**: 3K, 4K, and ACE detection
- **Trade analysis**: traded-death rate and trade-kill rate (within a 5-second window)
- **Clutch detection**: 1vX situations identified with win/loss outcomes
- **Economy analysis**: eco / force-buy / full-buy round K/D broken down
- **Flash analysis**: flash assists and self-flash tracking
- **Death clustering**: recurring death spots grouped by proximity
- **Spray transfer detection**: multi-kill sequences within a 2-second window
- **Round-by-round timeline**: K/D chart across all rounds

**Heatmap — T-side / CT-side Split**
- Two separate heatmaps generated per analysis: one for T-side rounds, one for CT-side
- Uses `awpy get maps` radar PNG (RGBA with alpha channel) as the map boundary mask — pixel-accurate, covers B site and T-spawn correctly
- Gaussian smoothing sigma tuned to 16 px for fluid corridor coverage
- Percentile-based normalization (98th) prevents spawn dominance
- Density threshold lowered to 0.001 so low-traffic corridors remain visible

**AI Coaching Prompt**
- Advanced stats (KAST, side K/D, multi-kills, trades, clutches, eco) injected into Claude prompt
- More specific, data-driven feedback per match

### How it works
1. Parse demo with `awpy`
2. Build structured match data (`kills`, `damages`, `grenades`, `rounds`, `player_positions`)
3. Run player analysis — basic + advanced — in `src/analyzer.py`
4. Generate coaching report in `src/coach.py` (Claude Opus 4.6)
5. Show results in Streamlit (`app.py`)

---

## 2) Proje Açıklaması (Türkçe)

CS2 AI Coach, Counter-Strike 2 demo dosyalarını analiz eden ve Anthropic Claude ile profesyonel koçluk raporu üreten bir Streamlit uygulamasıdır.

### v0.2 — Yeni Özellikler

**Gelişmiş Analiz Motoru**
- **KAST** (Kill / Assist / Survive / Trade) metriği
- **T-side / CT-side** ayrımı: taraf bazlı K/D ve round sayısı
- **Multi-kill roundlar**: 3K, 4K ve ACE tespiti
- **Trade analizi**: trade edilme oranı ve trade kill oranı (5 saniyelik pencere)
- **Clutch tespiti**: 1vX durumları ve sonuçları
- **Ekonomi analizi**: eco / force-buy / full-buy round bazlı K/D
- **Flash analizi**: flash asist ve self-flash sayıları
- **Ölüm kümeleme**: tekrarlayan ölüm noktaları mesafe bazlı gruplandırılır
- **Spray transfer tespiti**: 2 saniye içinde çoklu kill dizileri
- **Round bazlı zaman çizelgesi**: tüm roundlar boyunca K/D grafiği

**Isı Haritası — T-side / CT-side Ayrımı**
- Her analiz için iki ayrı ısı haritası: T-tarafı ve CT-tarafı
- `awpy get maps` ile indirilen RGBA PNG radar dosyası alpha kanalıyla piksel hassasiyetinde sınır maskesi
- Gaussian sigma = 16 px: koridor boyunca akıcı ısı dağılımı
- 98. yüzdelik normalizasyon: spawn baskınlığını önler
- Düşük density eşiği (0.001): seyrek ziyaret edilen koridorlar da görünür

**AI Koçluk Promptu**
- Gelişmiş istatistikler (KAST, taraf K/D, multi-kill, trade, clutch, ekonomi) Claude promptuna eklendi
- Maça ve oyuncuya özel, uygulanabilir geri bildirim

### Çalışma Akışı
1. Demo `awpy` ile parse edilir
2. Maç verisi yapılandırılır (`kills`, `damages`, `grenades`, `rounds`, `player_positions`)
3. `src/analyzer.py` ile temel + gelişmiş oyuncu analizi yapılır
4. `src/coach.py` ile AI raporu üretilir (Claude Opus 4.6)
5. Sonuçlar `app.py` üzerinden Streamlit arayüzünde gösterilir

---

## 3) Usage (English)

### Requirements
- Python 3.11+
- Anthropic API key

### Setup
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

Install dependencies:
```bash
pip install -r requirements.txt
```

Download map radar files (required for heatmap):
```bash
awpy get maps
```

Create `.env`:
```env
ANTHROPIC_API_KEY=sk-ant-...
```

Put your demo file into `demos/`.

### Run
```bash
streamlit run app.py
```

Open: `http://localhost:8501`

---

## 4) Kullanım (Türkçe)

### Gereksinimler
- Python 3.11+
- Anthropic API key

### Kurulum
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

Bağımlılıkları yükle:
```bash
pip install -r requirements.txt
```

Harita radar dosyalarını indir (ısı haritası için gerekli):
```bash
awpy get maps
```

`.env` dosyasını oluştur:
```env
ANTHROPIC_API_KEY=sk-ant-...
```

Demo dosyanı `demos/` klasörüne koy.

### Çalıştırma
```bash
streamlit run app.py
```

Tarayıcıdan aç: `http://localhost:8501`

---

## 5) Repository Structure
```text
cs2-coach/
├── .streamlit/
│   └── config.toml
├── demos/               # .dem dosyalarını buraya koy
├── outputs/             # Üretilen raporlar ve görseller
├── README.md
├── app.py               # Streamlit arayüzü
├── requirements.txt
└── src/
    ├── __init__.py
    ├── analyzer.py      # Temel + gelişmiş analiz (KAST, clutch, trade, eco...)
    ├── coach.py         # Claude API ile koçluk raporu üretimi
    ├── parser.py        # awpy demo parser
    └── utils.py         # Heatmap, koordinat dönüşümü, yardımcı fonksiyonlar
```

> Harita radar görselleri `awpy get maps` ile `~/.awpy/maps/` dizinine indirilir,
> proje dizinine kopyalanması gerekmez.
