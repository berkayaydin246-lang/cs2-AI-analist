"""
utils.py
Yardımcı fonksiyonlar — heatmap, dosya işlemleri vb.
"""

import os
import json
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.lines import Line2D
import pandas as pd
from pathlib import Path
from PIL import Image


def list_demos(demo_dir: str = "demos") -> list:
    """demos/ klasöründeki .dem dosyalarını listeler."""
    path = Path(demo_dir)
    if not path.exists():
        return []
    return [str(f) for f in path.glob("*.dem")]


def load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_death_positions(parsed_data: dict, player_name: str) -> list:
    """Oyuncunun öldüğü pozisyonları döner."""
    kills = parsed_data.get("kills", [])
    positions = []
    for k in kills:
        if k.get("victim_name") == player_name:
            x = k.get("victim_X") or k.get("victim_x")
            y = k.get("victim_Y") or k.get("victim_y")
            if x and y:
                positions.append((float(x), float(y)))
    return positions


def get_grenade_positions(parsed_data: dict, player_name: str) -> list:
    """Oyuncunun attığı grenade'lerin koordinatlı olanlarını döner."""
    grenades = parsed_data.get("grenades", [])
    positions = []

    def _to_float(v):
        if v is None or v == "":
            return None
        try:
            return float(v)
        except (ValueError, TypeError):
            return None

    for g in grenades:
        if g.get("thrower_name") != player_name:
            continue

        end_x = _to_float(g.get("nade_end_x", g.get("nade_x")))
        end_y = _to_float(g.get("nade_end_y", g.get("nade_y")))
        start_x = _to_float(g.get("nade_start_x"))
        start_y = _to_float(g.get("nade_start_y"))
        raw_path = g.get("nade_path", [])
        path = []
        if isinstance(raw_path, list):
            for pt in raw_path:
                if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                    px = _to_float(pt[0])
                    py = _to_float(pt[1])
                    if px is not None and py is not None:
                        path.append((px, py))

        if end_x is not None and end_y is not None:
            positions.append({
                "x": end_x,
                "y": end_y,
                "start_x": start_x,
                "start_y": start_y,
                "end_x": end_x,
                "end_y": end_y,
                "path": path,
                "type": g.get("grenade_type", "unknown"),
            })
    return positions


def plot_death_heatmap(positions: list, map_name: str, player_name: str,
                       grenade_positions: list = None, save_path: str = None):
    if not positions and not grenade_positions:
        print("[!] Ölüm/utility pozisyonu verisi bulunamadı.")
        return None

    # awpy 2.x map koordinat verileri
    MAP_INFO = {
        "de_mirage":  {"pos_x": -3230, "pos_y": 1713,  "scale": 5.0},
        "de_dust2":   {"pos_x": -2476, "pos_y": 3239,  "scale": 4.4},
        "de_inferno": {"pos_x": -2087, "pos_y": 3870,  "scale": 4.9},
        "de_nuke":    {"pos_x": -3453, "pos_y": 2887,  "scale": 7.0},
        "de_ancient": {"pos_x": -2953, "pos_y": 2164,  "scale": 5.0},
        "de_anubis":  {"pos_x": -2796, "pos_y": 3328,  "scale": 5.22},
        "de_vertigo": {"pos_x": -3168, "pos_y": 1762,  "scale": 4.0},
    }

    map_info = MAP_INFO.get(map_name, {"pos_x": 0, "pos_y": 0, "scale": 5.0})
    pos_x  = map_info["pos_x"]
    pos_y  = map_info["pos_y"]
    scale  = map_info["scale"]

    def game_to_pixel(x, y, img_size=1024):
        px = (x - pos_x) / scale
        py = (pos_y - y) / scale
        return px, py

    # Harita radar görselini yükle
    fig, ax = plt.subplots(figsize=(10, 10))
    radar_map = {
        "de_mirage": "De_mirage_radar.webp",
    }
    radar_file = radar_map.get(map_name)
    project_root = Path(__file__).parent.parent
    radar_path = project_root / radar_file if radar_file else None

    if radar_path and radar_path.exists():
        img = Image.open(radar_path)
        ax.imshow(img, extent=[0, 1024, 1024, 0], aspect="equal")
    else:
        try:
            from awpy.plot import plot_map
            fig, ax = plot_map(map_name=map_name, map_type="original")
        except Exception:
            ax.set_facecolor("#1a1a2e")
            fig.patch.set_facecolor("#16213e")

    converted = [game_to_pixel(p[0], p[1]) for p in positions]
    xs = [p[0] for p in converted]
    ys = [p[1] for p in converted]

    ax.scatter(xs, ys, c="red", s=180, alpha=0.9,
               edgecolors="white", linewidths=1.5, zorder=5)

    for i, (x, y) in enumerate(zip(xs, ys), 1):
        ax.annotate(str(i), (x, y), textcoords="offset points",
                    xytext=(6, 6), fontsize=11, color="white",
                    fontweight="bold", zorder=6)

    # Grenade pozisyonlarını çiz
    grenade_colors = {
        "smoke": "#aaaaaa",       # gri
        "flash": "#ffd700",       # sarı
        "molotov": "#ff4444",     # kırmızı
        "incendiary": "#ff4444",  # kırmızı
        "he_grenade": "#00cc44",  # yeşil
        "decoy": "#888888",       # koyu gri
    }
    grenade_labels = {
        "smoke": "Smoke",
        "flash": "Flash",
        "molotov": "Molotov",
        "incendiary": "Incendiary",
        "he_grenade": "HE",
        "decoy": "Decoy",
    }
    grenade_legend_added = set()
    route_drawn = False
    if grenade_positions:
        for gp in grenade_positions:
            gx, gy = game_to_pixel(gp["x"], gp["y"])
            gtype = gp["type"]
            color = grenade_colors.get(gtype, "#ffffff")

            path = gp.get("path") or []
            if len(path) >= 2:
                pixel_path = [game_to_pixel(p[0], p[1]) for p in path]
                pxs = [p[0] for p in pixel_path]
                pys = [p[1] for p in pixel_path]
                ax.plot(pxs, pys, color=color, linewidth=2.0, alpha=0.8, zorder=3)
                route_drawn = True
            elif gp.get("start_x") is not None and gp.get("start_y") is not None:
                sx, sy = game_to_pixel(gp["start_x"], gp["start_y"])
                ex, ey = game_to_pixel(gp.get("end_x", gp["x"]), gp.get("end_y", gp["y"]))
                ax.plot([sx, ex], [sy, ey], color=color, linewidth=2.0, alpha=0.75, zorder=3)
                route_drawn = True

            ax.scatter(gx, gy, c=color, s=80, alpha=0.85,
                       edgecolors="white", linewidths=0.8, zorder=4, marker="o")
            grenade_legend_added.add(gtype)

    ax.set_title(f"Ölüm & Utility Pozisyonları — {player_name} ({map_name})",
                 fontsize=13, color="white")
    ax.axis("off")

    legend_handles = [mpatches.Patch(color="red", label=f"Ölüm ({len(positions)} adet)")]
    for gtype in grenade_legend_added:
        color = grenade_colors.get(gtype, "#ffffff")
        label = grenade_labels.get(gtype, gtype)
        legend_handles.append(mpatches.Patch(color=color, label=label))
    if route_drawn:
        legend_handles.append(Line2D([0], [0], color="#ffffff", lw=2, label="Utility Rotası (Trajectory)"))
    ax.legend(handles=legend_handles, facecolor="#1a1a2e", labelcolor="white")
    plt.tight_layout()

    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches="tight")

    return fig


def format_stats_table(stats: dict) -> str:
    """İstatistikleri düzgün bir metin tablosuna çevirir."""
    lines = [
        f"{'İstatistik':<25} {'Değer':>10}",
        "-" * 37,
        f"{'K/D Oranı':<25} {stats.get('kd_ratio', 0):>10}",
        f"{'Kill':<25} {stats.get('kills', 0):>10}",
        f"{'Death':<25} {stats.get('deaths', 0):>10}",
        f"{'ADR':<25} {stats.get('adr', 0):>10}",
        f"{'Headshot Oranı':<25} {str(stats.get('hs_rate', 0)) + '%':>10}",
        f"{'Toplam Hasar':<25} {stats.get('total_damage', 0):>10}",
    ]
    return "\n".join(lines)
