"""
app.py
CS2 AI Coach — Streamlit arayüzü
Çalıştırmak için: streamlit run app.py
"""

import streamlit as st
import os
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.parser import parse_demo
from src.analyzer import analyze_player
from src.coach import get_coaching, save_report
from src.utils import list_demos, get_death_positions, get_grenade_positions, plot_death_heatmap, format_stats_table

# ─── Sayfa ayarları ───────────────────────────────────────────────
st.set_page_config(
    page_title="CS2 AI Coach",
    page_icon="🎯",
    layout="wide",
)

st.markdown("""
<style>
    .main { background-color: #0e1117; }
    .stButton > button {
        background-color: #f97316;
        color: white;
        border: none;
        border-radius: 8px;
        font-weight: bold;
    }
    .stButton > button:hover { background-color: #ea580c; }
    .metric-card {
        background-color: #1e2130;
        padding: 1rem;
        border-radius: 10px;
        text-align: center;
    }
</style>
""", unsafe_allow_html=True)

# ─── Başlık ───────────────────────────────────────────────────────
st.title("🎯 CS2 AI Coach")
st.markdown("Demo dosyanı yükle, oyuncu seç, AI koçun analiz etsin.")
st.divider()

# ─── API Key kontrolü ─────────────────────────────────────────────
api_key = os.getenv("ANTHROPIC_API_KEY", "")
if not api_key or "buraya" in api_key:
    st.warning("⚠️ `.env` dosyasına Anthropic API key'ini eklemen gerekiyor. `.env.example` dosyasına bak.")

# ─── Demo yükleme ─────────────────────────────────────────────────
st.subheader("📁 Demo Dosyası")

upload_tab, folder_tab = st.tabs(["Dosya Yükle", "demos/ Klasöründen Seç"])

demo_path = None

with upload_tab:
    uploaded = st.file_uploader("CS2 demo dosyasını seç (.dem)", type=["dem"])
    if uploaded:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".dem")
        tmp.write(uploaded.read())
        tmp.close()
        st.session_state["demo_path"] = tmp.name
        st.success(f"✅ Yüklendi: {uploaded.name}")

with folder_tab:
    demos = list_demos("demos")
    if demos:
        selected = st.selectbox("Demo seç", demos)
        if st.button("Bu demoyu kullan"):
            st.session_state["demo_path"] = selected
    else:
        st.info("demos/ klasöründe henüz .dem dosyası yok.")

# ─── Parse & Analiz ───────────────────────────────────────────────
demo_path = st.session_state.get("demo_path")

if demo_path:
    if "parsed_data" not in st.session_state or st.session_state.get("loaded_demo") != demo_path:
        with st.spinner("Demo parse ediliyor... Bu 10-30 saniye sürebilir."):
            try:
                parsed = parse_demo(demo_path)
                st.session_state["parsed_data"] = parsed
                st.session_state["loaded_demo"] = demo_path
                st.success(f"✅ Parse tamamlandı! Harita: {parsed['map']} | {parsed['total_rounds']} round")
            except Exception as e:
                st.error(f"❌ Parse hatası: {e}")
                st.stop()

    parsed_data = st.session_state["parsed_data"]

    st.divider()
    st.subheader("👤 Oyuncu Seç")

    players = parsed_data.get("players", [])
    if not players:
        st.error("Demo'da oyuncu bulunamadı.")
        st.stop()

    player_name = st.selectbox("Analiz edilecek oyuncu", sorted(players))

    if st.button("🔍 Analiz Et & Coaching Al", use_container_width=True):
        with st.spinner("Analiz yapılıyor..."):
            analysis = analyze_player(parsed_data, player_name)
            st.session_state["analysis"] = analysis

        with st.spinner("AI coaching hazırlanıyor..."):
            try:
                coaching = get_coaching(analysis)
                st.session_state["coaching"] = coaching
                report_path = save_report(coaching, player_name)
                st.session_state["report_path"] = report_path
            except Exception as e:
                st.error(f"❌ Claude API hatası: {e}")
                coaching = None

# ─── Sonuçlar ─────────────────────────────────────────────────────
if "analysis" in st.session_state:
    analysis = st.session_state["analysis"]
    stats = analysis["stats"]
    findings = analysis["findings"]

    st.divider()
    st.subheader(f"📊 {analysis['player']} — İstatistikler")

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("K/D", stats["kd_ratio"])
    col2.metric("ADR", stats["adr"])
    col3.metric("HS %", f"{stats['hs_rate']}%")
    col4.metric("Kill", stats["kills"])
    col5.metric("Death", stats["deaths"])

    # Silah bazlı kill
    if stats.get("weapon_kills"):
        st.markdown("**Silah Bazlı Kill:**")
        wk = stats["weapon_kills"]
        sorted_wk = dict(sorted(wk.items(), key=lambda x: -x[1]))
        st.bar_chart(sorted_wk)

    st.divider()
    st.subheader("⚠️ Kural Tabanlı Bulgular")

    for f in findings:
        severity = f["severity"]
        if severity == "high":
            st.error(f"🔴 [{f['category']}] {f['message']}")
        elif severity == "medium":
            st.warning(f"🟡 [{f['category']}] {f['message']}")
        else:
            st.success(f"🟢 [{f['category']}] {f['message']}")

    # Heatmap
    st.divider()
    st.subheader("🗺️ Ölüm & Utility Pozisyonları")
    utility_trace = []
    positions = []
    grenade_pos = []

    try:
        positions = get_death_positions(parsed_data, analysis["player"])
        utility_trace.append(f"✅ get_death_positions -> {len(positions)} kayıt")
    except Exception:
        utility_trace.append("❌ get_death_positions patladı")
        utility_trace.append(traceback.format_exc())
        st.error("get_death_positions çalışırken hata oluştu.")

    try:
        grenade_pos = get_grenade_positions(parsed_data, analysis["player"])
        utility_trace.append(f"✅ get_grenade_positions -> {len(grenade_pos)} kayıt")
    except Exception:
        utility_trace.append("❌ get_grenade_positions patladı")
        utility_trace.append(traceback.format_exc())
        st.error("get_grenade_positions çalışırken hata oluştu.")

    if positions or grenade_pos:
        try:
            fig = plot_death_heatmap(
                positions or [],
                analysis["map"],
                analysis["player"],
                grenade_positions=grenade_pos
            )
            utility_trace.append("✅ plot_death_heatmap tamamlandı")
            if fig:
                st.pyplot(fig)
            else:
                utility_trace.append("⚠️ plot_death_heatmap fig döndürmedi")
        except Exception:
            utility_trace.append("❌ plot_death_heatmap patladı")
            utility_trace.append(traceback.format_exc())
            st.error("plot_death_heatmap çalışırken hata oluştu.")
    else:
        utility_trace.append("ℹ️ Çizim atlandı: death/grenade koordinatı yok")
        st.info("Bu demo için koordinat verisi mevcut değil.")

    with st.expander("Utility Execution Trace", expanded=False):
        st.code("\n".join(utility_trace) if utility_trace else "Trace kaydı yok.", language="text")

    # Coaching raporu
    if "coaching" in st.session_state:
        st.divider()
        st.subheader("🤖 AI Coaching Raporu")
        st.markdown(st.session_state["coaching"])

        if "report_path" in st.session_state:
            with open(st.session_state["report_path"], "r", encoding="utf-8") as f:
                report_text = f.read()
            st.download_button(
                "📥 Raporu İndir (.txt)",
                data=report_text,
                file_name=f"coaching_{analysis['player']}.txt",
                mime="text/plain",
            )
