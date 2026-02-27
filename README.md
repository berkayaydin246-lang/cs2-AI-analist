# 🎯 CS2 AI Coach

CS2 demo dosyalarını analiz eden ve Claude AI ile profesyonel coaching raporu üreten uygulama.

---

## Kurulum

### 1. Python 3.11 gereklidir
```bash
python --version  # Python 3.11.x çıkmalı
```

### 2. Sanal ortam oluştur ve aktive et
```bash
python -m venv venv

# Windows:
venv\Scripts\activate

# Mac/Linux:
source venv/bin/activate
```

### 3. Kütüphaneleri yükle
```bash
pip install -r requirements.txt
```

### 4. API key ayarla
`.env.example` dosyasını kopyala ve `.env` olarak kaydet:
```bash
cp .env.example .env
```
Sonra `.env` dosyasını aç ve Anthropic API key'ini yaz:
```
ANTHROPIC_API_KEY=sk-ant-...
```
API key almak için: https://console.anthropic.com

### 5. Demo dosyası ekle
CS2'den indirdiğin `.dem` dosyasını `demos/` klasörüne koy.

---

## Çalıştırma

```bash
streamlit run app.py
```

Tarayıcıda `http://localhost:8501` açılır.

---

## Proje Yapısı

```
cs2-coach/
├── demos/          → .dem dosyaları buraya
├── src/
│   ├── parser.py   → Demo parse eder
│   ├── analyzer.py → Kural tabanlı analiz
│   ├── coach.py    → Claude AI coaching
│   └── utils.py    → Heatmap ve yardımcı araçlar
├── outputs/        → Raporlar buraya kaydedilir
├── app.py          → Streamlit arayüzü
├── requirements.txt
└── .env            → API key (GitHub'a atma!)
```

---

## CS2'den Demo Nasıl İndirilir?

1. CS2'yi aç
2. Ana menüden **İzle** → **Maçlarım** sekmesine git
3. İstediğin maça tıkla → **Demo İndir**
4. İndirilen `.dem` dosyasını `demos/` klasörüne koy

---

## Nasıl Çalışır?

```
.dem dosyası
    ↓
awpy parser → Ham veri (kill, damage, utility, pozisyon)
    ↓
analyzer.py → Kural tabanlı bulgular (HS oranı, ADR, utility vb.)
    ↓
Claude API → Türkçe coaching raporu
    ↓
Streamlit arayüzü → Görsel + rapor
```
