/**
 * replay.js — Canvas 2D Replay Engine
 *
 * Renders player positions tick-by-tick on a CS2 radar background.
 * Features: trails, HP/armor bars, yaw arrows, kill markers, bomb events with
 * explosion particles, grenade flight icons, persistent smoke/molotov areas,
 * flash blind radii + player blind tint, HE blast rings, bullet tracers,
 * weapon-class glyphs, defuse-kit badges, bomb-carrier badge, enemy position
 * zones and a focused-player mode (long aim line + highlight + info card).
 */

// ── Map info (world-to-pixel) ─────────────────────────────────────────────────
// Matches awpy 2.x coordinate origin and scale for each map.
const MAP_INFO = {
  de_mirage:   { pos_x: -3230, pos_y:  1713, scale: 5.00 },
  de_dust2:    { pos_x: -2476, pos_y:  3239, scale: 4.40 },
  de_inferno:  { pos_x: -2087, pos_y:  3870, scale: 4.90 },
  de_nuke:     { pos_x: -3453, pos_y:  2887, scale: 7.00 },
  de_overpass: { pos_x: -4831, pos_y:  1781, scale: 5.20 },
  de_ancient:  { pos_x: -2953, pos_y:  2164, scale: 5.00 },
  de_anubis:   { pos_x: -2796, pos_y:  3328, scale: 5.22 },
  de_vertigo:  { pos_x: -3168, pos_y:  1762, scale: 4.00 },
  de_cache:    { pos_x: -2000, pos_y:  3250, scale: 5.50 },
};

// awpy radar images are 1024×1024 — world→pixel coords are in that space,
const RADAR_SIZE  = 1024;
const CANVAS_SIZE = RADAR_SIZE;
const SCALE_RATIO = CANVAS_SIZE / RADAR_SIZE;
const TICK_RATE   = 64;
const MIN_ZOOM    = 1;
const MAX_ZOOM    = 4;
const ZOOM_STEP   = 1.22;

/**
 * Convert CS2 world coordinates to canvas pixel coordinates.
 * 1) world → radar pixel (0-1024) using awpy formula
 * 2) scale radar pixel -> canvas pixel (0-1024)
 * Returns null if the position maps outside playable area.
 */
function worldToCanvas(wx, wy, mi) {
  if (!isFinite(wx) || !isFinite(wy)) return null;
  // Step 1: radar coordinates (awpy 1024×1024 space)
  const rx = (wx - mi.pos_x) / mi.scale;
  const ry = (mi.pos_y - wy) / mi.scale;
  // Step 2: scale to canvas
  const cx = rx * SCALE_RATIO;
  const cy = ry * SCALE_RATIO;
  // Discard if clearly off-map (with generous margin for edge positions)
  const MARGIN = 60;
  if (cx < -MARGIN || cx > CANVAS_SIZE + MARGIN ||
      cy < -MARGIN || cy > CANVAS_SIZE + MARGIN) return null;
  return { cx, cy };
}

/** Convert a distance in world units to canvas pixels. */
function worldUnitsToPx(units, mi) {
  return (units / mi.scale) * SCALE_RATIO;
}

/**
 * CS2 yaw (degrees, 0 = +X east, CCW positive, world +Y = north) → canvas
 * direction vector. Canvas Y grows downward, so the Y component is negated.
 */
function yawToDir(yawDeg) {
  const rad = (yawDeg * Math.PI) / 180;
  return { dx: Math.cos(rad), dy: -Math.sin(rad) };
}

// ── Grenade icon config ───────────────────────────────────────────────────────
const GRENADE_CONFIG = {
  smoke:      { color: '#94a3b8', glow: 'rgba(148,163,184,.5)', label: 'S',  r: 7 },
  flash:      { color: '#fef9c3', glow: 'rgba(254,249,195,.7)', label: 'F',  r: 6 },
  molotov:    { color: '#f97316', glow: 'rgba(249,115,22,.6)',  label: 'M',  r: 7 },
  incendiary: { color: '#f97316', glow: 'rgba(249,115,22,.6)',  label: 'M',  r: 7 },
  he_grenade: { color: '#4ade80', glow: 'rgba(74,222,128,.5)', label: 'HE', r: 7 },
  hegrenade:  { color: '#4ade80', glow: 'rgba(74,222,128,.5)', label: 'HE', r: 7 },
  decoy:      { color: '#a78bfa', glow: 'rgba(167,139,250,.4)', label: 'D',  r: 5 },
};

const UTILITY_ICON_ASSETS = {
  smoke:   '/static/assets/replay/icons/smoke.svg',
  flash:   '/static/assets/replay/icons/flash.svg',
  he:      '/static/assets/replay/icons/he.svg',
  molotov: '/static/assets/replay/icons/molotov.svg',
  decoy:   '/static/assets/replay/icons/decoy.svg',
};

// Optional high-quality texture paths live here. Missing effects are not
// requested at all, so the browser console stays clean until a pack is added.
const REPLAY_ASSET_MANIFEST = '/static/assets/replay/manifest.json';

const GRENADE_FLIGHT_TICKS = {
  smoke: 105,
  flash: 78,
  he_grenade: 90,
  hegrenade: 90,
  molotov: 112,
  incendiary: 112,
  decoy: 95,
  unknown: 96,
};

const KILL_FLASH_TICKS = 320;
const GRENADE_IMPACT_TICKS = 256;

// Bullet tracer lifetime (~200 ms)
const TRACER_TICKS = 18;
const TRACER_LENGTH_PX = 210;

// Effect area radii in world units and timing (ticks)
const SMOKE_RADIUS_UNITS   = 144;  // CS2 smoke cloud radius
const SMOKE_EXPAND_TICKS   = 64;   // cloud fills over ~1 s
const SMOKE_FADE_TICKS     = 96;   // dissipates over last ~1.5 s
const MOLOTOV_RADIUS_UNITS = 120;  // fire zone radius
const MOLOTOV_EXPAND_TICKS = 24;
const MOLOTOV_FADE_TICKS   = 48;
const FLASH_RADIUS_UNITS   = 210;  // effective blind radius (visual)
const FLASH_RING_TICKS     = 56;
const HE_RADIUS_UNITS      = 110;  // blast ring radius (visual)
const HE_RING_TICKS        = 58;
const HE_DUST_TICKS        = 84;

// Bomb events
const BOMB_EVENT_FLASH_TICKS = 448;   // pickup/drop/defuse icon lifetime
const EXPLOSION_TICKS        = 170;   // shockwave rings lifetime
const EXPLOSION_RING_STAGGER = 7;     // ticks between successive rings
const EXPLOSION_RADIUS_UNITS = 420;   // outermost shockwave radius
const EXPLOSION_PARTICLES    = 9;
const ZONE_SAMPLE_STRIDE     = 10;
const ZONE_RADIUS_UNITS      = 190;

const PLAYER_STYLE = {
  CT: {
    fill: '#8fb8ee',
    stroke: '#c4dbff',
    rim: 'rgba(11,18,31,0.92)',
    glow: 'rgba(143,184,238,0.32)',
    label: '#cfe2ff',
    aim: 'rgba(190,218,255,0.86)',
    cone: 'rgba(143,184,238,0.13)',
  },
  T: {
    fill: '#e7c56b',
    stroke: '#fff0b7',
    rim: 'rgba(15,13,7,0.92)',
    glow: 'rgba(231,197,107,0.30)',
    label: '#f5df99',
    aim: 'rgba(255,232,163,0.86)',
    cone: 'rgba(231,197,107,0.13)',
  },
};

const PLAYER_MARKER_R = 14.2;
const PLAYER_HIT_RADIUS = 20;
const DAMAGE_FLASH_TICKS = 28;
const DAMAGE_MIN_DELTA = 1;
const BLIND_PIE_MAX_RADIUS = 54;

/** Deterministic pseudo-random in [0,1) from an integer seed (mulberry32). */
function seededRandom(seed) {
  let t = (seed + 0x6D2B79F5) | 0;
  t = Math.imul(t ^ (t >>> 15), t | 1);
  t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
}

function utilityKind(type) {
  const t = String(type || '').toLowerCase().replace(/^weapon_/, '');
  if (t.includes('smoke')) return 'smoke';
  if (t.includes('flash')) return 'flash';
  if (t.includes('molotov') || t.includes('incendiary') || t.includes('incgrenade')) return 'molotov';
  if (t.includes('he')) return 'he';
  return 'decoy';
}

/** Map a CS2 weapon name (display or weapon_* id) to a coarse weapon class. */
function weaponClass(weaponName) {
  const n = String(weaponName || '')
    .toLowerCase()
    .replace(/^weapon_/, '')
    .replace(/[\s-]/g, '');
  if (!n) return null;
  if (n.includes('knife') || n === 'bayonet' || n.includes('karambit')) return 'knife';
  if (n === 'c4' || n.includes('c4')) return 'c4';
  if (['awp', 'ssg08', 'scar20', 'g3sg1'].some(w => n.includes(w))) return 'sniper';
  if (['ak47', 'm4a4', 'm4a1', 'galil', 'famas', 'aug', 'sg553', 'sg556'].some(w => n.includes(w))) return 'rifle';
  if (['mp9', 'mac10', 'mp7', 'mp5', 'ump45', 'p90', 'bizon'].some(w => n.includes(w))) return 'smg';
  if (['nova', 'xm1014', 'mag7', 'sawedoff'].some(w => n.includes(w))) return 'shotgun';
  if (['m249', 'negev'].some(w => n.includes(w))) return 'mg';
  if (['hegrenade', 'flashbang', 'smokegrenade', 'molotov', 'incgrenade', 'decoy', 'grenade'].some(w => n.includes(w))) return 'grenade';
  if (n.includes('taser') || n.includes('zeus')) return 'zeus';
  return 'pistol';
}

function isBulletWeapon(weaponName) {
  const cls = weaponClass(weaponName);
  return Boolean(cls && !['knife', 'grenade', 'c4', 'zeus'].includes(cls));
}

function weaponAssetKey(weaponName, side = '') {
  const n = String(weaponName || '')
    .toLowerCase()
    .replace(/^weapon_/, '')
    .replace(/[\s-]/g, '')
    .replace(/_+/g, '');
  if (!n) return null;
  if (n.includes('ak47') || n === 'ak') return 'ak47';
  if (n.includes('awp')) return 'awp';
  if (n.includes('ssg08') || n.includes('ssg')) return 'ssg';
  if (n.includes('deagle') || n.includes('deserteagle')) return 'deagle';
  if (n.includes('usp')) return 'usp';
  if (n.includes('m4a1')) return 'm4a1';
  if (n.includes('m4a4') || n === 'm4') return 'm4a4';
  if (n.includes('c4')) return 'c4';
  if (n.includes('knife') || n.includes('bayonet') || n.includes('karambit')) {
    return String(side || '').toUpperCase() === 'CT' ? 'knife_ct' : 'knife_t';
  }
  if (n.includes('glock')) return 'glock';
  if (n.includes('mp5')) return 'mp5';
  return null;
}

// ── ReplayEngine ──────────────────────────────────────────────────────────────
class ReplayEngine {
  /**
   * @param {HTMLCanvasElement} canvas
   * @param {object} data   API response from /api/demo/{id}/replay/{round}
   * @param {string|null} radarUrl  Blob URL for the map radar image
   */
  constructor(canvas, data, radarUrl) {
    this.canvas   = canvas;
    this.canvas.width = CANVAS_SIZE;
    this.canvas.height = CANVAS_SIZE;
    this.ctx      = canvas.getContext('2d');
    this.ctx.imageSmoothingEnabled = true;
    this.ctx.imageSmoothingQuality = 'high';
    this.data     = data;
    this.radarUrl = radarUrl;
    this.radarImg = null;
    this.radarSource = null;
    this._iconAssets = {};
    this._effectAssets = {};
    this._weaponAssets = {};

    this.frames   = data.frames   || [];
    this.kills    = data.kills    || [];
    this.bombs    = data.bombs    || [];
    this.grenades = data.grenades || [];
    this.shots    = data.shots    || [];
    this.effects  = data.effects  || [];
    this.kills.sort((a, b) => (a.tick || 0) - (b.tick || 0));
    this.bombs.sort((a, b) => (a.tick || 0) - (b.tick || 0));
    this.grenades.sort((a, b) => (a.tick || 0) - (b.tick || 0));
    this.shots.sort((a, b) => (a.tick || 0) - (b.tick || 0));
    this.mapName  = data.map || 'de_mirage';
    this.mapInfo  = MAP_INFO[this.mapName] || MAP_INFO.de_mirage;

    this.currentFrame = 0;
    this._renderTick = Number(this.frames[0]?.tick) || 0;
    this.playing      = false;
    this._raf         = null;
    this._lastTime    = 0;
    this._timeRemainderMs = 0;
    this.speed        = 1.0;

    this.showTrails = true;
    this.showLabels = true;
    this.showTracers = true;
    this.showEffects = true;
    this.showZones = false;

    // Viewport state. Pan is stored as the top-left corner in base radar pixels.
    this.zoom = 1;
    this.panX = 0;
    this.panY = 0;
    this._dragging = false;
    this._didDrag = false;
    this._lastPointer = null;

    // Focused-player mode: player name or null
    this.selectedPlayer = null;
    this.onPlayerSelect = null; // (nameOrNull) => void

    // Trail history: playerName -> [{cx,cy}]
    this._trails = {};

    // Callbacks
    this.onFrameChange     = null; // (idx, total) => void
    this.onPlayStateChange = null; // (playing) => void

    // Active kill flashes
    this._activeKills = [];

    // Active bomb icons
    this._activeBombEvents = [];

    // Active grenade states
    this._activeGrenades = [];

    // Active bullet tracers
    this._activeShots = [];

    // Bomb state derived from bomb events at the current tick
    this._bombCarrier = null;   // player name currently holding the bomb
    this._plantedBomb = null;   // {x, y, tick} while the bomb is planted

    // Blind intervals precomputed from flash effects:
    // [{player, start, end}] — player is blind between start..end ticks
    this._blindIntervals = [];
    for (const ef of this.effects) {
      if ((ef.type || '') !== 'flash' || !Array.isArray(ef.blinded)) continue;
      for (const b of ef.blinded) {
        const player = String(b.player || b.name || b.victim || '');
        const start = Number.isFinite(Number(b.start_tick)) ? Number(b.start_tick) : (Number(ef.tick) || 0);
        const explicitEnd = Number(b.end_tick);
        const durationS = Number(b.duration_s);
        const blindDuration = Number(b.blind_duration);
        let durationTicks = Number.isFinite(durationS) && durationS > 0
          ? Math.round(durationS * TICK_RATE)
          : NaN;
        if (!Number.isFinite(durationTicks) && Number.isFinite(blindDuration) && blindDuration > 0) {
          // demoparser's blind_duration is seconds; some frontend fixtures use ticks.
          durationTicks = blindDuration > 16 ? Math.round(blindDuration) : Math.round(blindDuration * TICK_RATE);
        }
        const end = Number.isFinite(explicitEnd) ? explicitEnd : start + (Number.isFinite(durationTicks) ? durationTicks : 0);
        const strength = Number(b.strength);
        if (!player || end - start <= 0.2 * TICK_RATE) continue; // ignore negligible blinds
        this._blindIntervals.push({
          player,
          start,
          end,
          strength: Number.isFinite(strength) ? Math.max(0, Math.min(1, strength)) : null,
        });
      }
    }
    this._damagePulses = this._buildDamagePulses();

    // Reset activation flags
    this.kills.forEach(k => { k._activated = false; });
    this.bombs.forEach(b => { b._activated = false; });
    this.grenades.forEach(g => { g._activated = false; });
    this.shots.forEach(s => { s._activated = false; });

    // Hit-testable positions of players drawn in the last frame
    this._lastDrawnPlayers = [];

    // Lightweight aggregate position layer for the optional Zones overlay.
    this._zoneSamples = this._buildZoneSamples();

    this._installCanvasInteractions();
  }

  async init() {
    const [radarImg] = await Promise.all([
      this.radarUrl ? this._loadImage(this.radarUrl) : Promise.resolve(null),
      this._loadReplayAssets(),
    ]);
    this.radarImg = radarImg;
    this.radarSource = this._prepareRadarSource(this.radarImg);
    this._draw();
  }

  _loadImage(src) {
    return new Promise((resolve) => {
      const img = new Image();
      img.onload  = () => resolve(img);
      img.onerror = () => resolve(null);
      img.src = src;
    });
  }

  _prepareRadarSource(img) {
    if (!img) return null;
    try {
      const canvas = document.createElement('canvas');
      canvas.width = img.naturalWidth || img.width || RADAR_SIZE;
      canvas.height = img.naturalHeight || img.height || RADAR_SIZE;
      const ctx = canvas.getContext('2d');
      ctx.imageSmoothingEnabled = true;
      ctx.imageSmoothingQuality = 'high';
      ctx.filter = 'brightness(1.18) saturate(1.06) contrast(1.03)';
      ctx.globalAlpha = 0.96;
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
      return canvas;
    } catch {
      return img;
    }
  }

  async _loadReplayAssets() {
    const manifest = await this._loadAssetManifest();
    const iconEntries = Object.entries(manifest.icons || UTILITY_ICON_ASSETS);
    await Promise.all(iconEntries.map(async ([kind, src]) => {
      this._iconAssets[kind] = await this._loadImage(src);
    }));

    const weaponEntries = Object.entries(manifest.weapons || {});
    await Promise.all(weaponEntries.map(async ([kind, src]) => {
      this._weaponAssets[kind] = await this._loadImage(src);
    }));

    const effectEntries = Object.entries(manifest.effects || {});
    await Promise.all(effectEntries.map(async ([kind, candidates]) => {
      const entry = this._normalizeAssetEntry(candidates);
      this._effectAssets[kind] = entry?.src
        ? { ...entry, img: await this._loadImage(entry.src) }
        : null;
    }));
    for (const kind of ['heExplosion', 'molotovFire', 'smokePuff']) {
      if (!(kind in this._effectAssets)) this._effectAssets[kind] = null;
    }
  }

  async _loadAssetManifest() {
    try {
      const res = await fetch(REPLAY_ASSET_MANIFEST, { cache: 'no-store' });
      if (!res.ok) throw new Error('asset manifest unavailable');
      return await res.json();
    } catch {
      return { icons: UTILITY_ICON_ASSETS, effects: {} };
    }
  }

  _normalizeAssetEntry(entry) {
    if (!entry) return null;
    if (typeof entry === 'string') return { src: entry };
    if (Array.isArray(entry)) {
      const src = entry.find(Boolean);
      return src ? { src } : null;
    }
    if (typeof entry === 'object' && entry.src) {
      return {
        src: entry.src,
        frames: Number(entry.frames) || null,
        alpha: Number.isFinite(Number(entry.alpha)) ? Number(entry.alpha) : 1,
        scale: Number.isFinite(Number(entry.scale)) ? Number(entry.scale) : 1,
      };
    }
    return null;
  }

  _drawEffectSprite(ctx, img, x, y, size, alpha = 1, age = 0, duration = 1, rotation = 0) {
    const meta = img && img.img ? img : null;
    if (meta) {
      size *= meta.scale || 1;
      alpha *= meta.alpha ?? 1;
      img = meta.img;
    }
    if (!img || size <= 0 || alpha <= 0) return false;
    const iw = img.naturalWidth || img.width || 0;
    const ih = img.naturalHeight || img.height || 0;
    if (!iw || !ih) return false;
    const frames = Math.max(1, meta?.frames || Math.floor(iw / ih));
    const frameW = iw / frames;
    const t = Math.max(0, Math.min(1, age / Math.max(1, duration)));
    const frame = Math.min(frames - 1, Math.floor(t * frames));

    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(rotation);
    ctx.globalAlpha *= alpha;
    ctx.drawImage(img, frame * frameW, 0, frameW, ih, -size / 2, -size / 2, size, size);
    ctx.restore();
    return true;
  }

  _installCanvasInteractions() {
    this.canvas.onclick = null;
    this.canvas.onwheel = (e) => {
      e.preventDefault();
      const pt = this._canvasPointFromEvent(e);
      const nextZoom = this.zoom * (e.deltaY < 0 ? ZOOM_STEP : 1 / ZOOM_STEP);
      this._zoomAt(pt.x, pt.y, nextZoom);
    };

    this.canvas.onpointerdown = (e) => {
      if (e.button !== 0) return;
      this._dragging = true;
      this._didDrag = false;
      this._lastPointer = { clientX: e.clientX, clientY: e.clientY };
      this.canvas.setPointerCapture?.(e.pointerId);
      this.canvas.classList.add('is-panning');
      e.preventDefault();
    };

    this.canvas.onpointermove = (e) => {
      if (!this._dragging || !this._lastPointer) return;
      const dx = e.clientX - this._lastPointer.clientX;
      const dy = e.clientY - this._lastPointer.clientY;
      this._lastPointer = { clientX: e.clientX, clientY: e.clientY };
      if (Math.abs(dx) + Math.abs(dy) > 2) this._didDrag = true;

      const rect = this.canvas.getBoundingClientRect();
      const scaleX = this.canvas.width / Math.max(rect.width, 1);
      const scaleY = this.canvas.height / Math.max(rect.height, 1);
      this.panX -= (dx * scaleX) / this.zoom;
      this.panY -= (dy * scaleY) / this.zoom;
      this._clampViewport();
      this._draw();
      e.preventDefault();
    };

    const finishPointer = (e) => {
      if (!this._dragging) return;
      const shouldSelect = !this._didDrag;
      this._dragging = false;
      this._lastPointer = null;
      this.canvas.classList.remove('is-panning');
      this.canvas.releasePointerCapture?.(e.pointerId);
      if (shouldSelect) {
        const pt = this._canvasPointFromEvent(e);
        this._handleCanvasClick(pt.x, pt.y);
      }
      e.preventDefault();
    };

    this.canvas.onpointerup = finishPointer;
    this.canvas.onpointercancel = finishPointer;
    this.canvas.ondblclick = (e) => {
      e.preventDefault();
      this.resetView();
    };
  }

  _canvasPointFromEvent(e) {
    const rect = this.canvas.getBoundingClientRect();
    return {
      x: (e.clientX - rect.left) * (this.canvas.width / Math.max(rect.width, 1)),
      y: (e.clientY - rect.top) * (this.canvas.height / Math.max(rect.height, 1)),
    };
  }

  _screenToBase(x, y) {
    return {
      cx: x / this.zoom + this.panX,
      cy: y / this.zoom + this.panY,
    };
  }

  _baseToScreen(pos) {
    return {
      cx: (pos.cx - this.panX) * this.zoom,
      cy: (pos.cy - this.panY) * this.zoom,
    };
  }

  _clampViewport() {
    this.zoom = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, this.zoom));
    const viewSize = CANVAS_SIZE / this.zoom;
    const maxPan = Math.max(0, CANVAS_SIZE - viewSize);
    this.panX = Math.max(0, Math.min(maxPan, this.panX));
    this.panY = Math.max(0, Math.min(maxPan, this.panY));
  }

  _zoomAt(screenX, screenY, nextZoom) {
    const before = this._screenToBase(screenX, screenY);
    this.zoom = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, nextZoom));
    this.panX = before.cx - screenX / this.zoom;
    this.panY = before.cy - screenY / this.zoom;
    this._clampViewport();
    this._draw();
  }

  zoomIn() {
    this._zoomAt(CANVAS_SIZE / 2, CANVAS_SIZE / 2, this.zoom * ZOOM_STEP);
  }

  zoomOut() {
    this._zoomAt(CANVAS_SIZE / 2, CANVAS_SIZE / 2, this.zoom / ZOOM_STEP);
  }

  resetView() {
    this.zoom = 1;
    this.panX = 0;
    this.panY = 0;
    this._draw();
  }

  // ── Focused-player mode ──────────────────────────────────────────────────────
  setSelectedPlayer(name) {
    this.selectedPlayer = name || null;
    this._draw();
    if (this.onPlayerSelect) this.onPlayerSelect(this.selectedPlayer);
  }

  _handleCanvasClick(x, y) {
    const base = this._screenToBase(x, y);
    const hitRadius = PLAYER_HIT_RADIUS / this.zoom;
    let best = null;
    let bestDist = Infinity;
    for (const p of this._lastDrawnPlayers) {
      if (!p._pos) continue;
      const d = Math.hypot(p._pos.cx - base.cx, p._pos.cy - base.cy);
      if (d <= hitRadius && d < bestDist) { best = p; bestDist = d; }
    }
    if (best) {
      // Toggle selection when the same player is clicked again
      this.setSelectedPlayer(best.name === this.selectedPlayer ? null : best.name);
    } else {
      this.setSelectedPlayer(null);
    }
  }

  _firstTick() {
    return Number(this.frames[0]?.tick) || 0;
  }

  _lastTick() {
    return Number(this.frames[this.frames.length - 1]?.tick) || this._firstTick();
  }

  _frameIndexForTick(tick) {
    if (!this.frames.length) return 0;
    let lo = 0;
    let hi = this.frames.length - 1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      const mt = Number(this.frames[mid]?.tick) || 0;
      if (mt <= tick) lo = mid + 1;
      else hi = mid - 1;
    }
    return Math.max(0, Math.min(this.frames.length - 1, hi));
  }

  _setRenderTick(tick) {
    const nextTick = Math.max(this._firstTick(), Math.min(this._lastTick(), Number(tick) || this._firstTick()));
    const backwards = nextTick < this._renderTick;
    this._renderTick = nextTick;
    this.currentFrame = this._frameIndexForTick(nextTick);
    if (backwards) this._resetEventState();
    this._updateEventsForTick(nextTick);
    return backwards;
  }

  _resetEventState() {
    this._trails = {};
    this._activeKills = [];
    this._activeGrenades = [];
    this._activeBombEvents = [];
    this._activeShots = [];
    this.kills.forEach(k    => { k._activated = false; });
    this.bombs.forEach(b    => { b._activated = false; });
    this.grenades.forEach(g => { g._activated = false; });
    this.shots.forEach(s    => { s._activated = false; });
  }

  _angleLerp(a, b, t) {
    if (a == null || b == null || !Number.isFinite(Number(a)) || !Number.isFinite(Number(b))) {
      return a ?? b ?? null;
    }
    const start = Number(a);
    const delta = ((((Number(b) - start) % 360) + 540) % 360) - 180;
    return start + delta * t;
  }

  _interpolatedFrame(tick = this._renderTick) {
    if (!this.frames.length) return null;
    const idx = this._frameIndexForTick(tick);
    const f0 = this.frames[idx] || this.frames[0];
    const f1 = this.frames[Math.min(idx + 1, this.frames.length - 1)] || f0;
    const t0 = Number(f0.tick) || tick;
    const t1 = Number(f1.tick) || t0;
    const alpha = t1 > t0 ? Math.max(0, Math.min(1, (tick - t0) / (t1 - t0))) : 0;
    const nextByName = new Map((f1.players || []).map(p => [p.name, p]));
    const players = [];

    for (const p0 of f0.players || []) {
      const p1 = nextByName.get(p0.name);
      if (!p1) {
        players.push({ ...p0 });
        continue;
      }

      const x0 = Number(p0.x);
      const y0 = Number(p0.y);
      const x1 = Number(p1.x);
      const y1 = Number(p1.y);
      const canMove = [x0, y0, x1, y1].every(Number.isFinite);
      const stateSource = alpha >= 0.995 ? p1 : p0;
      players.push({
        ...stateSource,
        name: p0.name,
        side: p1.side || p0.side,
        x: canMove ? x0 + (x1 - x0) * alpha : p0.x,
        y: canMove ? y0 + (y1 - y0) * alpha : p0.y,
        yaw: this._angleLerp(p0.yaw, p1.yaw, alpha),
      });
    }

    return { tick, players };
  }

  // ── Playback controls ────────────────────────────────────────────────────────
  play() {
    if (this.playing) return;
    if (this._renderTick >= this._lastTick()) this.seekTo(0);
    this.playing  = true;
    this._lastTime = performance.now();
    this._timeRemainderMs = 0;
    this._raf = requestAnimationFrame(this._loop.bind(this));
    if (this.onPlayStateChange) this.onPlayStateChange(true);
  }

  pause() {
    if (!this.playing) return;
    this.playing = false;
    cancelAnimationFrame(this._raf);
    this._raf = null;
    if (this.onPlayStateChange) this.onPlayStateChange(false);
  }

  toggle() { this.playing ? this.pause() : this.play(); }

  seekTo(frameIndex) {
    const idx = Math.max(0, Math.min(this.frames.length - 1, frameIndex));
    const tick = Number(this.frames[idx]?.tick) || this._firstTick();
    this._timeRemainderMs = 0;
    this._setRenderTick(tick);
    this._draw();
    if (this.onFrameChange) this.onFrameChange(this.currentFrame, this.frames.length);
  }

  /** Seek directly to a demo tick (used by scrubber event markers). */
  seekToTick(tick) {
    if (!this.frames.length) return;
    this._timeRemainderMs = 0;
    this._setRenderTick(tick);
    this._draw();
    if (this.onFrameChange) this.onFrameChange(this.currentFrame, this.frames.length);
  }

  nextFrame() { this.seekTo(this.currentFrame + 1); }
  prevFrame() { this.seekTo(this.currentFrame - 1); }

  setSpeed(v)  { this.speed = v; }
  setTrails(v) { this.showTrails = v; if (!v) this._trails = {}; this._draw(); }
  setLabels(v) { this.showLabels = v; this._draw(); }
  setTracers(v) { this.showTracers = v; if (!v) this._activeShots = []; this._draw(); }
  setEffects(v) { this.showEffects = v; this._draw(); }
  setZones(v) { this.showZones = v; this._draw(); }

  stop() { this.pause(); this.seekTo(0); }

  _buildZoneSamples() {
    const samples = [];
    for (let i = 0; i < this.frames.length; i += ZONE_SAMPLE_STRIDE) {
      const frame = this.frames[i];
      for (const p of frame?.players || []) {
        if ((Number(p.hp) || 0) <= 0) continue;
        const side = String(p.side || '').toUpperCase() === 'CT' ? 'CT' : 'T';
        samples.push({ x: Number(p.x), y: Number(p.y), side });
      }
    }
    return samples;
  }

  _buildDamagePulses() {
    const pulses = [];
    const lastHp = new Map();
    for (const frame of this.frames) {
      const tick = Number(frame?.tick);
      if (!Number.isFinite(tick)) continue;
      for (const p of frame.players || []) {
        const name = p.name;
        const hp = Math.max(0, Math.min(100, Number(p.hp)));
        if (!name || !Number.isFinite(hp)) continue;
        const prev = lastHp.get(name);
        if (prev != null && hp < prev - DAMAGE_MIN_DELTA) {
          pulses.push({ player: name, tick, amount: prev - hp });
        }
        lastHp.set(name, hp);
      }
    }
    return pulses;
  }

  _damagePulse(playerName, currentTick) {
    let best = null;
    let bestStrength = 0;
    for (const pulse of this._damagePulses) {
      if (pulse.player !== playerName) continue;
      const age = currentTick - pulse.tick;
      if (age < 0 || age > DAMAGE_FLASH_TICKS) continue;
      const fade = 1 - age / DAMAGE_FLASH_TICKS;
      const amountBoost = Math.min(1, 0.35 + pulse.amount / 45);
      const strength = fade * amountBoost;
      if (strength > bestStrength) {
        best = pulse;
        bestStrength = strength;
      }
    }
    return best ? { ...best, strength: bestStrength } : null;
  }

  // ── Animation loop ───────────────────────────────────────────────────────────
  _loop(now) {
    if (!this.playing) return;
    const elapsed = Math.min(250, Math.max(0, now - this._lastTime));
    this._lastTime = now;
    const previousFrame = this.currentFrame;
    const nextTick = this._renderTick + (elapsed / 1000) * TICK_RATE * this.speed;

    if (nextTick >= this._lastTick()) {
      this._setRenderTick(this._lastTick());
      this._draw();
      if (this.onFrameChange) this.onFrameChange(this.currentFrame, this.frames.length);
      this.pause();
      return;
    }

    this._setRenderTick(nextTick);
    this._draw();
    if (this.currentFrame !== previousFrame && this.onFrameChange) {
      this.onFrameChange(this.currentFrame, this.frames.length);
    }
    this._raf = requestAnimationFrame(this._loop.bind(this));
  }

  // ── Event state update ───────────────────────────────────────────────────────
  _updateEventsForTick(currentTick = this._renderTick) {

    // Kills
    for (const k of this.kills) {
      if (!k._activated && k.tick <= currentTick) {
        k._activated = true;
        if (k.victim_x != null && k.victim_y != null) {
          const pos = worldToCanvas(k.victim_x, k.victim_y, this.mapInfo);
          if (pos) this._activeKills.push({ ...k, ...pos });
        }
      }
    }
    this._activeKills = this._activeKills.filter(k => (currentTick - (k.tick || 0)) <= KILL_FLASH_TICKS);

    // Bomb events (icons for plant/defuse/explode; pickup/drop only drive carrier state)
    for (const b of this.bombs) {
      if (!b._activated && b.tick <= currentTick) {
        b._activated = true;
        const evt = (b.event || '').toLowerCase();
        if ((evt.includes('plant') || evt.includes('defus') || evt.includes('explode')) &&
            b.x != null && b.y != null) {
          const pos = worldToCanvas(b.x, b.y, this.mapInfo);
          if (pos) this._activeBombEvents.push({ ...b, ...pos });
        }
      }
    }
    this._activeBombEvents = this._activeBombEvents.filter(b => {
      const evt = (b.event || '').toLowerCase();
      const age = currentTick - (b.tick || 0);
      if (evt.includes('explode')) return age <= EXPLOSION_TICKS;
      if (evt === 'plant') return true; // planted icon persists until round end / defuse / explode
      return age <= BOMB_EVENT_FLASH_TICKS;
    });

    // Bomb carrier + planted state derived from the full ordered event list
    this._bombCarrier = null;
    this._plantedBomb = null;
    for (const b of this.bombs) {
      if ((b.tick || 0) > currentTick) break;
      const evt = (b.event || '').toLowerCase();
      if (evt === 'pickup') {
        this._bombCarrier = b.player || null;
      } else if (evt === 'drop') {
        this._bombCarrier = null;
      } else if (evt.includes('plant') && !evt.includes('start')) {
        this._bombCarrier = null;
        if (b.x != null && b.y != null) this._plantedBomb = { x: b.x, y: b.y, tick: b.tick };
      } else if (evt.includes('defus') || evt.includes('explode')) {
        this._plantedBomb = null;
      }
    }

    // Grenades
    for (const g of this.grenades) {
      if (!g._activated && g.tick <= currentTick) {
        g._activated = true;
        const gType = (g.type || '').toLowerCase();
        const flightTicks = Number.isFinite(g.flight_ticks) ? g.flight_ticks : (GRENADE_FLIGHT_TICKS[gType] || GRENADE_FLIGHT_TICKS.unknown);
        const detTick = Number.isFinite(g.detonate_tick) ? g.detonate_tick : (g.tick + flightTicks);
        this._activeGrenades.push({
          ...g,
          type: gType || 'unknown',
          flight_ticks: flightTicks,
          detonate_tick: detTick,
        });
      }
    }
    this._activeGrenades = this._activeGrenades.filter(g => currentTick <= ((g.detonate_tick || g.tick) + GRENADE_IMPACT_TICKS));

    // Bullet tracers
    for (const s of this.shots) {
      if (!s._activated && s.tick <= currentTick) {
        s._activated = true;
        if (s.x != null && s.y != null && s.yaw != null && isBulletWeapon(s.weapon)) {
          const pos = worldToCanvas(s.x, s.y, this.mapInfo);
          if (pos) this._activeShots.push({ ...s, ...pos });
        }
      }
    }
    this._activeShots = this._activeShots.filter(s => (currentTick - (s.tick || 0)) <= TRACER_TICKS);
  }

  _grenadePositionAtTick(g, currentTick) {
    const throwTick = g.tick || 0;
    const flightTicks = Math.max(1, g.flight_ticks || GRENADE_FLIGHT_TICKS[g.type] || GRENADE_FLIGHT_TICKS.unknown);
    const detTick = g.detonate_tick || (throwTick + flightTicks);

    const rawPath = Array.isArray(g.path) ? g.path : [];
    const path = [];
    for (const pt of rawPath) {
      if (!Array.isArray(pt) || pt.length < 2) continue;
      const x = Number(pt[0]);
      const y = Number(pt[1]);
      if (Number.isFinite(x) && Number.isFinite(y)) path.push([x, y]);
    }

    const sx = Number.isFinite(Number(g.start_x)) ? Number(g.start_x) : Number(g.x);
    const sy = Number.isFinite(Number(g.start_y)) ? Number(g.start_y) : Number(g.y);
    const ex = Number.isFinite(Number(g.end_x)) ? Number(g.end_x) : Number(g.x);
    const ey = Number.isFinite(Number(g.end_y)) ? Number(g.end_y) : Number(g.y);

    if (!path.length) {
      if (Number.isFinite(sx) && Number.isFinite(sy)) path.push([sx, sy]);
      if (Number.isFinite(ex) && Number.isFinite(ey)) path.push([ex, ey]);
    }
    if (path.length === 1) path.push(path[0]);
    if (!path.length) return null;

    if (currentTick >= detTick) {
      return { wx: path[path.length - 1][0], wy: path[path.length - 1][1], phase: 'impact', path, progress: 1 };
    }

    const elapsed = Math.max(0, currentTick - throwTick);
    const progress = Math.max(0, Math.min(1, elapsed / flightTicks));
    const fidx = progress * (path.length - 1);
    const i0 = Math.floor(fidx);
    const i1 = Math.min(path.length - 1, i0 + 1);
    const t = fidx - i0;
    const wx = path[i0][0] + (path[i1][0] - path[i0][0]) * t;
    const wy = path[i0][1] + (path[i1][1] - path[i0][1]) * t;
    return { wx, wy, phase: 'flight', path, progress };
  }

  /** Remaining blind duration in ticks for a player at the given tick (0 = not blind). */
  _blindRemaining(playerName, currentTick) {
    return this._blindInfo(playerName, currentTick)?.remaining || 0;
  }

  _blindInfo(playerName, currentTick) {
    let best = null;
    for (const b of this._blindIntervals) {
      if (b.player !== playerName) continue;
      if (currentTick >= b.start && currentTick <= b.end) {
        const duration = Math.max(1, b.end - b.start);
        const remaining = Math.max(0, b.end - currentTick);
        const ratio = Math.max(0, Math.min(1, remaining / duration));
        const strength = b.strength ?? Math.min(1, duration / (2.4 * TICK_RATE));
        const candidate = { remaining, duration, ratio, strength, start: b.start, end: b.end };
        if (!best || candidate.remaining > best.remaining) best = candidate;
      }
    }
    return best;
  }

  // ── Main draw ────────────────────────────────────────────────────────────────
  _draw() {
    const ctx  = this.ctx;
    const size = CANVAS_SIZE;

    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, size, size);
    ctx.fillStyle = '#22272e';
    ctx.fillRect(0, 0, size, size);

    // Radar image. Draw from the original radar crop so zoom does not simply
    // upscale a previously downsampled 600px canvas.
    const radarSource = this.radarSource || this.radarImg;
    if (radarSource) {
      const viewSize = size / this.zoom;
      const sourceW = radarSource.width || RADAR_SIZE;
      const sourceH = radarSource.height || RADAR_SIZE;
      const sx = (this.panX / size) * sourceW;
      const sy = (this.panY / size) * sourceH;
      const sw = (viewSize / size) * sourceW;
      const sh = (viewSize / size) * sourceH;
      ctx.save();
      ctx.imageSmoothingEnabled = true;
      ctx.imageSmoothingQuality = 'high';
      ctx.drawImage(radarSource, sx, sy, sw, sh, 0, 0, size, size);
      ctx.restore();
      ctx.globalAlpha = 1;
    }

    const frame = this._interpolatedFrame(this._renderTick);
    if (!frame) return;
    const currentTick = frame.tick ?? 0;

    const players = frame.players || [];

    // Compute canvas positions and update trails
    for (const p of players) {
      const pos = worldToCanvas(p.x, p.y, this.mapInfo);
      p._pos = pos; // may be null if outside bounds
      if (pos && this.showTrails) {
        if (!this._trails[p.name]) this._trails[p.name] = [];
        const trail = this._trails[p.name];
        // Avoid duplicate points
        const last = trail[trail.length - 1];
        if (!last || Math.abs(last.cx - pos.cx) > 1 || Math.abs(last.cy - pos.cy) > 1) {
          trail.push(pos);
          if (trail.length > 50) trail.shift();
        }
      }
    }
    this._lastDrawnPlayers = players;

    ctx.save();
    ctx.setTransform(this.zoom, 0, 0, this.zoom, -this.panX * this.zoom, -this.panY * this.zoom);

    if (this.showZones) {
      this._drawPositionZones(ctx, players);
    }

    // Persistent area effects (smoke clouds, fire zones) — under everything
    if (this.showEffects) {
      for (const ef of this.effects) {
        if (ef.type === 'smoke')   this._drawSmokeArea(ctx, ef, currentTick);
        if (ef.type === 'molotov') this._drawMolotovArea(ctx, ef, currentTick);
      }
    }

    // Draw trails
    if (this.showTrails) {
      for (const p of players) {
        if (!p._pos) continue;
        const trail = this._trails[p.name];
        if (!trail || trail.length < 2) continue;
        const color = p.side === 'CT' ? PLAYER_STYLE.CT.fill : PLAYER_STYLE.T.fill;
        const invZoom = 1 / Math.max(this.zoom, 1);
        ctx.save();
        ctx.lineWidth = 1.15 * invZoom;
        for (let i = 1; i < trail.length; i++) {
          ctx.globalAlpha = (i / trail.length) * 0.36;
          ctx.strokeStyle = color;
          ctx.beginPath();
          ctx.moveTo(trail[i-1].cx, trail[i-1].cy);
          ctx.lineTo(trail[i].cx, trail[i].cy);
          ctx.stroke();
        }
        ctx.restore();
      }
    }

    // Draw grenade icons (in flight / fresh impact)
    if (this.showEffects) {
      for (const g of this._activeGrenades) {
        this._drawGrenade(ctx, g, currentTick);
      }
    }

    // Bullet tracers
    if (this.showTracers) {
      for (const s of this._activeShots) {
        this._drawTracer(ctx, s, currentTick);
      }
    }

    // Detonation rings (flash blind radius, HE blast)
    if (this.showEffects) {
      for (const ef of this.effects) {
        if (ef.type === 'flash') this._drawFlashRing(ctx, ef, currentTick);
        if (ef.type === 'he')    this._drawHeRing(ctx, ef, currentTick);
      }
    }

    // Draw kill markers
    for (const k of this._activeKills) {
      this._drawKillMarker(ctx, k, currentTick);
    }

    // Draw bomb events (planted icon, defuse icon, explosion effect)
    if (this.showEffects) {
      for (const b of this._activeBombEvents) {
        const evt = (b.event || '').toLowerCase();
        const ageTicks = currentTick - (b.tick || currentTick);
        if (evt.includes('explode')) {
          this._drawExplosion(ctx, b, ageTicks);
        } else if (evt === 'plant' && !this._plantedBomb) {
          // Bomb no longer planted (defused or exploded) — hide the planted icon
          continue;
        } else {
          this._drawBombIcon(ctx, b, ageTicks);
        }
      }
    }

    // Focused player: long aim line goes under the player markers
    const selected = this.selectedPlayer
      ? players.find(p => p.name === this.selectedPlayer && p._pos && p.hp > 0)
      : null;
    if (selected) this._drawAimLine(ctx, selected);

    // Draw players (dead first, alive on top)
    const dead  = players.filter(p => p.hp <= 0  && p._pos);
    const alive = players.filter(p => p.hp > 0   && p._pos);
    dead.forEach(p  => this._drawDeadPlayer(ctx, p));
    alive.forEach(p => this._drawAlivePlayer(ctx, p, currentTick));

    // Focused player: highlight + info card on top of everything
    if (selected) {
      this._drawSelectionHighlight(ctx, selected);
      this._drawInfoCard(ctx, selected);
    }

    ctx.restore();
  }

  // ── Draw helpers ─────────────────────────────────────────────────────────────

  _drawPositionZones(ctx, players) {
    if (!this._zoneSamples.length) return;
    const selected = this.selectedPlayer
      ? players.find(p => p.name === this.selectedPlayer)
      : null;
    const selectedSide = selected?.side === 'CT' ? 'CT' : selected ? 'T' : null;
    const radius = worldUnitsToPx(ZONE_RADIUS_UNITS, this.mapInfo);

    ctx.save();
    ctx.globalCompositeOperation = 'screen';
    for (const sample of this._zoneSamples) {
      if (selectedSide && sample.side === selectedSide) continue;
      const pos = worldToCanvas(sample.x, sample.y, this.mapInfo);
      if (!pos) continue;
      const color = sample.side === 'CT' ? '96,165,250' : '251,191,36';
      const grad = ctx.createRadialGradient(pos.cx, pos.cy, 0, pos.cx, pos.cy, radius);
      grad.addColorStop(0, `rgba(${color},0.075)`);
      grad.addColorStop(0.55, `rgba(${color},0.034)`);
      grad.addColorStop(1, `rgba(${color},0)`);
      ctx.fillStyle = grad;
      ctx.beginPath();
      ctx.arc(pos.cx, pos.cy, radius, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.restore();
  }

  _drawBlindPie(ctx, info, radius) {
    if (!info || info.ratio <= 0) return;
    const pieR = radius + 24 + Math.min(16, info.duration / TICK_RATE * 2);
    const alpha = 0.16 + 0.34 * info.strength;
    const start = -Math.PI / 2;
    const end = start + Math.PI * 2 * info.ratio;

    ctx.save();
    ctx.globalCompositeOperation = 'screen';
    ctx.fillStyle = `rgba(241,245,249,${alpha})`;
    ctx.beginPath();
    ctx.moveTo(0, 0);
    ctx.arc(0, 0, Math.min(BLIND_PIE_MAX_RADIUS, pieR), start, end, false);
    ctx.closePath();
    ctx.fill();

    ctx.strokeStyle = `rgba(255,255,255,${0.46 + 0.28 * info.strength})`;
    ctx.lineWidth = 2.2;
    ctx.lineCap = 'round';
    ctx.shadowColor = 'rgba(255,255,255,0.75)';
    ctx.shadowBlur = 10;
    ctx.beginPath();
    ctx.arc(0, 0, Math.min(BLIND_PIE_MAX_RADIUS, pieR), start, end, false);
    ctx.stroke();

    ctx.shadowBlur = 0;
    ctx.strokeStyle = 'rgba(148,163,184,0.28)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.arc(0, 0, Math.min(BLIND_PIE_MAX_RADIUS, pieR), 0, Math.PI * 2);
    ctx.stroke();
    ctx.restore();
  }

  _drawDamagePulse(ctx, pulse, radius) {
    if (!pulse) return;
    const strength = Math.max(0, Math.min(1, pulse.strength));
    ctx.save();
    ctx.globalCompositeOperation = 'screen';
    ctx.shadowColor = 'rgba(248,113,113,0.95)';
    ctx.shadowBlur = 16 * strength;
    ctx.strokeStyle = `rgba(248,113,113,${0.22 + 0.58 * strength})`;
    ctx.lineWidth = 2.2;
    ctx.beginPath();
    ctx.arc(0, 0, radius + 5 + 10 * (1 - strength), 0, Math.PI * 2);
    ctx.stroke();

    ctx.fillStyle = `rgba(239,68,68,${0.12 + 0.33 * strength})`;
    ctx.beginPath();
    ctx.arc(0, 0, radius + 1.5, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  }

  _drawAlivePlayer(ctx, p, currentTick) {
    const { cx, cy } = p._pos;
    const isT  = p.side !== 'CT';
    const style = PLAYER_STYLE[isT ? 'T' : 'CT'];
    const r = PLAYER_MARKER_R;
    const invZoom = 1 / Math.max(this.zoom, 1);
    const blindInfo = currentTick != null ? this._blindInfo(p.name, currentTick) : null;
    const damagePulse = currentTick != null ? this._damagePulse(p.name, currentTick) : null;

    ctx.save();
    ctx.translate(cx, cy);
    ctx.scale(invZoom, invZoom);

    this._drawBlindPie(ctx, blindInfo, r);

    // Soft direction wedge, kept screen-sized so zoomed views stay readable.
    if (p.yaw != null) {
      const { dx, dy } = yawToDir(p.yaw);
      ctx.fillStyle = style.cone;
      ctx.beginPath();
      ctx.moveTo(dx * (r + 13), dy * (r + 13));
      ctx.lineTo(-dy * 5, dx * 5);
      ctx.lineTo(dy * 5, -dx * 5);
      ctx.closePath();
      ctx.fill();
    }

    // Compact Scope-like body: dark rim, soft team fill, no glossy white dot.
    ctx.shadowColor = style.glow;
    ctx.shadowBlur  = 7;
    ctx.fillStyle = style.rim;
    ctx.beginPath();
    ctx.arc(0, 0, r + 1.7, 0, Math.PI * 2);
    ctx.fill();

    ctx.shadowBlur = 4;
    ctx.fillStyle = style.fill;
    ctx.beginPath();
    ctx.arc(0, 0, r, 0, Math.PI * 2);
    ctx.fill();

    ctx.shadowBlur = 0;
    ctx.strokeStyle = style.stroke;
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    ctx.arc(0, 0, r - 0.4, 0, Math.PI * 2);
    ctx.stroke();

    this._drawDamagePulse(ctx, damagePulse, r);

    // Blind overlay: white flash on the marker while the player is blinded
    if (blindInfo) {
      const strength = Math.max(0.12, Math.min(1, blindInfo.strength * blindInfo.ratio));
      ctx.globalAlpha = 0.22 + 0.36 * strength;
      ctx.fillStyle = '#ffffff';
      ctx.beginPath();
      ctx.arc(0, 0, r - 1, 0, Math.PI * 2);
      ctx.fill();
      ctx.globalAlpha = 1;
    }

    // Yaw direction arrow (canvas Y is flipped vs world yaw)
    if (p.yaw != null) {
      const { dx, dy } = yawToDir(p.yaw);
      ctx.strokeStyle = style.aim;
      ctx.lineWidth   = 1.45;
      ctx.lineCap     = 'round';
      ctx.beginPath();
      ctx.moveTo(dx * (r - 0.5), dy * (r - 0.5));
      ctx.lineTo(dx * (r + 9), dy * (r + 9));
      ctx.stroke();
    }

    // HP bar
    const hp   = Math.max(0, Math.min(100, p.hp));
    const barW = 22;
    const barH = 3;
    const barX = -barW / 2;
    const barY = r + 4;
    ctx.fillStyle = 'rgba(7,11,20,0.70)';
    ctx.fillRect(barX - 1, barY - 1, barW + 2, barH + 2);
    ctx.fillStyle = hp > 60 ? '#22c55e' : hp > 30 ? '#eab308' : '#ef4444';
    ctx.fillRect(barX, barY, barW * (hp / 100), barH);

    // Armor bar (thin gray-blue bar right under the HP bar)
    const armor = Math.max(0, Math.min(100, Number(p.armor) || 0));
    if (armor > 0) {
      const aY = barY + barH + 2;
      ctx.fillStyle = 'rgba(7,11,20,0.66)';
      ctx.fillRect(barX - 1, aY - 1, barW + 2, 1.8 + 2);
      ctx.fillStyle = '#91b4df';
      ctx.fillRect(barX, aY, barW * (armor / 100), 1.8);
    }

    // Weapon icon to the upper-right of the marker. Real assets are preferred;
    // fallback keeps unknown weapons visible without requiring a full icon set.
    const wKey = weaponAssetKey(p.weapon, p.side);
    if (!this._drawWeaponAsset(ctx, wKey, r + 12, -3, style.aim)) {
      const wCls = weaponClass(p.weapon);
      if (wCls) this._drawWeaponGlyph(ctx, wCls, r + 8, 0, style.aim);
    }

    // Defuse kit badge (CT only)
    if (p.side === 'CT' && p.defuser) {
      ctx.fillStyle = '#22c55e';
      ctx.strokeStyle = 'rgba(255,255,255,0.55)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.arc(-r - 3, -r - 2, 3.6, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
      ctx.fillStyle = '#06230f';
      ctx.font = 'bold 5.5px Inter, sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText('D', -r - 3, -r - 1.8);
    }

    // Bomb carrier badge (T holding the C4)
    if (this._bombCarrier && p.name === this._bombCarrier) {
      ctx.fillStyle = '#ef4444';
      ctx.strokeStyle = 'rgba(255,255,255,0.68)';
      ctx.lineWidth = 1;
      const bx = -r - 4, by = r + 2;
      ctx.beginPath();
      ctx.roundRect(bx - 5.2, by - 3.3, 11.2, 7.2, 2);
      ctx.fill();
      ctx.stroke();
      ctx.fillStyle = '#fff';
      ctx.font = 'bold 5.4px Inter, sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText('C4', bx + 0.4, by + 0.5);
    }

    ctx.restore();

    // Compact player name label.
    if (this.showLabels) {
      const shortName = p.name.length > 10 ? p.name.substring(0, 10) + '…' : p.name;
      ctx.save();
      ctx.translate(cx, cy);
      ctx.scale(invZoom, invZoom);
      ctx.font        = 'bold 10px Inter, sans-serif';
      ctx.textAlign   = 'center';
      ctx.textBaseline = 'middle';
      const textW = ctx.measureText(shortName).width;
      const w = textW + 8;
      const h = 13;
      const y = -r - 15;
      ctx.fillStyle = 'rgba(20,27,38,0.78)';
      ctx.strokeStyle = 'rgba(255,255,255,0.08)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.roundRect(-w / 2, y - h / 2, w, h, 3);
      ctx.fill();
      ctx.stroke();
      ctx.fillStyle = style.label;
      ctx.fillText(shortName, 0, y + 0.3);
      ctx.restore();
    }
  }

  _drawWeaponAsset(ctx, key, gx, gy, color = 'rgba(216,226,241,0.88)') {
    const img = key ? this._weaponAssets?.[key] : null;
    if (!img) return false;

    const iw = img.naturalWidth || img.width || 0;
    const ih = img.naturalHeight || img.height || 0;
    if (!iw || !ih) return false;

    const maxW = key === 'awp' || key === 'ssg' ? 122 : key === 'deagle' || key === 'usp' ? 80 : 106;
    const maxH = key === 'deagle' || key === 'usp' ? 44 : 48;
    const scale = Math.min(maxW / iw, maxH / ih);
    const w = iw * scale;
    const h = ih * scale;

    ctx.save();
    ctx.translate(gx, gy);
    ctx.globalAlpha = 0.96;
    ctx.shadowColor = 'rgba(0,0,0,0.78)';
    ctx.shadowBlur = 3.2;
    ctx.drawImage(img, -w * 0.16, -h / 2, w, h);
    ctx.restore();
    return true;
  }

  /** Tiny weapon-class silhouette drawn near the player marker. */
  _drawWeaponGlyph(ctx, cls, gx, gy, color = 'rgba(216,226,241,0.88)') {
    ctx.save();
    ctx.translate(gx, gy);
    ctx.strokeStyle = color;
    ctx.fillStyle   = color;
    ctx.lineWidth   = 1.15;
    ctx.lineCap     = 'round';
    ctx.shadowColor = 'rgba(0,0,0,0.8)';
    ctx.shadowBlur  = 2;

    ctx.beginPath();
    switch (cls) {
      case 'knife':
        // Short blade
        ctx.moveTo(-3, 3); ctx.lineTo(3, -3);
        ctx.moveTo(-3, 1); ctx.lineTo(-1, 3);
        ctx.stroke();
        break;
      case 'sniper':
        // Long barrel + scope dot
        ctx.moveTo(-6, 1); ctx.lineTo(6, 1);
        ctx.moveTo(-4, 1); ctx.lineTo(-4, 3.5);
        ctx.stroke();
        ctx.beginPath(); ctx.arc(1, -1.5, 1.4, 0, Math.PI * 2); ctx.stroke();
        break;
      case 'rifle':
        // Medium barrel + grip + magazine
        ctx.moveTo(-5, 0); ctx.lineTo(5, 0);
        ctx.moveTo(-3, 0); ctx.lineTo(-3, 3);
        ctx.moveTo(1, 0);  ctx.lineTo(1.8, 3);
        ctx.stroke();
        break;
      case 'smg':
        // Compact body + grip
        ctx.moveTo(-3.5, 0); ctx.lineTo(3.5, 0);
        ctx.moveTo(0, 0); ctx.lineTo(0, 3);
        ctx.stroke();
        break;
      case 'shotgun':
        // Thick short barrel
        ctx.lineWidth = 1.7;
        ctx.moveTo(-4, 0.5); ctx.lineTo(4, 0.5);
        ctx.stroke();
        ctx.lineWidth = 1.15;
        ctx.beginPath(); ctx.moveTo(-2, 0.5); ctx.lineTo(-2, 3); ctx.stroke();
        break;
      case 'mg':
        // Barrel + bipod
        ctx.moveTo(-5, 0); ctx.lineTo(5, 0);
        ctx.moveTo(3, 0); ctx.lineTo(1.5, 3);
        ctx.moveTo(3, 0); ctx.lineTo(4.5, 3);
        ctx.stroke();
        break;
      case 'grenade':
        ctx.arc(0, 0.5, 2.4, 0, Math.PI * 2);
        ctx.stroke();
        ctx.beginPath(); ctx.moveTo(1, -1.5); ctx.lineTo(2.2, -3); ctx.stroke();
        break;
      case 'c4':
        ctx.rect(-3, -2, 6, 4.5);
        ctx.stroke();
        ctx.beginPath(); ctx.arc(0, 0.2, 0.9, 0, Math.PI * 2); ctx.fill();
        break;
      case 'zeus':
        // Lightning zig-zag
        ctx.moveTo(-2, -3); ctx.lineTo(1, -0.5); ctx.lineTo(-1, 0.5); ctx.lineTo(2, 3);
        ctx.stroke();
        break;
      default: // pistol
        ctx.moveTo(-2.5, 0); ctx.lineTo(2.5, 0);
        ctx.moveTo(0.5, 0); ctx.lineTo(0, 2.8);
        ctx.stroke();
    }
    ctx.restore();
  }

  _drawDeadPlayer(ctx, p) {
    const { cx, cy } = p._pos;
    const isT  = p.side !== 'CT';
    const style = PLAYER_STYLE[isT ? 'T' : 'CT'];
    const invZoom = 1 / Math.max(this.zoom, 1);
    const s = 5;
    ctx.save();
    ctx.translate(cx, cy);
    ctx.scale(invZoom, invZoom);
    ctx.globalAlpha = 0.34;
    ctx.strokeStyle = style.fill;
    ctx.lineWidth   = 1.8;
    ctx.beginPath(); ctx.moveTo(-s, -s); ctx.lineTo(s, s); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(s, -s); ctx.lineTo(-s, s); ctx.stroke();
    ctx.restore();
  }

  _drawKillMarker(ctx, k, currentTick) {
    const ageTicks = currentTick - (k.tick || currentTick);
    const alpha = Math.max(0, 1 - ageTicks / KILL_FLASH_TICKS);
    const color = k.victim_side === 'CT' ? PLAYER_STYLE.CT.fill : PLAYER_STYLE.T.fill;
    const invZoom = 1 / Math.max(this.zoom, 1);
    const s = 6;
    ctx.save();
    ctx.translate(k.cx, k.cy);
    ctx.scale(invZoom, invZoom);
    ctx.globalAlpha = alpha * 0.9;
    ctx.strokeStyle = color;
    ctx.lineWidth   = 2;
    ctx.lineCap     = 'round';
    ctx.shadowColor = color;
    ctx.shadowBlur  = 8;
    ctx.beginPath(); ctx.moveTo(-s, -s); ctx.lineTo(s, s); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(s, -s);  ctx.lineTo(-s, s); ctx.stroke();
    if (k.headshot) {
      ctx.strokeStyle = '#f97316';
      ctx.lineWidth   = 1.5;
      ctx.shadowColor = '#f97316';
      ctx.beginPath();
      ctx.arc(0, 0, s + 3, 0, Math.PI * 2);
      ctx.stroke();
    }
    ctx.restore();
  }

  // ── Bullet tracer ────────────────────────────────────────────────────────────
  _drawTracer(ctx, s, currentTick) {
    const age = currentTick - (s.tick || currentTick);
    const alpha = Math.max(0, 1 - age / TRACER_TICKS);
    if (alpha <= 0) return;
    const { dx, dy } = yawToDir(s.yaw);
    const isT = (s.side || '').toUpperCase() !== 'CT';
    const color = isT ? 'rgba(253,224,71,' : 'rgba(147,197,253,';

    const x0 = s.cx, y0 = s.cy;
    const x1 = s.cx + dx * TRACER_LENGTH_PX;
    const y1 = s.cy + dy * TRACER_LENGTH_PX;

    ctx.save();
    const grad = ctx.createLinearGradient(x0, y0, x1, y1);
    grad.addColorStop(0, color + (0.85 * alpha) + ')');
    grad.addColorStop(0.35, color + (0.45 * alpha) + ')');
    grad.addColorStop(1, color + '0)');
    ctx.strokeStyle = grad;
    ctx.lineWidth = 2.2;
    ctx.lineCap = 'round';
    ctx.shadowColor = color + (0.6 * alpha) + ')';
    ctx.shadowBlur = 6;
    ctx.beginPath();
    ctx.moveTo(x0, y0);
    ctx.lineTo(x1, y1);
    ctx.stroke();
    // Muzzle flash dot
    ctx.fillStyle = color + (0.9 * alpha) + ')';
    ctx.beginPath();
    ctx.arc(x0, y0, 1.8, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  }

  _drawGrenadeTrail(ctx, g, state, cfg) {
    const path = Array.isArray(state.path) ? state.path : [];
    if (path.length < 2) return;
    const invZoom = 1 / Math.max(this.zoom, 1);
    const points = path
      .map(([wx, wy]) => worldToCanvas(wx, wy, this.mapInfo))
      .filter(Boolean);
    if (points.length < 2) return;

    const current = worldToCanvas(state.wx, state.wy, this.mapInfo);
    if (!current) return;

    const progress = Math.max(0, Math.min(1, Number(state.progress) || 0));
    const visibleCount = Math.max(1, Math.floor(progress * (points.length - 1)));
    const traveled = points.slice(0, visibleCount + 1);
    traveled[traveled.length - 1] = current;

    ctx.save();
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';

    ctx.globalAlpha = 0.28;
    ctx.strokeStyle = cfg.color;
    ctx.lineWidth = 1.05 * invZoom;
    ctx.setLineDash([5 * invZoom, 5 * invZoom]);
    ctx.beginPath();
    ctx.moveTo(points[0].cx, points[0].cy);
    for (const pt of points.slice(1)) ctx.lineTo(pt.cx, pt.cy);
    ctx.stroke();

    ctx.globalAlpha = state.phase === 'flight' ? 0.95 : 0.38;
    ctx.setLineDash([]);
    ctx.strokeStyle = cfg.color;
    ctx.lineWidth = 1.9 * invZoom;
    ctx.shadowColor = cfg.glow;
    ctx.shadowBlur = 10;
    ctx.beginPath();
    ctx.moveTo(traveled[0].cx, traveled[0].cy);
    for (const pt of traveled.slice(1)) ctx.lineTo(pt.cx, pt.cy);
    ctx.stroke();

    ctx.shadowBlur = 0;
    ctx.globalAlpha = state.phase === 'flight' ? 0.9 : 0.45;
    ctx.fillStyle = cfg.color;
    ctx.beginPath();
    ctx.arc(points[0].cx, points[0].cy, 2.2 * invZoom, 0, Math.PI * 2);
    ctx.fill();
    ctx.beginPath();
    ctx.arc(points[points.length - 1].cx, points[points.length - 1].cy, 2.6 * invZoom, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  }

  // ── Persistent area effects ──────────────────────────────────────────────────
  _drawSmokeArea(ctx, ef, currentTick) {
    const start = ef.start_tick || 0;
    const end   = ef.end_tick || (start + 18 * TICK_RATE);
    if (currentTick < start || currentTick > end) return;
    const pos = worldToCanvas(ef.x, ef.y, this.mapInfo);
    if (!pos) return;

    const fullR = worldUnitsToPx(SMOKE_RADIUS_UNITS, this.mapInfo);
    const age = currentTick - start;
    const left = end - currentTick;
    const life = Math.max(1, end - start);
    const timeRatio = Math.max(0, Math.min(1, left / life));
    // Expansion in, hold, fade out
    let grow = Math.min(1, age / SMOKE_EXPAND_TICKS);
    grow = 1 - (1 - grow) * (1 - grow); // ease-out
    const fade = Math.min(1, left / SMOKE_FADE_TICKS);
    const r = fullR * grow;
    const alpha = 0.72 * fade;
    const invZoom = 1 / Math.max(this.zoom, 1);

    ctx.save();
    ctx.globalCompositeOperation = 'screen';
    const grad = ctx.createRadialGradient(pos.cx, pos.cy, r * 0.15, pos.cx, pos.cy, r);
    grad.addColorStop(0, `rgba(226,232,240,${alpha * 0.82})`);
    grad.addColorStop(0.48, `rgba(190,200,213,${alpha * 0.58})`);
    grad.addColorStop(0.82, `rgba(100,116,139,${alpha * 0.30})`);
    grad.addColorStop(1, `rgba(148,163,184,0)`);
    ctx.fillStyle = grad;
    ctx.beginPath();
    ctx.arc(pos.cx, pos.cy, r, 0, Math.PI * 2);
    ctx.fill();

    // Organic smoke texture. If an asset pack is present, use it as the main
    // cloud and keep procedural puffs only as a fallback.
    const smokeSprite = this._effectAssets?.smokePuff;
    if (smokeSprite) {
      const driftA = Math.sin((currentTick - start) / 96) * 0.2;
      this._drawEffectSprite(ctx, smokeSprite, pos.cx, pos.cy, r * 2.28, alpha * 0.82, age, life, driftA);
      this._drawEffectSprite(
        ctx,
        smokeSprite,
        pos.cx + Math.cos(driftA + 1.7) * r * 0.08,
        pos.cy + Math.sin(driftA + 1.7) * r * 0.08,
        r * 2.08,
        alpha * 0.34,
        age + 41,
        life,
        -driftA * 0.7
      );
    } else {
      for (let i = 0; i < 14; i++) {
        const seed = start + i * 37;
        const a = seededRandom(seed) * Math.PI * 2 + Math.sin((currentTick + i * 11) / 55) * 0.12;
        const d = r * (0.08 + seededRandom(seed + 3) * 0.68);
        const br = r * (0.12 + seededRandom(seed + 7) * 0.18);
        const wobble = Math.sin((currentTick + seed) / 78) * r * 0.025;
        const px = pos.cx + Math.cos(a) * (d + wobble);
        const py = pos.cy + Math.sin(a) * (d - wobble);
        const puffAlpha = alpha * (0.22 + seededRandom(seed + 13) * 0.14);
        const puff = ctx.createRadialGradient(px, py, 0, px, py, br);
        puff.addColorStop(0, `rgba(241,245,249,${puffAlpha})`);
        puff.addColorStop(1, 'rgba(148,163,184,0)');
        ctx.fillStyle = puff;
        ctx.beginPath();
        ctx.arc(px, py, br, 0, Math.PI * 2);
        ctx.fill();
      }
    }

    // Soft shell plus lifetime timer arc.
    ctx.strokeStyle = `rgba(226,232,240,${alpha * 0.24})`;
    ctx.lineWidth = 1.1 * invZoom;
    ctx.beginPath();
    ctx.arc(pos.cx, pos.cy, r * 0.92, 0, Math.PI * 2);
    ctx.stroke();

    ctx.globalCompositeOperation = 'source-over';
    ctx.strokeStyle = `rgba(248,250,252,${0.48 * fade})`;
    ctx.lineWidth = Math.max(1.25 * invZoom, 0.8);
    ctx.lineCap = 'round';
    ctx.shadowColor = 'rgba(255,255,255,0.55)';
    ctx.shadowBlur = 7 * invZoom;
    ctx.beginPath();
    ctx.arc(pos.cx, pos.cy, r + 6 * invZoom, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * timeRatio, false);
    ctx.stroke();
    ctx.restore();
  }

  _drawMolotovArea(ctx, ef, currentTick) {
    const start = ef.start_tick || 0;
    const end   = ef.end_tick || (start + 7 * TICK_RATE);
    if (currentTick < start || currentTick > end) return;
    const pos = worldToCanvas(ef.x, ef.y, this.mapInfo);
    if (!pos) return;

    const fullR = worldUnitsToPx(MOLOTOV_RADIUS_UNITS, this.mapInfo);
    const age = currentTick - start;
    const left = end - currentTick;
    const grow = Math.min(1, age / MOLOTOV_EXPAND_TICKS);
    const fade = Math.min(1, left / MOLOTOV_FADE_TICKS);
    const r = fullR * grow;
    // Fire flicker: small deterministic alpha jitter per frame
    const flicker = 0.88 + 0.12 * seededRandom((ef.start_tick || 0) * 31 + currentTick);
    const alpha = 0.78 * fade * flicker;

    ctx.save();
    ctx.globalCompositeOperation = 'screen';
    const grad = ctx.createRadialGradient(pos.cx, pos.cy, r * 0.1, pos.cx, pos.cy, r);
    grad.addColorStop(0, `rgba(254,215,102,${alpha})`);
    grad.addColorStop(0.55, `rgba(249,115,22,${alpha * 0.85})`);
    grad.addColorStop(1, `rgba(220,38,38,0)`);
    ctx.fillStyle = grad;
    ctx.beginPath();
    ctx.arc(pos.cx, pos.cy, r, 0, Math.PI * 2);
    ctx.fill();

    const fireSprite = this._effectAssets?.molotovFire;
    if (fireSprite) {
      const spriteAge = age < MOLOTOV_EXPAND_TICKS
        ? age * 2.4
        : MOLOTOV_EXPAND_TICKS * 2.4 + ((currentTick - start) % 18) * 0.25;
      const spin = Math.sin((currentTick - start) / 38) * 0.09;
      this._drawEffectSprite(ctx, fireSprite, pos.cx, pos.cy, r * 2.34, alpha * 0.82, spriteAge, 72, spin);
      this._drawEffectSprite(ctx, fireSprite, pos.cx, pos.cy, r * 1.9, alpha * 0.28, spriteAge + 17, 72, spin + 0.55);
    }

    // Jagged inner flame ring for texture
    ctx.strokeStyle = `rgba(254,170,60,${alpha * 0.7})`;
    ctx.lineWidth = 1.4;
    ctx.beginPath();
    const spikes = 9;
    for (let i = 0; i <= spikes; i++) {
      const a = (i / spikes) * Math.PI * 2;
      const jitter = 0.72 + 0.2 * seededRandom((ef.start_tick || 0) * 17 + i * 7 + Math.floor(currentTick / 8));
      const px = pos.cx + Math.cos(a) * r * jitter;
      const py = pos.cy + Math.sin(a) * r * jitter;
      if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
    }
    ctx.closePath();
    ctx.stroke();
    ctx.restore();
  }

  // ── Detonation rings ─────────────────────────────────────────────────────────
  _drawFlashRing(ctx, ef, currentTick) {
    const age = currentTick - (ef.tick || 0);
    if (age < 0 || age > FLASH_RING_TICKS) return;
    const pos = worldToCanvas(ef.x, ef.y, this.mapInfo);
    if (!pos) return;

    const fullR = worldUnitsToPx(FLASH_RADIUS_UNITS, this.mapInfo);
    const t = age / FLASH_RING_TICKS;
    const r = fullR * Math.min(1, t * 2.2); // expands fast, then holds
    const alpha = (1 - t) * 0.8;

    ctx.save();
    // Bright core at detonation point
    if (age < 12) {
      ctx.fillStyle = `rgba(255,255,240,${(1 - age / 12) * 0.9})`;
      ctx.beginPath();
      ctx.arc(pos.cx, pos.cy, 6 + age, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.strokeStyle = `rgba(254,249,195,${alpha})`;
    ctx.lineWidth = 2;
    ctx.shadowColor = 'rgba(254,249,195,0.8)';
    ctx.shadowBlur = 10;
    ctx.beginPath();
    ctx.arc(pos.cx, pos.cy, r, 0, Math.PI * 2);
    ctx.stroke();
    // Faint filled blind radius
    ctx.fillStyle = `rgba(254,249,195,${alpha * 0.15})`;
    ctx.beginPath();
    ctx.arc(pos.cx, pos.cy, r, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  }

  _drawHeRing(ctx, ef, currentTick) {
    const age = currentTick - (ef.tick || 0);
    if (age < 0 || age > HE_DUST_TICKS) return;
    const pos = worldToCanvas(ef.x, ef.y, this.mapInfo);
    if (!pos) return;

    const fullR = worldUnitsToPx(HE_RADIUS_UNITS, this.mapInfo);
    const invZoom = 1 / Math.max(this.zoom, 1);
    const ringT = Math.min(1, age / HE_RING_TICKS);
    const dustT = Math.min(1, age / HE_DUST_TICKS);
    const ringR = fullR * (0.16 + 0.92 * ringT);
    const ringAlpha = Math.max(0, 1 - ringT) * 0.82;
    const dustAlpha = Math.max(0, 1 - dustT) * 0.48;
    const seedBase = Math.floor((ef.tick || 0) * 13 + pos.cx * 3 + pos.cy);

    ctx.save();

    // Short hot flash at the exact detonation point.
    if (age < 13) {
      const hot = 1 - age / 13;
      ctx.globalCompositeOperation = 'screen';
      ctx.shadowColor = 'rgba(253,224,71,0.95)';
      ctx.shadowBlur = 18 * invZoom;
      const core = ctx.createRadialGradient(pos.cx, pos.cy, 0, pos.cx, pos.cy, fullR * 0.32);
      core.addColorStop(0, `rgba(255,255,238,${0.92 * hot})`);
      core.addColorStop(0.35, `rgba(253,186,116,${0.58 * hot})`);
      core.addColorStop(1, 'rgba(180,83,9,0)');
      ctx.fillStyle = core;
      ctx.beginPath();
      ctx.arc(pos.cx, pos.cy, fullR * (0.24 + 0.18 * (1 - hot)), 0, Math.PI * 2);
      ctx.fill();
    }

    const heSprite = this._effectAssets?.heExplosion;
    if (heSprite) {
      const spriteAlpha = Math.min(0.96, 0.24 + (1 - dustT) * 0.74);
      const spriteSize = fullR * (3.35 + dustT * 0.78);
      const rotation = seededRandom(seedBase + 101) * Math.PI * 2;
      ctx.globalCompositeOperation = 'source-over';
      this._drawEffectSprite(ctx, heSprite, pos.cx, pos.cy, spriteSize, spriteAlpha, age, HE_DUST_TICKS, rotation);
    }

    // Warm shockwave: quick read of the blast radius without the old green ring.
    if (age <= HE_RING_TICKS) {
      ctx.globalCompositeOperation = 'screen';
      ctx.strokeStyle = `rgba(255,237,213,${ringAlpha})`;
      ctx.lineWidth = Math.max(1.4 * invZoom, 0.8);
      ctx.lineCap = 'round';
      ctx.shadowColor = 'rgba(251,146,60,0.78)';
      ctx.shadowBlur = 10 * invZoom;
      ctx.beginPath();
      ctx.arc(pos.cx, pos.cy, ringR, 0, Math.PI * 2);
      ctx.stroke();

      ctx.strokeStyle = `rgba(245,158,11,${ringAlpha * 0.45})`;
      ctx.lineWidth = Math.max(3.5 * invZoom, 1.2);
      ctx.shadowBlur = 0;
      ctx.beginPath();
      ctx.arc(pos.cx, pos.cy, ringR * 0.64, 0, Math.PI * 2);
      ctx.stroke();
    }

    // Dust cloud and debris flecks that hang for a few frames after the pop.
    ctx.globalCompositeOperation = 'source-over';
    for (let i = 0; i < 16; i++) {
      const a = seededRandom(seedBase + i * 9) * Math.PI * 2;
      const speed = 0.24 + seededRandom(seedBase + i * 9 + 1) * 0.82;
      const dist = fullR * speed * Math.pow(dustT, 0.68);
      const drift = Math.sin((currentTick + i * 19) / 18) * fullR * 0.025;
      const px = pos.cx + Math.cos(a) * dist + Math.cos(a + Math.PI / 2) * drift;
      const py = pos.cy + Math.sin(a) * dist + Math.sin(a + Math.PI / 2) * drift;
      const puffR = fullR * (0.035 + seededRandom(seedBase + i * 9 + 2) * 0.055) * (0.7 + dustT);
      const warmth = seededRandom(seedBase + i * 9 + 3);
      const color = warmth > 0.55 ? '214,178,120' : '148,137,124';
      const puff = ctx.createRadialGradient(px, py, 0, px, py, puffR);
      puff.addColorStop(0, `rgba(${color},${dustAlpha * (0.42 + warmth * 0.22)})`);
      puff.addColorStop(1, 'rgba(71,85,105,0)');
      ctx.fillStyle = puff;
      ctx.beginPath();
      ctx.arc(px, py, puffR, 0, Math.PI * 2);
      ctx.fill();

      if (age < 34 && i < 9) {
        const shardAlpha = (1 - age / 34) * 0.45;
        ctx.strokeStyle = `rgba(251,191,36,${shardAlpha})`;
        ctx.lineWidth = Math.max(1.1 * invZoom, 0.65);
        ctx.beginPath();
        ctx.moveTo(pos.cx + Math.cos(a) * fullR * 0.09, pos.cy + Math.sin(a) * fullR * 0.09);
        ctx.lineTo(pos.cx + Math.cos(a) * dist * 0.72, pos.cy + Math.sin(a) * dist * 0.72);
        ctx.stroke();
      }
    }
    ctx.restore();
  }

  // ── Bomb rendering ───────────────────────────────────────────────────────────
  _drawBombIcon(ctx, b, ageTicks) {
    const evt   = (b.event || '').toLowerCase();
    const isDefuse = evt.includes('defus');
    const isPlant  = evt === 'plant' || evt === 'plant_start';
    // Planted bomb icon persists (no fade); other events fade out
    const alpha = isPlant ? 1 : Math.max(0, 1 - ageTicks / BOMB_EVENT_FLASH_TICKS);
    const color = isDefuse ? '#22c55e' : '#ef4444';
    const pulse = 1 + Math.sin((ageTicks / 16) * 0.3) * 0.15;
    const r = 9 * pulse;

    ctx.save();
    ctx.translate(b.cx, b.cy);
    ctx.globalAlpha = alpha;
    ctx.shadowColor = color;
    ctx.shadowBlur  = 16;
    ctx.fillStyle   = color;
    ctx.beginPath();
    ctx.arc(0, 0, r, 0, Math.PI * 2);
    ctx.fill();
    ctx.shadowBlur = 0;
    ctx.fillStyle  = '#fff';
    ctx.font       = 'bold 10px Inter, sans-serif';
    ctx.textAlign  = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(isDefuse ? 'D' : 'C4', 0, 0);
    ctx.restore();
  }

  /**
   * Bomb detonation: staggered expanding shockwave rings + drifting dust
   * particles that fade out — replaces the old single pulsing dot.
   */
  _drawExplosion(ctx, b, ageTicks) {
    if (ageTicks < 0 || ageTicks > EXPLOSION_TICKS) return;
    const fullR = worldUnitsToPx(EXPLOSION_RADIUS_UNITS, this.mapInfo);
    const seedBase = (b.tick || 0) * 101;

    ctx.save();
    ctx.translate(b.cx, b.cy);

    // Initial white-hot core flash
    if (ageTicks < 14) {
      const coreA = (1 - ageTicks / 14);
      ctx.fillStyle = `rgba(255,241,200,${coreA * 0.95})`;
      ctx.beginPath();
      ctx.arc(0, 0, 8 + ageTicks * 1.6, 0, Math.PI * 2);
      ctx.fill();
    }

    // 3 staggered shockwave rings
    const RINGS = 3;
    const ringLife = EXPLOSION_TICKS - EXPLOSION_RING_STAGGER * (RINGS - 1);
    for (let i = 0; i < RINGS; i++) {
      const rAge = ageTicks - i * EXPLOSION_RING_STAGGER;
      if (rAge < 0 || rAge > ringLife) continue;
      const t = rAge / ringLife;
      const radius = fullR * (0.35 + 0.65 * (1 - i * 0.18)) * t;
      const alpha = (1 - t) * (0.75 - i * 0.15);
      ctx.strokeStyle = `rgba(255,${170 - i * 30},${70 - i * 15},${alpha})`;
      ctx.lineWidth = 3 - i * 0.7;
      ctx.shadowColor = 'rgba(255,140,60,0.7)';
      ctx.shadowBlur = 12;
      ctx.beginPath();
      ctx.arc(0, 0, radius, 0, Math.PI * 2);
      ctx.stroke();
    }

    // Dust scatter: deterministic particles drifting outward
    ctx.shadowBlur = 0;
    for (let i = 0; i < EXPLOSION_PARTICLES; i++) {
      const a  = seededRandom(seedBase + i * 3)     * Math.PI * 2;
      const v  = 0.35 + seededRandom(seedBase + i * 3 + 1) * 0.65; // speed factor
      const sz = 1.5 + seededRandom(seedBase + i * 3 + 2) * 2.5;
      const t  = ageTicks / EXPLOSION_TICKS;
      const dist = fullR * 0.8 * v * Math.sqrt(t); // decelerating drift
      const px = Math.cos(a) * dist;
      const py = Math.sin(a) * dist;
      const pAlpha = Math.max(0, (1 - t) * 0.7);
      ctx.fillStyle = `rgba(214,196,168,${pAlpha})`;
      ctx.beginPath();
      ctx.arc(px, py, sz * (1 + t), 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.restore();
  }

  // ── Focused-player overlays ──────────────────────────────────────────────────
  _drawAimLine(ctx, p) {
    if (p.yaw == null || !p._pos) return;
    const { dx, dy } = yawToDir(p.yaw);
    const { cx, cy } = p._pos;
    const len = CANVAS_SIZE * 1.6; // reaches the canvas edge from anywhere
    const invZoom = 1 / Math.max(this.zoom, 1);

    ctx.save();
    ctx.strokeStyle = 'rgba(250,204,21,0.85)';
    ctx.lineWidth = 1.6 * invZoom;
    ctx.lineCap = 'round';
    ctx.shadowColor = 'rgba(250,204,21,0.7)';
    ctx.shadowBlur = 7;
    ctx.beginPath();
    ctx.moveTo(cx + dx * (PLAYER_MARKER_R + 3) * invZoom, cy + dy * (PLAYER_MARKER_R + 3) * invZoom);
    ctx.lineTo(cx + dx * len, cy + dy * len);
    ctx.stroke();
    ctx.restore();
  }

  _drawSelectionHighlight(ctx, p) {
    const { cx, cy } = p._pos;
    const s = 13;
    const invZoom = 1 / Math.max(this.zoom, 1);
    ctx.save();
    ctx.translate(cx, cy);
    ctx.scale(invZoom, invZoom);
    ctx.strokeStyle = '#fb923c';
    ctx.lineWidth = 1.7;
    ctx.shadowColor = 'rgba(251,146,60,0.8)';
    ctx.shadowBlur = 6;
    ctx.beginPath();
    ctx.roundRect(-s, -s, s * 2, s * 2, 4);
    ctx.stroke();
    ctx.restore();
  }

  _drawInfoCard(ctx, p) {
    const { cx, cy } = p._pos;
    const name = p.name.length > 14 ? p.name.substring(0, 14) + '…' : p.name;
    const weapon = String(p.weapon || '').replace(/^weapon_/, '') || '-';
    const line2 = `${weapon}  HP ${Math.round(p.hp)}  AR ${Math.round(p.armor || 0)}`;
    const invZoom = 1 / Math.max(this.zoom, 1);
    const screen = this._baseToScreen(p._pos);

    ctx.save();
    ctx.translate(cx, cy);
    ctx.scale(invZoom, invZoom);
    ctx.font = 'bold 10px Inter, sans-serif';
    const w = Math.max(ctx.measureText(name).width, ctx.measureText(line2).width) + 16;
    const h = 32;
    let x = 18;
    let y = -h - 18;
    if (screen.cx + x + w > CANVAS_SIZE - 4) x = -w - 18;
    if (screen.cy + y < 4) y = 18;
    if (screen.cy + y + h > CANVAS_SIZE - 4) y = -h - 18;

    ctx.fillStyle = 'rgba(10,15,25,0.88)';
    ctx.strokeStyle = 'rgba(251,146,60,0.65)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.roundRect(x, y, w, h, 5);
    ctx.fill();
    ctx.stroke();

    ctx.textAlign = 'left';
    ctx.textBaseline = 'top';
    ctx.fillStyle = p.side === 'CT' ? '#93c5fd' : '#fcd34d';
    ctx.fillText(name, x + 8, y + 5);
    ctx.font = '9px Inter, sans-serif';
    ctx.fillStyle = '#cbd5e1';
    ctx.fillText(line2, x + 8, y + 18);
    ctx.restore();
  }

  _drawUtilityIcon(ctx, type, cfg, scale = 1) {
    const t = String(type || '').toLowerCase();
    const kind = utilityKind(t);
    const s = Math.max(0.7, scale);

    ctx.save();
    ctx.scale(s, s);
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    ctx.shadowColor = cfg.glow;
    ctx.shadowBlur = 8;

    const icon = this._iconAssets?.[kind];
    if (icon) {
      const size = kind === 'he' ? 52 : kind === 'flash' ? 54 : kind === 'molotov' ? 58 : 56;
      ctx.globalAlpha = 0.98;
      ctx.drawImage(icon, -size / 2, -size / 2, size, size);
      ctx.restore();
      return;
    }

    if (t.includes('smoke')) {
      ctx.rotate(-0.08);
      ctx.fillStyle = '#f8fafc';
      ctx.strokeStyle = 'rgba(15,23,42,0.45)';
      ctx.lineWidth = 1.1;
      ctx.beginPath();
      ctx.roundRect(-4.2, -8.5, 8.4, 17, 2.6);
      ctx.fill();
      ctx.stroke();
      ctx.shadowBlur = 0;
      ctx.strokeStyle = 'rgba(51,65,85,0.55)';
      ctx.beginPath();
      ctx.moveTo(-3.2, -3.5); ctx.lineTo(3.2, -3.5);
      ctx.moveTo(-3.2, 2.2);  ctx.lineTo(3.2, 2.2);
      ctx.stroke();
      ctx.fillStyle = '#cbd5e1';
      ctx.fillRect(-2.6, -6.8, 5.2, 2.1);
    } else if (t.includes('flash')) {
      ctx.rotate(0.22);
      ctx.fillStyle = '#fff7cc';
      ctx.strokeStyle = 'rgba(92,77,24,0.55)';
      ctx.lineWidth = 1.1;
      ctx.beginPath();
      ctx.roundRect(-3.6, -8, 7.2, 16, 3.2);
      ctx.fill();
      ctx.stroke();
      ctx.shadowBlur = 0;
      ctx.fillStyle = '#d9edf7';
      ctx.fillRect(-2.7, -5.6, 5.4, 2.2);
      ctx.fillStyle = '#64748b';
      ctx.beginPath();
      ctx.arc(0, -8.5, 2, 0, Math.PI * 2);
      ctx.fill();
    } else if (t.includes('molotov') || t.includes('incendiary')) {
      ctx.rotate(-0.35);
      ctx.fillStyle = '#f8fafc';
      ctx.strokeStyle = 'rgba(15,23,42,0.6)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.roundRect(-3, -8, 6, 13, 2.4);
      ctx.fill();
      ctx.stroke();
      ctx.fillStyle = 'rgba(251,146,60,0.82)';
      ctx.fillRect(-2.4, -1, 4.8, 5.4);
      ctx.fillStyle = '#fef08a';
      ctx.beginPath();
      ctx.moveTo(0, -11);
      ctx.quadraticCurveTo(4, -6, 0.8, -4.2);
      ctx.quadraticCurveTo(-3.8, -6, 0, -11);
      ctx.fill();
    } else if (t.includes('he')) {
      ctx.fillStyle = '#86efac';
      ctx.strokeStyle = 'rgba(20,83,45,0.75)';
      ctx.lineWidth = 1.1;
      ctx.beginPath();
      ctx.arc(0, 1, 7.2, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
      ctx.shadowBlur = 0;
      ctx.strokeStyle = 'rgba(20,83,45,0.45)';
      ctx.beginPath();
      for (let x = -4; x <= 4; x += 4) {
        ctx.moveTo(x, -4.5); ctx.lineTo(x, 6.2);
      }
      for (let y = -2; y <= 5; y += 3.5) {
        ctx.moveTo(-5.8, y); ctx.lineTo(5.8, y);
      }
      ctx.stroke();
      ctx.fillStyle = '#d9f99d';
      ctx.fillRect(-2.6, -8.5, 5.2, 3.2);
    } else {
      ctx.fillStyle = cfg.color;
      ctx.strokeStyle = 'rgba(255,255,255,0.55)';
      ctx.lineWidth = 1.1;
      ctx.beginPath();
      ctx.roundRect(-5.5, -6, 11, 12, 3);
      ctx.fill();
      ctx.stroke();
      ctx.shadowBlur = 0;
      ctx.fillStyle = 'rgba(15,23,42,0.55)';
      ctx.beginPath();
      ctx.arc(0, 0, 2.4, 0, Math.PI * 2);
      ctx.fill();
    }

    ctx.restore();
  }

  _drawGrenade(ctx, g, currentTick) {
    const state = this._grenadePositionAtTick(g, currentTick);
    if (!state) return;

    // Once a smoke/molotov detonates its persistent area effect takes over —
    // don't keep drawing the icon on top of the cloud/fire.
    if (state.phase === 'impact' && (g.type === 'smoke' || g.type === 'molotov' || g.type === 'incendiary')) {
      return;
    }

    const pos = worldToCanvas(state.wx, state.wy, this.mapInfo);
    if (!pos) return;

    const cfg = GRENADE_CONFIG[g.type] || GRENADE_CONFIG.decoy;
    const invZoom = 1 / Math.max(this.zoom, 1);
    this._drawGrenadeTrail(ctx, g, state, cfg);
    const ageTicks = Math.max(0, currentTick - (g.tick || currentTick));
    const expand = ageTicks < 32 ? (ageTicks / 32) * 1.3 : 1.0;

    let alpha = 0.9;
    if (state.phase === 'impact') {
      const impactAge = currentTick - (g.detonate_tick || currentTick);
      alpha = Math.max(0, 1 - impactAge / GRENADE_IMPACT_TICKS) * 0.95;
    }

    ctx.save();
    ctx.translate(pos.cx, pos.cy);
    ctx.scale(invZoom, invZoom);
    ctx.globalAlpha = alpha;

    this._drawUtilityIcon(ctx, g.type, cfg, expand);

    ctx.restore();

    // Thrower name (brief, only while fresh)
    if (ageTicks < 96 && this.showLabels) {
      const thrower = String(g.thrower || '');
      const shortName = thrower.length > 8 ? thrower.substring(0, 8) + '…' : thrower;
      ctx.save();
      ctx.translate(pos.cx, pos.cy);
      ctx.scale(invZoom, invZoom);
      ctx.globalAlpha = alpha * 0.8;
      ctx.font        = '9px Inter, sans-serif';
      ctx.textAlign   = 'center';
      ctx.fillStyle   = '#e8edf5';
      ctx.shadowColor = '#000';
      ctx.shadowBlur  = 3;
      ctx.fillText(shortName, 0, -cfg.r - 5);
      ctx.restore();
    }
  }
}
