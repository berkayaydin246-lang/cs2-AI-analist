"""
app.py
CS2 AI Coach - Streamlit arayuzu
Calistirmak icin: streamlit run app.py
"""

import os
import sys
import tempfile
import traceback
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))

from src.analyzer import analyze_player
from src.coach import get_coaching, save_report
from src.parser import parse_demo
from src.utils import (
    get_aim_points,
    get_death_positions,
    get_grenade_positions,
    get_player_movement_positions,
    list_demos,
    plot_death_heatmap,
    plot_player_activity_map,
)


st.set_page_config(page_title="CS2 AI Coach", page_icon="🎯", layout="wide")

st.markdown(
    """
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
</style>
""",
    unsafe_allow_html=True,
)

st.title("🎯 CS2 AI Coach")
st.markdown("Demo dosyani yukle, oyuncu sec, analiz al.")
st.divider()

# Varsayilan: API'siz ve gorselsiz hizli analiz
api_key = os.getenv("ANTHROPIC_API_KEY", "")
run_ai_coaching = st.toggle("AI coaching kullan (API gerekir)", value=False)
show_visuals = st.toggle("Utility ve isi haritasi gorsellerini goster", value=False)

if run_ai_coaching and (not api_key or "buraya" in api_key):
    st.warning("AI coaching icin .env dosyasina gecerli ANTHROPIC_API_KEY eklenmeli.")


st.subheader("Demo Dosyasi")
upload_tab, folder_tab = st.tabs(["Dosya Yukle", "demos/ klasorunden sec"])

with upload_tab:
    uploaded = st.file_uploader("CS2 demo dosyasini sec (.dem)", type=["dem"])
    if uploaded:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".dem")
        tmp.write(uploaded.read())
        tmp.close()
        st.session_state["demo_path"] = tmp.name
        st.success(f"Yuklendi: {uploaded.name}")

with folder_tab:
    demos = list_demos("demos")
    if demos:
        selected = st.selectbox("Demo sec", demos)
        if st.button("Bu demoyu kullan"):
            st.session_state["demo_path"] = selected
    else:
        st.info("demos/ klasorunde henuz .dem dosyasi yok.")


demo_path = st.session_state.get("demo_path")

if demo_path:
    required_schema_version = 4
    needs_reparse = (
        "parsed_data" not in st.session_state
        or st.session_state.get("loaded_demo") != demo_path
    )

    if not needs_reparse:
        cached = st.session_state.get("parsed_data", {})
        required_keys = {"shots", "player_positions"}
        if (
            not required_keys.issubset(set(cached.keys()))
            or cached.get("schema_version", 0) < required_schema_version
        ):
            needs_reparse = True

    if needs_reparse:
        with st.spinner("Demo parse ediliyor..."):
            try:
                parsed = parse_demo(demo_path)
                st.session_state["parsed_data"] = parsed
                st.session_state["loaded_demo"] = demo_path
                st.success(f"Parse tamamlandi. Harita: {parsed['map']} | {parsed['total_rounds']} round")
            except Exception as e:
                st.error(f"Parse hatasi: {e}")
                st.stop()

    parsed_data = st.session_state["parsed_data"]

    st.divider()
    st.subheader("Oyuncu Sec")
    players = parsed_data.get("players", [])
    if not players:
        st.error("Demo'da oyuncu bulunamadi.")
        st.stop()

    player_name = st.selectbox("Analiz edilecek oyuncu", sorted(players))

    if st.button("Analiz Et", use_container_width=True):
        with st.spinner("Analiz yapiliyor..."):
            analysis = analyze_player(parsed_data, player_name)
            st.session_state["analysis"] = analysis

        # Onceki raporlari temizle
        st.session_state.pop("coaching", None)
        st.session_state.pop("report_path", None)

        if run_ai_coaching:
            if not api_key or "buraya" in api_key:
                st.info("AI coaching atlandi: gecerli API key yok.")
            else:
                with st.spinner("AI coaching hazirlaniyor..."):
                    try:
                        coaching = get_coaching(analysis)
                        st.session_state["coaching"] = coaching
                        report_path = save_report(coaching, player_name)
                        st.session_state["report_path"] = report_path
                    except Exception as e:
                        st.error(f"Claude API hatasi: {e}")


if "analysis" in st.session_state:
    analysis = st.session_state["analysis"]
    stats = analysis["stats"]
    findings = analysis["findings"]

    st.divider()
    st.subheader(f"{analysis['player']} - Istatistikler")

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("K/D", stats.get("kd_ratio", 0))
    c2.metric("ADR", stats.get("adr", 0))
    c3.metric("HS %", f"{stats.get('hs_rate', 0)}%")
    c4.metric("Kill", stats.get("kills", 0))
    c5.metric("Death", stats.get("deaths", 0))
    c6.metric("Accuracy", f"{stats.get('accuracy', 0)}%")

    c7, c8, c9 = st.columns(3)
    c7.metric("Shots Fired", stats.get("shots_fired", 0))
    c8.metric("Shots Hit", stats.get("shots_hit", 0))
    c9.metric("Opening Win %", f"{stats.get('opening_win_rate', 0)}%")

    if stats.get("weapon_kills"):
        st.markdown("**Silah Bazli Kill:**")
        wk = stats["weapon_kills"]
        sorted_wk = dict(sorted(wk.items(), key=lambda x: -x[1]))
        st.bar_chart(sorted_wk)

    st.divider()
    st.subheader("Kural Tabanli Bulgular")
    for f in findings:
        sev = f.get("severity", "low")
        msg = f"[{f.get('category', 'Genel')}] {f.get('message', '')}"
        if sev == "high":
            st.error(msg)
        elif sev == "medium":
            st.warning(msg)
        else:
            st.success(msg)

    # ── ADVANCED ANALYTICS ──
    if "advanced" in analysis:
        adv = analysis["advanced"]

        st.divider()
        st.subheader("Detayli Analiz")

        # Side-specific stats
        ss = adv.get("side_stats", {})
        t_s = ss.get("t_side", {})
        ct_s = ss.get("ct_side", {})
        if t_s.get("rounds", 0) > 0 or ct_s.get("rounds", 0) > 0:
            st.markdown("**T-side vs CT-side**")
            col_t, col_ct = st.columns(2)
            with col_t:
                st.markdown("##### T-side")
                tc1, tc2, tc3, tc4 = st.columns(4)
                tc1.metric("K/D", t_s.get("kd_ratio", 0))
                tc2.metric("Kill", t_s.get("kills", 0))
                tc3.metric("Death", t_s.get("deaths", 0))
                tc4.metric("HS%", f"{t_s.get('hs_rate', 0)}%")
                st.caption(f"{t_s.get('rounds', 0)} round")
            with col_ct:
                st.markdown("##### CT-side")
                cc1, cc2, cc3, cc4 = st.columns(4)
                cc1.metric("K/D", ct_s.get("kd_ratio", 0))
                cc2.metric("Kill", ct_s.get("kills", 0))
                cc3.metric("Death", ct_s.get("deaths", 0))
                cc4.metric("HS%", f"{ct_s.get('hs_rate', 0)}%")
                st.caption(f"{ct_s.get('rounds', 0)} round")

        # Round timeline
        round_stats = adv.get("round_stats", [])
        if round_stats:
            st.markdown("**Round Timeline**")
            import pandas as pd
            rdf = pd.DataFrame(round_stats)
            if "round" in rdf.columns:
                rdf = rdf.set_index("round")
            st.bar_chart(rdf[["kills", "deaths"]])

        # Key metrics row
        kast = adv.get("kast", {})
        mk = adv.get("multi_kills", {})
        ts = adv.get("trade_stats", {})
        col_k, col_m, col_tr = st.columns(3)
        col_k.metric("KAST", f"{kast.get('kast_percentage', 0)}%")
        col_m.metric("3K / 4K / ACE", f"{mk.get('total_3k', 0)} / {mk.get('total_4k', 0)} / {mk.get('total_aces', 0)}")
        col_tr.metric("Trade Orani", f"{ts.get('traded_rate', 0)}%")

        # Clutch analysis
        with st.expander("Clutch Analizi"):
            clutches = adv.get("clutches", [])
            if clutches:
                won = sum(1 for c in clutches if c["won"])
                st.markdown(f"**{len(clutches)}** clutch durumu, **{won}** kazanilan")
                for c in clutches:
                    result = "Kazandi" if c["won"] else "Kaybetti"
                    icon = "+" if c["won"] else "-"
                    st.write(f"[{icon}] Round {c['round']}: 1v{c['vs']} — {result} ({c['kills']} kill)")
            else:
                st.info("Bu macta clutch durumu tespit edilmedi.")

        # Economy analysis
        with st.expander("Ekonomi Analizi"):
            eco = adv.get("economy_stats", {})
            has_data = False
            for tier_name, tier_label in [("eco", "Eco (<$8K)"), ("force", "Force ($8K-$20K)"), ("full_buy", "Full Buy (>$20K)")]:
                tier = eco.get(tier_name, {})
                if tier.get("rounds", 0) > 0:
                    has_data = True
                    st.write(f"**{tier_label}**: {tier['rounds']} round — {tier['kills']}K / {tier['deaths']}D (K/D: {tier['kd_ratio']})")
            if not has_data:
                st.info("Ekonomi verisi bulunamadi.")

        # Flash analysis
        with st.expander("Flash Analizi"):
            fs = adv.get("flash_stats", {})
            if fs.get("flash_count", 0) > 0:
                fc1, fc2, fc3 = st.columns(3)
                fc1.metric("Toplam Flash", fs["flash_count"])
                fc2.metric("Flash Assist", fs["flash_assists"])
                fc3.metric("Etkinlik", f"{fs['flash_assist_rate']}%")
            else:
                st.info("Flash kullanimi tespit edilmedi.")

        # Spray transfers
        with st.expander("Hizli Kill / Spray Transfer"):
            spray_transfers = adv.get("spray_transfers", [])
            if spray_transfers:
                st.markdown(f"**{len(spray_transfers)}** hizli ardisik kill tespit edildi")
                for t in spray_transfers:
                    st.write(f"Round {t['round']}: {t['victim1']} -> {t['victim2']} ({t['time_ms']}ms, {t['weapon']})")
            else:
                st.info("Hizli ardisik kill tespit edilmedi.")

        # Death clusters
        with st.expander("Olum Bolge Analizi"):
            dc = adv.get("death_clusters", {})
            clusters = dc.get("clusters", [])
            if clusters:
                st.markdown(f"Toplam **{dc.get('total', 0)}** olum pozisyonu analiz edildi")
                for i, c in enumerate(clusters, 1):
                    bar_len = min(c["count"] * 3, 20)
                    bar = "|" * bar_len
                    st.write(f"Bolge {i}: **{c['count']} olum** (koordinat: {c['center_x']:.0f}, {c['center_y']:.0f}) {bar}")
            else:
                st.info("Yeterli olum pozisyonu verisi bulunamadi.")

    if show_visuals:
        st.divider()
        st.subheader("Olum ve Utility Pozisyonlari")
        utility_trace = []
        positions = []
        grenade_pos = []

        try:
            positions = get_death_positions(parsed_data, analysis["player"])
            utility_trace.append(f"OK get_death_positions -> {len(positions)}")
        except Exception:
            utility_trace.append("ERR get_death_positions")
            utility_trace.append(traceback.format_exc())
            st.error("get_death_positions calisirken hata olustu.")

        try:
            grenade_pos = get_grenade_positions(parsed_data, analysis["player"])
            utility_trace.append(f"OK get_grenade_positions -> {len(grenade_pos)}")
        except Exception:
            utility_trace.append("ERR get_grenade_positions")
            utility_trace.append(traceback.format_exc())
            st.error("get_grenade_positions calisirken hata olustu.")

        if positions or grenade_pos:
            try:
                fig = plot_death_heatmap(
                    positions or [],
                    analysis["map"],
                    analysis["player"],
                    grenade_positions=grenade_pos,
                )
                utility_trace.append("OK plot_death_heatmap")
                if fig:
                    st.pyplot(fig)
                else:
                    utility_trace.append("WARN plot_death_heatmap -> no fig")
            except Exception:
                utility_trace.append("ERR plot_death_heatmap")
                utility_trace.append(traceback.format_exc())
                st.error("plot_death_heatmap calisirken hata olustu.")
        else:
            utility_trace.append("INFO cizim atlandi: death/grenade verisi yok")
            st.info("Bu demo icin koordinat verisi mevcut degil.")

        with st.expander("Utility Execution Trace", expanded=False):
            st.code("\n".join(utility_trace) if utility_trace else "Trace kaydi yok.", language="text")

    st.divider()
    st.subheader("Oyuncu Gezi Isi Haritasi (T-side / CT-side)")
    activity_trace = []

    player = analysis["player"]
    map_n = analysis["map"]
    bounds = parsed_data.get("map_bounds")

    def _generate_side_heatmap(side_label, side_code, prefix):
        """Belirli bir side icin heatmap olusturur, fig ve on_map_path doner."""
        pos = get_player_movement_positions(parsed_data, player, side=side_code)
        activity_trace.append(f"OK get_player_movement_positions({side_code}) -> {len(pos)}")
        if not pos:
            return None, None, 0
        fig = plot_player_activity_map(
            movement_positions=pos,
            map_name=map_n,
            player_name=player,
            show_aim_points=False,
            map_bounds=bounds,
            output_dir="outputs",
            output_prefix=prefix,
            title_suffix=side_label,
        )
        on_map = Path("outputs") / f"{prefix}_on_map.png"
        activity_trace.append(f"OK plot ({side_code}) saved -> outputs/{prefix}_on_map.png")
        return fig, on_map, len(pos)

    try:
        col_t_hm, col_ct_hm = st.columns(2)

        with col_t_hm:
            st.markdown("##### T-side")
            t_fig, t_path, t_count = _generate_side_heatmap("T-side", "T", "heatmap_t")
            if t_fig:
                st.pyplot(t_fig)
            elif t_path and t_path.exists():
                st.image(str(t_path), use_container_width=True)
            elif t_count == 0:
                st.info("T-side movement verisi bulunamadi.")

        with col_ct_hm:
            st.markdown("##### CT-side")
            ct_fig, ct_path, ct_count = _generate_side_heatmap("CT-side", "CT", "heatmap_ct")
            if ct_fig:
                st.pyplot(ct_fig)
            elif ct_path and ct_path.exists():
                st.image(str(ct_path), use_container_width=True)
            elif ct_count == 0:
                st.info("CT-side movement verisi bulunamadi.")

        if t_count > 0 or ct_count > 0:
            st.caption(f"T-side: {t_count} pozisyon | CT-side: {ct_count} pozisyon")
        else:
            st.info("Bu demo icin movement verisi bulunamadi.")

    except Exception:
        activity_trace.append("ERR heatmap generation")
        activity_trace.append(traceback.format_exc())
        st.error("Heatmap olusturulurken hata olustu.")

    with st.expander("Activity Execution Trace", expanded=False):
        st.code("\n".join(activity_trace) if activity_trace else "Trace kaydi yok.", language="text")

    if "coaching" in st.session_state:
        st.divider()
        st.subheader("AI Coaching Raporu")
        st.markdown(st.session_state["coaching"])

        if "report_path" in st.session_state:
            with open(st.session_state["report_path"], "r", encoding="utf-8") as f:
                report_text = f.read()
            st.download_button(
                "Raporu indir (.txt)",
                data=report_text,
                file_name=f"coaching_{analysis['player']}.txt",
                mime="text/plain",
            )
