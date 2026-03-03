"""
utils.py
Yardımcı fonksiyonlar — heatmap, dosya işlemleri vb.
"""

import os
import json
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.lines import Line2D
from matplotlib.colors import LinearSegmentedColormap
import numpy as np
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


def _normalize_side_util(side_val) -> str:
    """Side değerini 'T' veya 'CT' olarak normalize eder."""
    s = str(side_val).strip().lower()
    if s in ("2", "t", "terrorist"):
        return "T"
    if s in ("3", "ct", "counter-terrorist", "counterterrorist"):
        return "CT"
    return s.upper()


def get_player_movement_positions(parsed_data: dict, player_name: str, side: str = None) -> list:
    """Oyuncunun maç boyunca geçtiği koordinatları döner. side='T' veya 'CT' ile filtrelenebilir."""
    points = []
    for p in parsed_data.get("player_positions", []):
        if p.get("player_name") != player_name:
            continue
        if side is not None:
            p_side = _normalize_side_util(p.get("side", ""))
            if p_side != side:
                continue
        try:
            points.append((float(p["x"]), float(p["y"])))
        except (TypeError, ValueError, KeyError):
            continue
    return points


def get_aim_points(parsed_data: dict, player_name: str) -> dict:
    """Aim analizi için shot pozisyonları ve hit noktalarını döner."""
    shot_points = []
    for s in parsed_data.get("shots", []):
        if s.get("shooter_name") != player_name:
            continue
        try:
            shot_points.append((float(s["shot_x"]), float(s["shot_y"])))
        except (TypeError, ValueError, KeyError):
            continue

    hit_points = []
    for d in parsed_data.get("damages", []):
        if d.get("attacker_name") != player_name:
            continue
        x = d.get("victim_x")
        y = d.get("victim_y")
        try:
            hit_points.append((float(x), float(y)))
        except (TypeError, ValueError):
            continue

    return {"shot_points": shot_points, "hit_points": hit_points}


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
    project_root = Path(__file__).resolve().parent.parent
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


def plot_player_activity_map(
    movement_positions: list,
    map_name: str,
    player_name: str,
    aim_points: dict = None,
    save_path: str = None,
    show_aim_points: bool = False,
    map_bounds: dict = None,
    output_dir: str = "outputs",
    output_prefix: str = "heatmap",
    title_suffix: str = "",
):
    """Build movement heatmap matrix and save both heatmap-only and map overlay outputs."""
    if not movement_positions:
        print("[!] Oyuncu movement verisi bulunamadi.")
        return None

    # Radar dosyasini yukle — oncelik sirasi:
    #   1. awpy'nin indirdigi RGBA PNG (~/.awpy/maps/<map>.png) — alpha kanali ideal maske saglar
    #   2. Proje kokündeki eski webp dosyasi (fallback)
    #   3. Yok ise sadece karanlik arka plan kullanilir
    try:
        import awpy.data
        awpy_map_path = awpy.data.MAPS_DIR / f"{map_name}.png"
    except Exception:
        awpy_map_path = None

    project_root = Path(__file__).resolve().parent.parent
    legacy_radar_file = "De_mirage_radar.webp" if map_name == "de_mirage" else None
    legacy_radar_path = project_root / legacy_radar_file if legacy_radar_file else None

    grid_w, grid_h = 1024, 1024
    radar_img = None
    radar_has_alpha = False

    if awpy_map_path and awpy_map_path.exists():
        radar_img = Image.open(awpy_map_path).convert("RGBA")
        radar_has_alpha = True
        print(f"[+] awpy radar haritasi kullaniliyor: {awpy_map_path.name}")
    elif legacy_radar_path and legacy_radar_path.exists():
        radar_img = Image.open(legacy_radar_path).convert("RGBA")
        radar_has_alpha = False
        print(f"[+] Proje radar haritasi kullaniliyor: {legacy_radar_path.name}")
    else:
        print("[!] Radar haritasi bulunamadi. 'awpy get maps' komutuyla indirebilirsiniz.")

    if radar_img is not None:
        img_w, img_h = radar_img.size
        if (img_w, img_h) != (grid_w, grid_h):
            radar_img = radar_img.resize((grid_w, grid_h), Image.LANCZOS)
            print(f"[*] Radar {img_w}x{img_h} -> {grid_w}x{grid_h} olceklendi.")

    map_info = {
        "de_mirage": {"pos_x": -3230, "pos_y": 1713, "scale": 5.0},
        "de_dust2": {"pos_x": -2476, "pos_y": 3239, "scale": 4.4},
        "de_inferno": {"pos_x": -2087, "pos_y": 3870, "scale": 4.9},
        "de_nuke": {"pos_x": -3453, "pos_y": 2887, "scale": 7.0},
        "de_ancient": {"pos_x": -2953, "pos_y": 2164, "scale": 5.0},
        "de_anubis": {"pos_x": -2796, "pos_y": 3328, "scale": 5.22},
        "de_vertigo": {"pos_x": -3168, "pos_y": 1762, "scale": 4.0},
    }.get(map_name)

    def world_to_pixel(x: float, y: float):
        if map_info:
            px = (x - map_info["pos_x"]) / map_info["scale"]
            py = (map_info["pos_y"] - y) / map_info["scale"]
            if grid_w != 1024:
                px = px * (grid_w / 1024.0)
            if grid_h != 1024:
                py = py * (grid_h / 1024.0)
            return px, py

        if isinstance(map_bounds, dict) and {"x_min", "x_max", "y_min", "y_max"}.issubset(map_bounds.keys()):
            dx = max(map_bounds["x_max"] - map_bounds["x_min"], 1e-9)
            dy = max(map_bounds["y_max"] - map_bounds["y_min"], 1e-9)
            px = (x - map_bounds["x_min"]) / dx * grid_w
            py = (map_bounds["y_max"] - y) / dy * grid_h
            return px, py

        return None, None

    # 1) Visit count matrix: counts[y, x]
    counts = np.zeros((grid_h, grid_w), dtype=np.float32)
    valid_points = 0
    for x, y in movement_positions:
        try:
            px, py = world_to_pixel(float(x), float(y))
        except (TypeError, ValueError):
            continue
        if px is None or py is None:
            continue
        ix = int(round(px))
        iy = int(round(py))
        if 0 <= ix < grid_w and 0 <= iy < grid_h:
            counts[iy, ix] += 1.0
            valid_points += 1

    if valid_points == 0:
        print("[!] Heatmap icin gecerli pixel noktasi bulunamadi.")
        return None

    # Gaussian smoothing — sigma büyük olmalı ki harita üzerinde akıcı
    # ısı dağılımı oluşsun (1024px grid için ~15-18px iyi sonuç verir).
    sigma = 16.0
    radius = int(3 * sigma)
    gy, gx = np.mgrid[-radius : radius + 1, -radius : radius + 1]
    kernel = np.exp(-(gx**2 + gy**2) / (2.0 * sigma**2))
    kernel /= max(kernel.sum(), 1e-12)

    out_shape = (
        counts.shape[0] + kernel.shape[0] - 1,
        counts.shape[1] + kernel.shape[1] - 1,
    )
    f_counts = np.fft.rfftn(counts, s=out_shape)
    f_kernel = np.fft.rfftn(kernel, s=out_shape)
    conv_full = np.fft.irfftn(f_counts * f_kernel, s=out_shape)
    sy = (kernel.shape[0] - 1) // 2
    sx = (kernel.shape[1] - 1) // 2
    density = conv_full[sy : sy + grid_h, sx : sx + grid_w]
    density = np.clip(density, 0.0, None)

    # 2) Normalize — percentile-based clipping ile hotspot dominasyonunu önle.
    # Spawn gibi aşırı yoğun tek noktalar tüm haritayı soldurmaz.
    dmax = float(density.max())
    if dmax > 0:
        flat = density[density > 0]
        if len(flat) > 0:
            clip_val = float(np.percentile(flat, 98))
            clip_val = max(clip_val, dmax * 0.15)
            density_clipped = np.clip(density, 0.0, clip_val)
            density_norm = density_clipped / clip_val
        else:
            density_norm = density / dmax
    else:
        density_norm = density
    # FFT kaynaklı çok küçük sayısal artıkları temizle.
    # Esik düsük tutuldu (0.001) — CT koridorlari gibi seyrek ziyaret edilen
    # yerler de görünür olsun.
    density_norm = np.where(density_norm >= 0.001, density_norm, 0.0)

    cmap = LinearSegmentedColormap.from_list(
        "green_yellow_red",
        [
            (0.0, "#00b050"),
            (0.65, "#ffdd00"),
            (1.0, "#cc0000"),
        ],
    )
    rgba = cmap(density_norm)
    # Sabit taban alpha kaldirildi — density=0 olan yerlerde alpha kesinlikle 0.
    # Bu harita siniri disina hafif Gaussian kuyrugu tasmasini engeller.
    # Koridorlar gibi dusuk yogunluklu alanlar icin x^0.40 egrisinin
    # dik baslangici yeterli gorunurluk saglar.
    alpha = np.where(
        density_norm > 0,
        np.clip(0.90 * np.power(density_norm, 0.40), 0.0, 0.92),
        0.0,
    )

    from scipy.ndimage import distance_transform_edt, gaussian_filter as gf

    if radar_has_alpha and radar_img is not None:
        # IDEAL YOL: awpy PNG dosyasinin alpha kanali harita sinirini piksel
        # hassasiyetinde tanimlar. Harita ici=255, dis alan=0.
        # Bu yaklasim hem B site hem T spawn gibi karanlik alanlari dogru kapsar.
        alpha_channel = np.asarray(radar_img)[:, :, 3].astype(np.float32) / 255.0
        # Kenarlari hafifce yumusat (sert kenar yerine smooth gecis)
        map_mask = gf(alpha_channel, sigma=3.0)
        map_mask = np.clip(map_mask, 0.0, 1.0)
    else:
        # FALLBACK: Alpha kanali yoksa oyuncu pozisyon verisinden maske uret.
        # Oyuncunun gercekten bulundugu piksellerden 35px mesafedeki her alan
        # harita ici sayilir — radar brightness'a bagli degil.
        visited_binary = counts > 0
        dist_from_visited = distance_transform_edt(~visited_binary)
        map_mask = (dist_from_visited <= 35).astype(np.float32)
        map_mask = gf(map_mask, sigma=6.0)
        map_mask = np.clip(map_mask, 0.0, 1.0)

    alpha *= map_mask

    rgba[:, :, 3] = alpha

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    heatmap_only_path = out_dir / f"{output_prefix}_only.png"
    heatmap_on_map_path = out_dir / f"{output_prefix}_on_map.png"

    # 3) Heatmap only output
    fig_only, ax_only = plt.subplots(figsize=(10, 10))
    ax_only.imshow(rgba, extent=[0, grid_w, grid_h, 0], interpolation="bilinear")
    ax_only.set_xlim(0, grid_w)
    ax_only.set_ylim(grid_h, 0)
    ax_only.axis("off")
    plt.tight_layout()
    fig_only.savefig(heatmap_only_path, dpi=150, bbox_inches="tight", pad_inches=0)
    plt.close(fig_only)

    # 4) Overlay on map
    fig, ax = plt.subplots(figsize=(10, 10))
    if radar_img is not None:
        ax.imshow(radar_img, extent=[0, grid_w, grid_h, 0], aspect="equal")
    else:
        ax.set_facecolor("#0e1117")
        fig.patch.set_facecolor("#0e1117")

    ax.imshow(rgba, extent=[0, grid_w, grid_h, 0], interpolation="bilinear", zorder=4)
    ax.set_xlim(0, grid_w)
    ax.set_ylim(grid_h, 0)
    title_text = f"Heatmap - {player_name} ({map_name})"
    if title_suffix:
        title_text += f" — {title_suffix}"
    ax.set_title(title_text, fontsize=13, color="white")
    ax.axis("off")

    # Colorbar — yoğunluk skalası
    from matplotlib.cm import ScalarMappable
    from matplotlib.colors import Normalize
    sm = ScalarMappable(cmap=cmap, norm=Normalize(vmin=0, vmax=1))
    sm.set_array([])
    cbar = fig.colorbar(sm, ax=ax, fraction=0.046, pad=0.04, shrink=0.8)
    cbar.set_label("Yogunluk", color="white", fontsize=11)
    cbar.ax.yaxis.set_tick_params(color="white")
    cbar.ax.tick_params(labelcolor="white")

    plt.tight_layout()
    fig.savefig(heatmap_on_map_path, dpi=150, bbox_inches="tight", pad_inches=0)

    if save_path:
        fig.savefig(save_path, dpi=150, bbox_inches="tight", pad_inches=0)

    print(f"[+] Heatmap only saved: {heatmap_only_path}")
    print(f"[+] Heatmap on map saved: {heatmap_on_map_path}")

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
