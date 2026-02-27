# CS2 AI Coach

## 1) Project Overview (English)
CS2 AI Coach is a Streamlit application that analyzes Counter-Strike 2 demo files (`.dem`) and produces:
- Player performance statistics (K/D, ADR, HS rate, weapon distribution)
- Rule-based findings
- Map visualization (death points + utility trajectories)
- AI coaching report generated with Anthropic Claude

### How it works
1. Parse demo with `awpy`
2. Build structured match data (`kills`, `damages`, `grenades`, `rounds`, `players`)
3. Run player analysis in `src/analyzer.py`
4. Generate coaching report in `src/coach.py`
5. Show results in Streamlit (`app.py`)

---

## 2) Proje Açıklaması (Türkçe)
CS2 AI Coach, Counter-Strike 2 demo dosyalarını (`.dem`) analiz eden bir Streamlit uygulamasıdır. Uygulama:
- Oyuncu istatistiklerini çıkarır (K/D, ADR, HS oranı, silah dağılımı)
- Kural tabanlı bulgular üretir
- Harita üzerinde ölüm noktaları ve utility rotalarını görselleştirir
- Anthropic Claude ile koçluk raporu oluşturur

### Çalışma akışı
1. Demo `awpy` ile parse edilir
2. Maç verisi yapılandırılır (`kills`, `damages`, `grenades`, `rounds`, `players`)
3. `src/analyzer.py` ile oyuncu analizi yapılır
4. `src/coach.py` ile AI raporu üretilir
5. Sonuçlar `app.py` üzerinden Streamlit arayüzünde gösterilir

---

## 3) Usage (English)

### Requirements
- Python 3.11
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
- Python 3.11
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

## 5) Minimal Repository Structure
```text
cs2-coach/
├── .streamlit/config.toml
├── De_mirage_radar.webp
├── README.md
├── app.py
├── requirements.txt
└── src/
    ├── __init__.py
    ├── analyzer.py
    ├── coach.py
    ├── parser.py
    └── utils.py
```
