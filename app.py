"""
app.py
CS2 AI Coach Streamlit UI
Run: streamlit run app.py
"""

from __future__ import annotations

import copy
import os
import sys
import tempfile
import traceback
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))

from src.analyzer import analyze_player
from src.coach import get_coaching, get_scouting_report, save_report
from src.parser import parse_demo
from src.replay import build_replay_data, get_round_frame_summary, render_replay_animation, render_replay_frame
from src.team_analyzer import analyze_team, apply_manual_round_tags
from src.utils import (
    create_round_route_gif,
    get_death_positions,
    get_grenade_positions,
    get_player_movement_positions,
    list_demos,
    plot_deaths_map,
    plot_player_activity_map,
    plot_utility_map,
)


REQUIRED_SCHEMA_VERSION = 12


st.set_page_config(page_title="CS2 AI Coach", page_icon="CS2", layout="wide")

st.markdown(
    """
<style>
.main { background: linear-gradient(180deg, #0f172a 0%, #111827 45%, #0b1020 100%); }
.stButton > button {
    background: #ea580c;
    color: #ffffff;
    border: 0;
    border-radius: 10px;
    font-weight: 700;
}
.stButton > button:hover { background: #c2410c; }
</style>
""",
    unsafe_allow_html=True,
)


st.title("CS2 AI Coach")
st.caption("Single demo: bireysel analiz + takim analizi + scouting + 2D replay")
st.divider()

api_key = os.getenv("ANTHROPIC_API_KEY", "")
run_ai_coaching = st.toggle("AI coaching kullan", value=False)
run_ai_scouting = st.toggle("AI scouting kullan", value=False)
show_visuals = st.toggle("Harita gorsellerini ac", value=False)

if (run_ai_coaching or run_ai_scouting) and (not api_key or "buraya" in api_key.lower()):
    st.warning("AI ozellikleri icin gecerli ANTHROPIC_API_KEY gerekli.")


st.subheader("Demo")
upload_tab, folder_tab = st.tabs(["Dosya Yukle", "demos/ klasorunden sec"])

with upload_tab:
    uploaded = st.file_uploader(".dem dosyasini sec", type=["dem"])
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
        if st.button("Bu demoyu kullan", key="btn_use_demo"):
            st.session_state["demo_path"] = selected
    else:
        st.info("demos/ klasorunde demo bulunamadi.")


def _load_parsed_demo(demo_path: str) -> dict:
    needs_reparse = (
        "parsed_data" not in st.session_state
        or st.session_state.get("loaded_demo") != demo_path
    )

    if not needs_reparse:
        cached = st.session_state.get("parsed_data", {})
        required_keys = {"shots", "player_positions", "rounds", "grenades", "bomb_events"}
        if not required_keys.issubset(set(cached.keys())):
            needs_reparse = True
        if cached.get("schema_version", 0) < REQUIRED_SCHEMA_VERSION:
            needs_reparse = True

    if needs_reparse:
        with st.spinner("Demo parse ediliyor..."):
            parsed = parse_demo(demo_path)
        st.session_state["parsed_data"] = parsed
        st.session_state["loaded_demo"] = demo_path

        # reset demo-scoped cache
        st.session_state.pop("analysis", None)
        st.session_state.pop("team_analysis", None)
        st.session_state.pop("replay_data", None)
        st.session_state.pop("replay_cache_key", None)
        st.session_state.pop("coaching", None)
        st.session_state.pop("report_path", None)
        st.session_state.pop("scouting_report", None)

    return st.session_state["parsed_data"]


def _ensure_team_analysis(parsed_data: dict) -> dict:
    if "team_analysis" not in st.session_state:
        with st.spinner("Takim analizi hazirlaniyor..."):
            st.session_state["team_analysis"] = analyze_team(parsed_data)
    return st.session_state["team_analysis"]


def _render_individual_tab(parsed_data: dict, analysis: dict):
    player_name = analysis["player"]
    stats = analysis["stats"]
    findings = analysis["findings"]
    adv = analysis.get("advanced", {})

    st.subheader(f"{player_name} - Bireysel Analiz")

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
    c9.metric("Opening Win", f"{stats.get('opening_win_rate', 0)}%")

    pro = adv.get("pro_metrics", {})
    if pro:
        st.markdown("**Pro Metrikler**")
        p1, p2, p3, p4, p5 = st.columns(5)
        p1.metric("HLTV 2.0 (approx)", pro.get("hltv_rating", 0))
        p2.metric("Impact", pro.get("impact_rating", 0))
        p3.metric("Entry Success", f"{pro.get('entry_success_rate', 0)}%")
        p4.metric("Duel Win", f"{pro.get('duels', {}).get('duel_win_rate', 0)}%")
        p5.metric("Utility Score", pro.get("utility_effectiveness", {}).get("utility_score", 0))

    if stats.get("weapon_kills"):
        st.markdown("**Silah Bazli Kill**")
        wk = dict(sorted(stats["weapon_kills"].items(), key=lambda x: -x[1]))
        st.bar_chart(wk)

    st.divider()
    st.markdown("**Kural Tabanli Bulgular**")
    for f in findings:
        severity = f.get("severity", "low")
        message = f"[{f.get('category', 'Genel')}] {f.get('message', '')}"
        if severity == "high":
            st.error(message)
        elif severity == "medium":
            st.warning(message)
        else:
            st.success(message)

    st.divider()
    st.markdown("**Detayli Analiz**")

    ss = adv.get("side_stats", {})
    t_s = ss.get("t_side", {})
    ct_s = ss.get("ct_side", {})
    left, right = st.columns(2)
    with left:
        st.markdown("##### T-side")
        st.metric("K/D", t_s.get("kd_ratio", 0))
        st.caption(f"Round: {t_s.get('rounds', 0)} | K: {t_s.get('kills', 0)} D: {t_s.get('deaths', 0)}")
    with right:
        st.markdown("##### CT-side")
        st.metric("K/D", ct_s.get("kd_ratio", 0))
        st.caption(f"Round: {ct_s.get('rounds', 0)} | K: {ct_s.get('kills', 0)} D: {ct_s.get('deaths', 0)}")

    core1, core2, core3 = st.columns(3)
    core1.metric("KAST", f"{adv.get('kast', {}).get('kast_percentage', 0)}%")
    mk = adv.get("multi_kills", {})
    core2.metric("3K/4K/ACE", f"{mk.get('total_3k', 0)} / {mk.get('total_4k', 0)} / {mk.get('total_aces', 0)}")
    core3.metric("Trade Rate", f"{adv.get('trade_stats', {}).get('traded_rate', 0)}%")

    with st.expander("Clutch"):
        clutches = adv.get("clutches", [])
        if clutches:
            won = sum(1 for c in clutches if c.get("won"))
            st.write(f"Toplam: {len(clutches)} | Kazanilan: {won}")
            for c in clutches:
                res = "W" if c.get("won") else "L"
                st.write(f"Round {c.get('round')}: 1v{c.get('vs')} -> {res} ({c.get('kills', 0)} kill)")
        else:
            st.info("Clutch bulunamadi.")

    with st.expander("Ekonomi"):
        eco = adv.get("economy_stats", {})
        for tier_name, label in (("eco", "Eco"), ("force", "Force"), ("full_buy", "Full Buy")):
            tier = eco.get(tier_name, {})
            if tier.get("rounds", 0) > 0:
                st.write(f"{label}: {tier.get('rounds')} round | {tier.get('kills')}K/{tier.get('deaths')}D | K/D {tier.get('kd_ratio')}")

    with st.expander("Flash"):
        fs = adv.get("flash_stats", {})
        if fs.get("flash_count", 0) > 0:
            f1, f2, f3 = st.columns(3)
            f1.metric("Flash", fs.get("flash_count", 0))
            f2.metric("Assist", fs.get("flash_assists", 0))
            f3.metric("Rate", f"{fs.get('flash_assist_rate', 0)}%")
        else:
            st.info("Flash verisi yok.")

    with st.expander("Spray Transfer"):
        transfers = adv.get("spray_transfers", [])
        if transfers:
            for t in transfers:
                st.write(f"Round {t.get('round')}: {t.get('victim1')} -> {t.get('victim2')} ({t.get('time_ms')} ms)")
        else:
            st.info("Transfer bulunamadi.")

    with st.expander("Death Clusters"):
        clusters = adv.get("death_clusters", {}).get("clusters", [])
        if clusters:
            for i, c in enumerate(clusters, 1):
                st.write(f"Bolge {i}: {c.get('count')} olum @ ({c.get('center_x', 0):.0f}, {c.get('center_y', 0):.0f})")
        else:
            st.info("Cluster verisi yok.")

    round_stats = adv.get("round_stats", [])
    if round_stats:
        rdf = pd.DataFrame(round_stats)
        if "round" in rdf.columns:
            rdf = rdf.set_index("round")
        keep = [c for c in ("kills", "deaths") if c in rdf.columns]
        if keep:
            st.bar_chart(rdf[keep])

    if show_visuals:
        st.divider()
        st.markdown("**Olum / Utility Haritalari**")
        positions = get_death_positions(parsed_data, player_name)
        grenade_pos = get_grenade_positions(parsed_data, player_name)

        dcol, ucol = st.columns(2)
        with dcol:
            st.markdown("##### Olum Pozisyonlari")
            if positions:
                fig = plot_deaths_map(positions, analysis["map"], player_name)
                if fig:
                    st.pyplot(fig)
            else:
                st.info("Olum koordinati yok.")

        with ucol:
            st.markdown("##### Utility Pozisyonlari")
            if grenade_pos:
                fig = plot_utility_map(grenade_pos, analysis["map"], player_name)
                if fig:
                    st.pyplot(fig)
            else:
                st.info("Utility koordinati yok.")

        st.divider()
        st.markdown("**T/CT Hareket Isi Haritasi**")
        map_name = analysis["map"]
        bounds = parsed_data.get("map_bounds")

        def _draw_side_heatmap(side_code: str, side_label: str, prefix: str):
            pos = get_player_movement_positions(parsed_data, player_name, side=side_code)
            if not pos:
                st.info(f"{side_label} movement verisi yok.")
                return
            fig = plot_player_activity_map(
                movement_positions=pos,
                map_name=map_name,
                player_name=player_name,
                show_aim_points=False,
                map_bounds=bounds,
                output_dir="outputs",
                output_prefix=prefix,
                title_suffix=side_label,
            )
            if fig:
                st.pyplot(fig)

        h1, h2 = st.columns(2)
        with h1:
            st.markdown("##### T-side")
            _draw_side_heatmap("T", "T-side", "heatmap_t")
        with h2:
            st.markdown("##### CT-side")
            _draw_side_heatmap("CT", "CT-side", "heatmap_ct")

    st.divider()
    st.markdown("**Round Rota GIF**")
    if parsed_data.get("player_positions"):
        g1, g2, g3 = st.columns(3)
        with g1:
            side_choice = st.selectbox("Taraf", ["Tumu", "T-side", "CT-side"], key="gif_side")
        with g2:
            speed_choice = st.selectbox("Hiz", ["Yavas", "Normal", "Hizli"], index=1, key="gif_speed")
        with g3:
            fpr = st.slider("Round basina frame", 8, 20, 12, key="gif_fpr")

        side_map = {"Tumu": None, "T-side": "T", "CT-side": "CT"}
        speed_map = {"Yavas": 60, "Normal": 80, "Hizli": 40}

        if st.button("Rota GIF olustur", key="btn_make_gif"):
            with st.spinner("GIF olusturuluyor..."):
                gif_path = create_round_route_gif(
                    parsed_data=parsed_data,
                    player_name=player_name,
                    map_name=analysis["map"],
                    output_dir="outputs",
                    output_prefix="route",
                    frames_per_round=fpr,
                    frame_duration_ms=speed_map[speed_choice],
                    side_filter=side_map[side_choice],
                )
            if gif_path:
                st.session_state["gif_path"] = gif_path

        gif_path = st.session_state.get("gif_path")
        if gif_path and Path(gif_path).exists():
            st.image(gif_path, caption=f"{player_name} route")
            with open(gif_path, "rb") as gf:
                st.download_button("GIF indir", data=gf.read(), file_name=Path(gif_path).name, mime="image/gif")
    else:
        st.info("Movement verisi yok.")


def _render_team_tab(parsed_data: dict, team_analysis: dict):
    st.subheader("Takim Analizi")

    team1 = team_analysis.get("teams", {}).get("team1", {})
    team2 = team_analysis.get("teams", {}).get("team2", {})

    t1, t2 = st.columns(2)
    with t1:
        st.markdown(f"### {team1.get('name', 'Team 1')}")
        st.caption(", ".join(team1.get("players", [])) or "Oyuncu yok")
    with t2:
        st.markdown(f"### {team2.get('name', 'Team 2')}")
        st.caption(", ".join(team2.get("players", [])) or "Oyuncu yok")

    st.markdown("**Scoreboard (10 oyuncu)**")
    scoreboard = team_analysis.get("scoreboard", [])
    if scoreboard:
        sdf = pd.DataFrame(scoreboard)
        columns = [
            c for c in [
                "player", "team", "rating", "impact_rating", "kd_ratio", "kills", "deaths", "adr", "kast", "hs_rate",
                "opening_kills", "opening_deaths",
            ] if c in sdf.columns
        ]
        st.dataframe(sdf[columns], use_container_width=True, hide_index=True)
    else:
        st.info("Scoreboard verisi yok.")

    st.markdown("**Takim Aggregate**")
    a1 = team1.get("aggregate", {})
    a2 = team2.get("aggregate", {})
    ag1, ag2 = st.columns(2)
    with ag1:
        st.metric("Avg Rating", a1.get("avg_rating", 0))
        st.metric("Team ADR", a1.get("team_adr", 0))
        st.metric("Team KAST", f"{a1.get('team_kast', 0)}%")
        st.caption(f"Kills/Deaths: {a1.get('kills', 0)}/{a1.get('deaths', 0)}")
    with ag2:
        st.metric("Avg Rating", a2.get("avg_rating", 0))
        st.metric("Team ADR", a2.get("team_adr", 0))
        st.metric("Team KAST", f"{a2.get('team_kast', 0)}%")
        st.caption(f"Kills/Deaths: {a2.get('kills', 0)}/{a2.get('deaths', 0)}")

    st.markdown("**Koordinasyon**")
    c1, c2 = st.columns(2)
    coord = team_analysis.get("coordination", {})
    with c1:
        c = coord.get("team1", {})
        st.write(f"Team 1 score: {c.get('coordination_score', 0)}")
        st.write(f"Traded rate: %{c.get('traded_rate', 0)}")
        st.write(f"Flash combo: %{c.get('flash_combo_rate', 0)}")
        st.write(f"Avg refrag: {c.get('avg_refrag_ms', 0)} ms")
    with c2:
        c = coord.get("team2", {})
        st.write(f"Team 2 score: {c.get('coordination_score', 0)}")
        st.write(f"Traded rate: %{c.get('traded_rate', 0)}")
        st.write(f"Flash combo: %{c.get('flash_combo_rate', 0)}")
        st.write(f"Avg refrag: {c.get('avg_refrag_ms', 0)} ms")

    st.markdown("**CT Setup Detection**")
    setups = team_analysis.get("ct_setups", [])
    if setups:
        st.dataframe(pd.DataFrame(setups), use_container_width=True, hide_index=True)
    else:
        st.info("CT setup tespit edilemedi.")

    st.markdown("**T Execute Detection**")
    executes = team_analysis.get("t_executes", [])
    if executes:
        st.dataframe(pd.DataFrame(executes), use_container_width=True, hide_index=True)
    else:
        st.info("T execute pattern tespit edilemedi.")

    st.markdown("**Round Tagging**")
    demo_key = st.session_state.get("loaded_demo", "demo")
    manual_key = f"manual_round_tags::{demo_key}"
    if manual_key not in st.session_state:
        st.session_state[manual_key] = {}

    manual_tags = st.session_state[manual_key]
    merged_tags = apply_manual_round_tags(team_analysis.get("round_tags", []), manual_tags)

    with st.expander("Manual round tag ekle"):
        rounds = [row.get("round", 0) for row in merged_tags if row.get("round", 0) > 0]
        if rounds:
            rc1, rc2, rc3 = st.columns([1, 2, 1])
            with rc1:
                selected_round = st.selectbox("Round", rounds, key="manual_round")
            with rc2:
                new_tag = st.text_input("Tag", key="manual_tag_input", placeholder="ornek: timeout_fake")
            with rc3:
                if st.button("Ekle", key="btn_add_tag"):
                    tag = (new_tag or "").strip().lower()
                    if tag:
                        manual_tags.setdefault(int(selected_round), [])
                        if tag not in manual_tags[int(selected_round)]:
                            manual_tags[int(selected_round)].append(tag)
                            st.success(f"Round {selected_round} -> {tag}")

        if st.button("Manual taglari temizle", key="btn_clear_manual_tags"):
            st.session_state[manual_key] = {}
            manual_tags = {}
            st.info("Temizlendi.")

    merged_tags = apply_manual_round_tags(team_analysis.get("round_tags", []), manual_tags)
    team_analysis["round_tags"] = merged_tags
    st.session_state["team_analysis"] = team_analysis

    all_tags = sorted({
        tag
        for row in merged_tags
        for tag in row.get("all_tags", row.get("tags", []))
    })
    selected_filter_tags = st.multiselect("Tag filtre", all_tags, default=[])

    tag_rows = []
    for row in merged_tags:
        tag_list = row.get("all_tags", row.get("tags", []))
        if selected_filter_tags and not any(t in tag_list for t in selected_filter_tags):
            continue
        tag_rows.append(
            {
                "round": row.get("round"),
                "tags": ", ".join(row.get("tags", [])),
                "manual_tags": ", ".join(row.get("manual_tags", [])),
                "all_tags": ", ".join(tag_list),
            }
        )

    if tag_rows:
        st.dataframe(pd.DataFrame(tag_rows), use_container_width=True, hide_index=True)
    else:
        st.info("Filtreye uyan round yok.")

    st.divider()
    st.markdown("**Rakip Scouting (AI)**")
    target_team = st.selectbox("Scouting hedef takimi", options=["team1", "team2"], index=1)

    if st.button("Scouting raporu uret", key="btn_scout"):
        if not run_ai_scouting:
            st.info("AI scouting toggle kapali.")
        elif not api_key or "buraya" in api_key.lower():
            st.warning("Gecerli ANTHROPIC_API_KEY gerekli.")
        else:
            with st.spinner("Scouting raporu olusturuluyor..."):
                scouting_input = copy.deepcopy(team_analysis)
                scouting_input["round_tags"] = merged_tags
                st.session_state["scouting_report"] = get_scouting_report(
                    scouting_input,
                    target_team=target_team,
                )

    if st.session_state.get("scouting_report"):
        st.markdown(st.session_state["scouting_report"])


def _render_replay_tab(parsed_data: dict, team_analysis: dict):
    st.subheader("2D Replay")

    if not parsed_data.get("player_positions"):
        st.warning("Replay icin player_positions verisi gerekli.")
        return

    with st.container(border=True):
        cfg1, cfg2, cfg3, cfg4 = st.columns([1.2, 1.2, 1.0, 1.0])
        with cfg1:
            quality = st.selectbox(
                "Replay kalite",
                ["Akici (onerilen)", "Detayli", "Hafif"],
                index=0,
                key="replay_quality",
            )
        with cfg2:
            replay_mode = st.selectbox(
                "Gorunum modu",
                ["Akici (animasyon)", "Kare kare (inceleme)"],
                index=0,
                key="replay_mode",
            )
        with cfg3:
            side_filter = st.selectbox("Taraf filtresi", ["ALL", "CT", "T"], index=0, key="replay_side")
        with cfg4:
            speed_label = st.selectbox("Oynatma hizi", ["0.5x", "1x", "2x", "4x"], index=1, key="replay_speed")

        toggles1 = st.columns(5)
        with toggles1[0]:
            show_labels = st.toggle("Label", value=True, key="replay_show_labels")
        with toggles1[1]:
            show_trails = st.toggle("Trail", value=True, key="replay_show_trails")
        with toggles1[2]:
            show_kills = st.toggle("Kill marker", value=True, key="replay_show_kills")
        with toggles1[3]:
            show_grenades = st.toggle("Grenade marker", value=True, key="replay_show_grenades")
        with toggles1[4]:
            show_dead_players = st.toggle("Dead players", value=True, key="replay_show_dead")

        toggles2 = st.columns(4)
        with toggles2[0]:
            show_direction = st.toggle("Yon oku", value=True, key="replay_show_direction")
        with toggles2[1]:
            show_bomb_events = st.toggle("Bomb event", value=True, key="replay_show_bomb_events")
        with toggles2[2]:
            show_sites = st.toggle("A/B label", value=True, key="replay_show_sites")
        with toggles2[3]:
            trail_frames = st.slider("Trail uzunlugu", 4, 24, 12, key="replay_trail_frames")

    max_frames_map = {
        "Hafif": 140,
        "Akici (onerilen)": 220,
        "Detayli": 320,
    }
    max_frames_per_round = max_frames_map[quality]
    replay_cache_key = (
        st.session_state.get("loaded_demo"),
        parsed_data.get("map", "unknown"),
        int(max_frames_per_round),
        parsed_data.get("schema_version", 0),
    )

    if st.session_state.get("replay_cache_key") != replay_cache_key:
        with st.spinner("Replay verisi hazirlaniyor..."):
            st.session_state["replay_data"] = build_replay_data(
                parsed_data,
                parsed_data.get("map", "unknown"),
                max_frames_per_round=max_frames_per_round,
            )
        st.session_state["replay_cache_key"] = replay_cache_key
        st.session_state["replay_round_value"] = None

    replay_data = st.session_state.get("replay_data", {})
    round_numbers = sorted(replay_data.get("rounds", {}).keys())
    if not round_numbers:
        st.info("Replay frame verisi bulunamadi.")
        return

    if st.session_state.get("replay_round_value") not in round_numbers:
        st.session_state["replay_round_value"] = round_numbers[0]

    current_round = st.session_state["replay_round_value"]

    nav1, nav2, nav3 = st.columns([1.0, 3.0, 1.0])
    with nav1:
        if st.button("< Round", key="replay_prev_round_btn"):
            idx = round_numbers.index(current_round)
            current_round = round_numbers[max(0, idx - 1)]
            st.session_state["replay_round_value"] = current_round
            st.session_state[f"replay_frame_idx::{current_round}"] = 0
            st.session_state[f"replay_frame_slider::{current_round}"] = 0
    with nav2:
        if st.session_state.get("replay_round_selectbox") != current_round:
            st.session_state["replay_round_selectbox"] = current_round
        selected_round = st.selectbox("Round", round_numbers, key="replay_round_selectbox")
        if selected_round != st.session_state.get("replay_round_value"):
            st.session_state["replay_round_value"] = selected_round
            st.session_state[f"replay_frame_idx::{selected_round}"] = 0
            st.session_state[f"replay_frame_slider::{selected_round}"] = 0
    with nav3:
        if st.button("Round >", key="replay_next_round_btn"):
            idx = round_numbers.index(st.session_state["replay_round_value"])
            current_round = round_numbers[min(len(round_numbers) - 1, idx + 1)]
            st.session_state["replay_round_value"] = current_round
            st.session_state[f"replay_frame_idx::{current_round}"] = 0
            st.session_state[f"replay_frame_slider::{current_round}"] = 0

    current_round = st.session_state["replay_round_value"]
    round_data = replay_data.get("rounds", {}).get(int(current_round), {})
    frame_count = len(round_data.get("frames", []))
    if frame_count == 0:
        st.info("Bu round icin frame yok.")
        return

    if replay_mode == "Akici (animasyon)":
        duration_map = {"0.5x": 160, "1x": 95, "2x": 60, "4x": 35}
        fig = render_replay_animation(
            replay_data=replay_data,
            round_num=int(current_round),
            map_name=parsed_data.get("map", "unknown"),
            side_filter=side_filter,
            show_labels=show_labels,
            show_direction=show_direction,
            show_grenades=show_grenades,
            show_kills=show_kills,
            show_dead_players=show_dead_players,
            show_trails=show_trails,
            trail_frames=trail_frames,
            show_bomb_events=show_bomb_events,
            show_sites=show_sites,
            frame_duration_ms=duration_map.get(speed_label, 95),
        )
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

        round_summary = get_round_frame_summary(replay_data, int(current_round), 0)
        info1, info2, info3, info4 = st.columns(4)
        info1.metric("Round Sure", f"{round_summary.get('duration_s', 0):.1f}s")
        info2.metric("Frame Sayisi", round_summary.get("frame_count", 0))
        info3.metric("CT Oyuncu", round_summary.get("ct_total", 0))
        info4.metric("T Oyuncu", round_summary.get("t_total", 0))
    else:
        frame_key = f"replay_frame_idx::{current_round}"
        slider_key = f"replay_frame_slider::{current_round}"

        if frame_key not in st.session_state:
            st.session_state[frame_key] = 0
        if slider_key not in st.session_state:
            st.session_state[slider_key] = st.session_state[frame_key]

        current_frame = int(st.session_state.get(slider_key, st.session_state.get(frame_key, 0)))
        current_frame = max(0, min(frame_count - 1, current_frame))

        c1, c2, c3 = st.columns([1.0, 4.2, 1.0])
        with c1:
            if st.button("< Frame", key=f"replay_prev_frame_btn::{current_round}"):
                current_frame = max(0, current_frame - 1)
        with c3:
            if st.button("Frame >", key=f"replay_next_frame_btn::{current_round}"):
                current_frame = min(frame_count - 1, current_frame + 1)

        st.session_state[frame_key] = current_frame
        st.session_state[slider_key] = current_frame

        with c2:
            selected_frame = st.slider(
                "Frame",
                min_value=0,
                max_value=frame_count - 1,
                key=slider_key,
            )

        current_frame = int(selected_frame)
        st.session_state[frame_key] = current_frame

        summary = get_round_frame_summary(replay_data, int(current_round), current_frame)
        st.markdown(f"**{summary.get('header', '')}**")
        info1, info2, info3, info4 = st.columns(4)
        info1.metric("Frame", f"{summary.get('frame_idx', 0)+1}/{summary.get('frame_count', 0)}")
        info2.metric("Kalan", f"{summary.get('remaining_s', 0):.1f}s")
        info3.metric("Visible Kills", summary.get("kills_visible", 0))
        info4.metric("Visible Utility", summary.get("grenades_visible", 0))

        fig = render_replay_frame(
            replay_data=replay_data,
            round_num=int(current_round),
            frame_idx=current_frame,
            map_name=parsed_data.get("map", "unknown"),
            side_filter=side_filter,
            show_labels=show_labels,
            show_direction=show_direction,
            show_grenades=show_grenades,
            show_kills=show_kills,
            show_dead_players=show_dead_players,
            show_trails=show_trails,
            trail_frames=trail_frames,
            show_bomb_events=show_bomb_events,
            show_sites=show_sites,
        )
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    round_tags = team_analysis.get("round_tags", [])
    selected_row = next((r for r in round_tags if int(r.get("round", 0)) == int(current_round)), None)
    if selected_row:
        tags = selected_row.get("all_tags", selected_row.get("tags", []))
        if tags:
            st.caption("Round tags: " + " | ".join(tags))


demo_path = st.session_state.get("demo_path")
if not demo_path:
    st.info("Devam etmek icin demo sec.")
    st.stop()

try:
    parsed_data = _load_parsed_demo(demo_path)
except Exception as exc:
    st.error(f"Parse hatasi: {exc}")
    st.code(traceback.format_exc(), language="python")
    st.stop()

st.success(f"Parse tamamlandi. Harita: {parsed_data.get('map')} | {parsed_data.get('total_rounds')} round")

players = parsed_data.get("players", [])
if not players:
    st.error("Demo'da oyuncu bulunamadi.")
    st.stop()

st.subheader("Oyuncu Sec")
player_name = st.selectbox("Analiz edilecek oyuncu", sorted(players))

if st.button("Oyuncu Analizi Baslat", use_container_width=True):
    with st.spinner("Oyuncu analizi yapiliyor..."):
        analysis = analyze_player(parsed_data, player_name)
    st.session_state["analysis"] = analysis
    st.session_state.pop("coaching", None)
    st.session_state.pop("report_path", None)

    if run_ai_coaching:
        if not api_key or "buraya" in api_key.lower():
            st.info("AI coaching atlandi: API key yok.")
        else:
            with st.spinner("AI coaching raporu uretiliyor..."):
                coaching = get_coaching(analysis)
                st.session_state["coaching"] = coaching
                st.session_state["report_path"] = save_report(coaching, player_name)

team_analysis = _ensure_team_analysis(parsed_data)

st.divider()
tab1, tab2, tab3 = st.tabs(["Bireysel Analiz", "Takim Analizi", "2D Replay"])

with tab1:
    if "analysis" not in st.session_state:
        st.info("Bireysel analiz icin once 'Oyuncu Analizi Baslat' butonuna bas.")
    else:
        _render_individual_tab(parsed_data, st.session_state["analysis"])

        if st.session_state.get("coaching"):
            st.divider()
            st.subheader("AI Coaching")
            st.markdown(st.session_state["coaching"])

            report_path = st.session_state.get("report_path")
            if report_path and Path(report_path).exists():
                with open(report_path, "r", encoding="utf-8") as fh:
                    text = fh.read()
                st.download_button(
                    "Raporu indir (.txt)",
                    data=text,
                    file_name=f"coaching_{st.session_state['analysis']['player']}.txt",
                    mime="text/plain",
                )

with tab2:
    _render_team_tab(parsed_data, team_analysis)

with tab3:
    _render_replay_tab(parsed_data, team_analysis)

