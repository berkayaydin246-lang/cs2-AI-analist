/**
 * replay.js — Canvas 2D Replay Engine
 *
 * Renders player positions tick-by-tick on a CS2 radar background.
 * Features: trails, HP/armor bars, yaw arrows, kill markers, bomb events with
 * explosion particles, grenade flight icons, persistent smoke/molotov areas,
 * flash blind radii + player blind tint, HE blast rings, bullet tracers,
 * weapon-class glyphs, defuse-kit badges, bomb-carrier badge and a
 * focused-player mode (long aim line + highlight + info card).
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

const CANVAS_SIZE = 600;
// awpy radar images are 1024×1024 — world→pixel coords are in that space,
// so we must scale down to our canvas size.
const RADAR_SIZE  = 1024;
const SCALE_RATIO = CANVAS_SIZE / RADAR_SIZE; // 0.5859...
const TICK_RATE   = 64;

/**
 * Convert CS2 world coordinates to canvas pixel coordinates.
 * 1) world → radar pixel (0-1024) using awpy formula
 * 2) scale radar pixel → canvas pixel (0-600)
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
const TRACER_TICKS = 13;
const TRACER_LENGTH_PX = 130;

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
const HE_RING_TICKS        = 36;

// Bomb events
const BOMB_EVENT_FLASH_TICKS = 448;   // pickup/drop/defuse icon lifetime
const EXPLOSION_TICKS        = 170;   // shockwave rings lifetime
const EXPLOSION_RING_STAGGER = 7;     // ticks between successive rings
const EXPLOSION_RADIUS_UNITS = 420;   // outermost shockwave radius
const EXPLOSION_PARTICLES    = 9;

/** Deterministic pseudo-random in [0,1) from an integer seed (mulberry32). */
function seededRandom(seed) {
  let t = (seed + 0x6D2B79F5) | 0;
  t = Math.imul(t ^ (t >>> 15), t | 1);
  t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
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

// ── ReplayEngine ──────────────────────────────────────────────────────────────
class ReplayEngine {
  /**
   * @param {HTMLCanvasElement} canvas
   * @param {object} data   API response from /api/demo/{id}/replay/{round}
   * @param {string|null} radarUrl  Blob URL for the map radar image
   */
  constructor(canvas, data, radarUrl) {
    this.canvas   = canvas;
    this.ctx      = canvas.getContext('2d');
    this.data     = data;
    this.radarUrl = radarUrl;
    this.radarImg = null;

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
    this.playing      = false;
    this._raf         = null;
    this._lastTime    = 0;
    this.speed        = 1.0;
    this.msPerFrame   = 33; // ~30fps at 1x

    this.showTrails = true;
    this.showLabels = true;

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
        const dur = Number(b.duration_s) || 0;
        if (!b.player || dur <= 0.2) continue; // ignore negligible blinds
        this._blindIntervals.push({
          player: b.player,
          start: ef.tick,
          end: ef.tick + Math.round(dur * TICK_RATE),
        });
      }
    }

    // Reset activation flags
    this.kills.forEach(k => { k._activated = false; });
    this.bombs.forEach(b => { b._activated = false; });
    this.grenades.forEach(g => { g._activated = false; });
    this.shots.forEach(s => { s._activated = false; });

    // Hit-testable positions of players drawn in the last frame
    this._lastDrawnPlayers = [];

    // Click-to-select (assignment, not addEventListener — engine is recreated per round)
    this.canvas.onclick = (e) => {
      const rect = this.canvas.getBoundingClientRect();
      const x = (e.clientX - rect.left) * (this.canvas.width / rect.width);
      const y = (e.clientY - rect.top) * (this.canvas.height / rect.height);
      this._handleCanvasClick(x, y);
    };
  }

  async init() {
    if (this.radarUrl) {
      this.radarImg = await this._loadImage(this.radarUrl);
    }
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

  // ── Focused-player mode ──────────────────────────────────────────────────────
  setSelectedPlayer(name) {
    this.selectedPlayer = name || null;
    this._draw();
    if (this.onPlayerSelect) this.onPlayerSelect(this.selectedPlayer);
  }

  _handleCanvasClick(x, y) {
    const HIT_RADIUS = 14;
    let best = null;
    let bestDist = Infinity;
    for (const p of this._lastDrawnPlayers) {
      if (!p._pos) continue;
      const d = Math.hypot(p._pos.cx - x, p._pos.cy - y);
      if (d <= HIT_RADIUS && d < bestDist) { best = p; bestDist = d; }
    }
    if (best) {
      // Toggle selection when the same player is clicked again
      this.setSelectedPlayer(best.name === this.selectedPlayer ? null : best.name);
    } else {
      this.setSelectedPlayer(null);
    }
  }

  // ── Playback controls ────────────────────────────────────────────────────────
  play() {
    if (this.playing) return;
    this.playing  = true;
    this._lastTime = performance.now();
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
    const backwards = idx < this.currentFrame;
    if (backwards) {
      // Reset all events when seeking backwards
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
    this.currentFrame = idx;
    this._updateEventsForFrame();
    this._draw();
    if (this.onFrameChange) this.onFrameChange(this.currentFrame, this.frames.length);
  }

  /** Seek directly to a demo tick (used by scrubber event markers). */
  seekToTick(tick) {
    if (!this.frames.length) return;
    const t0 = this.frames[0].tick ?? 0;
    const t1 = this.frames[this.frames.length - 1].tick ?? t0;
    const span = Math.max(t1 - t0, 1);
    const idx = Math.round(((tick - t0) / span) * (this.frames.length - 1));
    this.seekTo(idx);
  }

  nextFrame() { this.seekTo(this.currentFrame + 1); }
  prevFrame() { this.seekTo(this.currentFrame - 1); }

  setSpeed(v)  { this.speed = v; }
  setTrails(v) { this.showTrails = v; if (!v) this._trails = {}; this._draw(); }
  setLabels(v) { this.showLabels = v; this._draw(); }

  stop() { this.pause(); this.seekTo(0); }

  // ── Animation loop ───────────────────────────────────────────────────────────
  _loop(now) {
    if (!this.playing) return;
    const elapsed  = now - this._lastTime;
    const interval = this.msPerFrame / this.speed;
    if (elapsed >= interval) {
      this._lastTime = now - (elapsed % interval);
      if (this.currentFrame < this.frames.length - 1) {
        this.currentFrame++;
        this._updateEventsForFrame();
        this._draw();
        if (this.onFrameChange) this.onFrameChange(this.currentFrame, this.frames.length);
      } else {
        this.pause();
        return;
      }
    }
    this._raf = requestAnimationFrame(this._loop.bind(this));
  }

  // ── Event state update ───────────────────────────────────────────────────────
  _updateEventsForFrame() {
    const currentTick = this.frames[this.currentFrame]?.tick ?? 0;

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
        if (s.x != null && s.y != null && s.yaw != null) {
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
      return { wx: path[path.length - 1][0], wy: path[path.length - 1][1], phase: 'impact' };
    }

    const elapsed = Math.max(0, currentTick - throwTick);
    const progress = Math.max(0, Math.min(1, elapsed / flightTicks));
    const fidx = progress * (path.length - 1);
    const i0 = Math.floor(fidx);
    const i1 = Math.min(path.length - 1, i0 + 1);
    const t = fidx - i0;
    const wx = path[i0][0] + (path[i1][0] - path[i0][0]) * t;
    const wy = path[i0][1] + (path[i1][1] - path[i0][1]) * t;
    return { wx, wy, phase: 'flight' };
  }

  /** Remaining blind duration in ticks for a player at the given tick (0 = not blind). */
  _blindRemaining(playerName, currentTick) {
    let remaining = 0;
    for (const b of this._blindIntervals) {
      if (b.player !== playerName) continue;
      if (currentTick >= b.start && currentTick <= b.end) {
        remaining = Math.max(remaining, b.end - currentTick);
      }
    }
    return remaining;
  }

  // ── Main draw ────────────────────────────────────────────────────────────────
  _draw() {
    const ctx  = this.ctx;
    const size = CANVAS_SIZE;

    ctx.clearRect(0, 0, size, size);
    ctx.fillStyle = '#0d1117';
    ctx.fillRect(0, 0, size, size);

    // Radar image
    if (this.radarImg) {
      ctx.globalAlpha = 0.72;
      ctx.drawImage(this.radarImg, 0, 0, size, size);
      ctx.globalAlpha = 1;
    }

    const frame = this.frames[this.currentFrame];
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

    // Persistent area effects (smoke clouds, fire zones) — under everything
    for (const ef of this.effects) {
      if (ef.type === 'smoke')   this._drawSmokeArea(ctx, ef, currentTick);
      if (ef.type === 'molotov') this._drawMolotovArea(ctx, ef, currentTick);
    }

    // Draw trails
    if (this.showTrails) {
      for (const p of players) {
        if (!p._pos) continue;
        const trail = this._trails[p.name];
        if (!trail || trail.length < 2) continue;
        const color = p.side === 'CT' ? '#3b82f6' : '#f59e0b';
        ctx.save();
        ctx.lineWidth = 1.5;
        for (let i = 1; i < trail.length; i++) {
          ctx.globalAlpha = (i / trail.length) * 0.45;
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
    for (const g of this._activeGrenades) {
      this._drawGrenade(ctx, g, currentTick);
    }

    // Bullet tracers
    for (const s of this._activeShots) {
      this._drawTracer(ctx, s, currentTick);
    }

    // Detonation rings (flash blind radius, HE blast)
    for (const ef of this.effects) {
      if (ef.type === 'flash') this._drawFlashRing(ctx, ef, currentTick);
      if (ef.type === 'he')    this._drawHeRing(ctx, ef, currentTick);
    }

    // Draw kill markers
    for (const k of this._activeKills) {
      this._drawKillMarker(ctx, k, currentTick);
    }

    // Draw bomb events (planted icon, defuse icon, explosion effect)
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
  }

  // ── Draw helpers ─────────────────────────────────────────────────────────────

  _drawAlivePlayer(ctx, p, currentTick) {
    const { cx, cy } = p._pos;
    const isT  = p.side !== 'CT';
    const color = isT ? '#f59e0b' : '#3b82f6';
    const glow  = isT ? 'rgba(245,158,11,.55)' : 'rgba(59,130,246,.55)';
    const r = 9;

    ctx.save();
    ctx.translate(cx, cy);

    // Outer glow ring
    ctx.shadowColor = glow;
    ctx.shadowBlur  = 14;
    ctx.strokeStyle = color;
    ctx.lineWidth   = 1.5;
    ctx.globalAlpha = 0.35;
    ctx.beginPath();
    ctx.arc(0, 0, r + 3, 0, Math.PI * 2);
    ctx.stroke();

    // Filled dot
    ctx.globalAlpha = 1;
    ctx.shadowBlur  = 10;
    ctx.fillStyle   = color;
    ctx.beginPath();
    ctx.arc(0, 0, r, 0, Math.PI * 2);
    ctx.fill();

    // Inner highlight
    ctx.shadowBlur  = 0;
    ctx.fillStyle   = 'rgba(255,255,255,0.75)';
    ctx.beginPath();
    ctx.arc(-2.5, -2.5, 2.5, 0, Math.PI * 2);
    ctx.fill();

    // Blind overlay: white flash on the marker while the player is blinded
    const blindLeft = currentTick != null ? this._blindRemaining(p.name, currentTick) : 0;
    if (blindLeft > 0) {
      const strength = Math.min(1, blindLeft / (2 * TICK_RATE)); // full white for ≥2 s left
      ctx.globalAlpha = 0.35 + 0.5 * strength;
      ctx.fillStyle = '#ffffff';
      ctx.beginPath();
      ctx.arc(0, 0, r - 1, 0, Math.PI * 2);
      ctx.fill();
      ctx.globalAlpha = 1;
    }

    // Yaw direction arrow (canvas Y is flipped vs world yaw)
    if (p.yaw != null) {
      const { dx, dy } = yawToDir(p.yaw);
      ctx.strokeStyle = 'rgba(255,255,255,0.85)';
      ctx.lineWidth   = 2;
      ctx.lineCap     = 'round';
      ctx.beginPath();
      ctx.moveTo(dx * r, dy * r);
      ctx.lineTo(dx * (r + 8), dy * (r + 8));
      ctx.stroke();
    }

    // HP bar
    const hp   = Math.max(0, Math.min(100, p.hp));
    const barW = 22;
    const barH = 3;
    const barX = -barW / 2;
    const barY = r + 5;
    ctx.fillStyle = 'rgba(7,11,20,0.75)';
    ctx.fillRect(barX - 1, barY - 1, barW + 2, barH + 2);
    ctx.fillStyle = hp > 60 ? '#22c55e' : hp > 30 ? '#eab308' : '#ef4444';
    ctx.fillRect(barX, barY, barW * (hp / 100), barH);

    // Armor bar (thin gray-blue bar right under the HP bar)
    const armor = Math.max(0, Math.min(100, Number(p.armor) || 0));
    if (armor > 0) {
      const aY = barY + barH + 2;
      ctx.fillStyle = 'rgba(7,11,20,0.75)';
      ctx.fillRect(barX - 1, aY - 1, barW + 2, 2 + 2);
      ctx.fillStyle = '#7da7d9';
      ctx.fillRect(barX, aY, barW * (armor / 100), 2);
    }

    // Weapon-class glyph to the upper-right of the marker
    const wCls = weaponClass(p.weapon);
    if (wCls) this._drawWeaponGlyph(ctx, wCls, r + 4, -r - 4);

    // Defuse kit badge (CT only)
    if (p.side === 'CT' && p.defuser) {
      ctx.fillStyle = '#22c55e';
      ctx.strokeStyle = 'rgba(255,255,255,0.7)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.arc(-r - 4, -r - 3, 4.5, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
      ctx.fillStyle = '#06230f';
      ctx.font = 'bold 6px Inter, sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText('D', -r - 4, -r - 2.6);
    }

    // Bomb carrier badge (T holding the C4)
    if (this._bombCarrier && p.name === this._bombCarrier) {
      ctx.fillStyle = '#ef4444';
      ctx.strokeStyle = 'rgba(255,255,255,0.8)';
      ctx.lineWidth = 1;
      const bx = -r - 5, by = r + 3;
      ctx.beginPath();
      ctx.roundRect(bx - 6, by - 4, 13, 8.5, 2);
      ctx.fill();
      ctx.stroke();
      ctx.fillStyle = '#fff';
      ctx.font = 'bold 6px Inter, sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText('C4', bx + 0.5, by + 0.6);
    }

    ctx.restore();

    // Player name label (no transform to keep text crisp)
    if (this.showLabels) {
      const shortName = p.name.length > 10 ? p.name.substring(0, 10) + '…' : p.name;
      ctx.save();
      ctx.shadowColor = 'rgba(0,0,0,0.9)';
      ctx.shadowBlur  = 5;
      ctx.font        = 'bold 10px Inter, sans-serif';
      ctx.textAlign   = 'center';
      ctx.textBaseline = 'top';
      ctx.fillStyle   = '#e8edf5';
      ctx.fillText(shortName, cx, cy + r + 16);
      ctx.restore();
    }
  }

  /** Tiny weapon-class silhouette drawn near the player marker. */
  _drawWeaponGlyph(ctx, cls, gx, gy) {
    ctx.save();
    ctx.translate(gx, gy);
    ctx.strokeStyle = 'rgba(232,237,245,0.95)';
    ctx.fillStyle   = 'rgba(232,237,245,0.95)';
    ctx.lineWidth   = 1.6;
    ctx.lineCap     = 'round';
    ctx.shadowColor = 'rgba(0,0,0,0.8)';
    ctx.shadowBlur  = 3;

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
        ctx.lineWidth = 2.4;
        ctx.moveTo(-4, 0.5); ctx.lineTo(4, 0.5);
        ctx.stroke();
        ctx.lineWidth = 1.6;
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
    const color = isT ? '#f59e0b' : '#3b82f6';
    const s = 5;
    ctx.save();
    ctx.translate(cx, cy);
    ctx.globalAlpha = 0.28;
    ctx.strokeStyle = color;
    ctx.lineWidth   = 2;
    ctx.beginPath(); ctx.moveTo(-s, -s); ctx.lineTo(s, s); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(s, -s); ctx.lineTo(-s, s); ctx.stroke();
    ctx.restore();
  }

  _drawKillMarker(ctx, k, currentTick) {
    const ageTicks = currentTick - (k.tick || currentTick);
    const alpha = Math.max(0, 1 - ageTicks / KILL_FLASH_TICKS);
    const color = k.victim_side === 'CT' ? '#3b82f6' : '#f59e0b';
    const s = 6;
    ctx.save();
    ctx.translate(k.cx, k.cy);
    ctx.globalAlpha = alpha * 0.9;
    ctx.strokeStyle = color;
    ctx.lineWidth   = 2.5;
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
    grad.addColorStop(1, color + '0)');
    ctx.strokeStyle = grad;
    ctx.lineWidth = 1.6;
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
    // Expansion in, hold, fade out
    let grow = Math.min(1, age / SMOKE_EXPAND_TICKS);
    grow = 1 - (1 - grow) * (1 - grow); // ease-out
    const fade = Math.min(1, left / SMOKE_FADE_TICKS);
    const r = fullR * grow;
    const alpha = 0.55 * fade;

    ctx.save();
    const grad = ctx.createRadialGradient(pos.cx, pos.cy, r * 0.15, pos.cx, pos.cy, r);
    grad.addColorStop(0, `rgba(203,213,225,${alpha})`);
    grad.addColorStop(0.75, `rgba(148,163,184,${alpha * 0.85})`);
    grad.addColorStop(1, `rgba(148,163,184,0)`);
    ctx.fillStyle = grad;
    ctx.beginPath();
    ctx.arc(pos.cx, pos.cy, r, 0, Math.PI * 2);
    ctx.fill();
    // Soft edge ring
    ctx.strokeStyle = `rgba(203,213,225,${alpha * 0.5})`;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.arc(pos.cx, pos.cy, r * 0.92, 0, Math.PI * 2);
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
    const alpha = 0.6 * fade * flicker;

    ctx.save();
    const grad = ctx.createRadialGradient(pos.cx, pos.cy, r * 0.1, pos.cx, pos.cy, r);
    grad.addColorStop(0, `rgba(254,215,102,${alpha})`);
    grad.addColorStop(0.55, `rgba(249,115,22,${alpha * 0.85})`);
    grad.addColorStop(1, `rgba(220,38,38,0)`);
    ctx.fillStyle = grad;
    ctx.beginPath();
    ctx.arc(pos.cx, pos.cy, r, 0, Math.PI * 2);
    ctx.fill();
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
    if (age < 0 || age > HE_RING_TICKS) return;
    const pos = worldToCanvas(ef.x, ef.y, this.mapInfo);
    if (!pos) return;

    const fullR = worldUnitsToPx(HE_RADIUS_UNITS, this.mapInfo);
    const t = age / HE_RING_TICKS;
    const r = fullR * t;
    const alpha = (1 - t) * 0.85;

    ctx.save();
    ctx.strokeStyle = `rgba(74,222,128,${alpha})`;
    ctx.lineWidth = 2.5;
    ctx.shadowColor = 'rgba(74,222,128,0.7)';
    ctx.shadowBlur = 8;
    ctx.beginPath();
    ctx.arc(pos.cx, pos.cy, r, 0, Math.PI * 2);
    ctx.stroke();
    // Inner blast core
    if (age < 8) {
      ctx.fillStyle = `rgba(190,242,100,${(1 - age / 8) * 0.8})`;
      ctx.beginPath();
      ctx.arc(pos.cx, pos.cy, 5, 0, Math.PI * 2);
      ctx.fill();
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

    ctx.save();
    ctx.strokeStyle = 'rgba(250,204,21,0.85)';
    ctx.lineWidth = 2;
    ctx.lineCap = 'round';
    ctx.shadowColor = 'rgba(250,204,21,0.7)';
    ctx.shadowBlur = 8;
    ctx.beginPath();
    ctx.moveTo(cx + dx * 10, cy + dy * 10);
    ctx.lineTo(cx + dx * len, cy + dy * len);
    ctx.stroke();
    ctx.restore();
  }

  _drawSelectionHighlight(ctx, p) {
    const { cx, cy } = p._pos;
    const s = 15;
    ctx.save();
    ctx.translate(cx, cy);
    ctx.strokeStyle = '#fb923c';
    ctx.lineWidth = 2;
    ctx.shadowColor = 'rgba(251,146,60,0.8)';
    ctx.shadowBlur = 8;
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

    ctx.save();
    ctx.font = 'bold 10px Inter, sans-serif';
    const w = Math.max(ctx.measureText(name).width, ctx.measureText(line2).width) + 16;
    const h = 32;
    // Keep the card inside the canvas
    let x = cx + 20;
    let y = cy - h - 8;
    if (x + w > CANVAS_SIZE - 4) x = cx - w - 20;
    if (y < 4) y = cy + 20;

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
    const ageTicks = Math.max(0, currentTick - (g.tick || currentTick));
    const expand = ageTicks < 32 ? (ageTicks / 32) * 1.3 : 1.0;

    let alpha = 0.9;
    if (state.phase === 'impact') {
      const impactAge = currentTick - (g.detonate_tick || currentTick);
      alpha = Math.max(0, 1 - impactAge / GRENADE_IMPACT_TICKS) * 0.95;
    }

    ctx.save();
    ctx.translate(pos.cx, pos.cy);
    ctx.globalAlpha = alpha;

    // Glow
    ctx.shadowColor = cfg.glow;
    ctx.shadowBlur  = 12;

    // Circle
    ctx.fillStyle = cfg.color;
    ctx.beginPath();
    ctx.arc(0, 0, cfg.r * expand, 0, Math.PI * 2);
    ctx.fill();

    // Border
    ctx.shadowBlur  = 0;
    ctx.strokeStyle = 'rgba(255,255,255,0.5)';
    ctx.lineWidth   = 1;
    ctx.beginPath();
    ctx.arc(0, 0, cfg.r * expand, 0, Math.PI * 2);
    ctx.stroke();

    // Label
    ctx.fillStyle   = '#0d1220';
    ctx.font        = `bold ${cfg.r > 6 ? '8' : '7'}px Inter, sans-serif`;
    ctx.textAlign   = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(cfg.label, 0, 0.5);

    ctx.restore();

    // Thrower name (brief, only while fresh)
    if (ageTicks < 96 && this.showLabels) {
      const thrower = String(g.thrower || '');
      const shortName = thrower.length > 8 ? thrower.substring(0, 8) + '…' : thrower;
      ctx.save();
      ctx.globalAlpha = alpha * 0.8;
      ctx.font        = '9px Inter, sans-serif';
      ctx.textAlign   = 'center';
      ctx.fillStyle   = '#e8edf5';
      ctx.shadowColor = '#000';
      ctx.shadowBlur  = 3;
      ctx.fillText(shortName, pos.cx, pos.cy - cfg.r - 4);
      ctx.restore();
    }
  }
}
