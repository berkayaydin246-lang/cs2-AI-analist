/**
 * replay.js — Canvas 2D Replay Engine
 *
 * Renders player positions tick-by-tick on a CS2 radar background.
 * Features: trails, HP bars, yaw arrows, kill markers, bomb events, grenade icons.
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
const BOMB_FLASH_TICKS = 448;
const GRENADE_IMPACT_TICKS = 256;

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
    this.kills.sort((a, b) => (a.tick || 0) - (b.tick || 0));
    this.bombs.sort((a, b) => (a.tick || 0) - (b.tick || 0));
    this.grenades.sort((a, b) => (a.tick || 0) - (b.tick || 0));
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

    // Reset activation flags
    this.kills.forEach(k => { k._activated = false; });
    this.bombs.forEach(b => { b._activated = false; });
    this.grenades.forEach(g => { g._activated = false; });
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
      this.kills.forEach(k    => { k._activated = false; });
      this.bombs.forEach(b    => { b._activated = false; });
      this.grenades.forEach(g => { g._activated = false; });
    }
    this.currentFrame = idx;
    this._updateEventsForFrame();
    this._draw();
    if (this.onFrameChange) this.onFrameChange(this.currentFrame, this.frames.length);
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

    // Bomb events
    for (const b of this.bombs) {
      if (!b._activated && b.tick <= currentTick) {
        b._activated = true;
        if (b.x != null && b.y != null) {
          const pos = worldToCanvas(b.x, b.y, this.mapInfo);
          if (pos) this._activeBombEvents.push({ ...b, ...pos });
        }
      }
    }
    this._activeBombEvents = this._activeBombEvents.filter(b => (currentTick - (b.tick || 0)) <= BOMB_FLASH_TICKS);

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

    // Draw grenade icons
    for (const g of this._activeGrenades) {
      this._drawGrenade(ctx, g, currentTick);
    }

    // Draw kill markers
    for (const k of this._activeKills) {
      this._drawKillMarker(ctx, k, currentTick);
    }

    // Draw bomb events
    for (const b of this._activeBombEvents) {
      const ageTicks = currentTick - (b.tick || currentTick);
      if (ageTicks < BOMB_FLASH_TICKS) this._drawBombIcon(ctx, b, ageTicks);
    }

    // Draw players (dead first, alive on top)
    const dead  = players.filter(p => p.hp <= 0  && p._pos);
    const alive = players.filter(p => p.hp > 0   && p._pos);
    dead.forEach(p  => this._drawDeadPlayer(ctx, p));
    alive.forEach(p => this._drawAlivePlayer(ctx, p));
  }

  // ── Draw helpers ─────────────────────────────────────────────────────────────

  _drawAlivePlayer(ctx, p) {
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

    // Yaw direction arrow
    if (p.yaw != null) {
      const rad = (p.yaw * Math.PI) / 180;
      const ax  = Math.cos(rad) * (r + 8);
      const ay  = Math.sin(rad) * (r + 8);
      ctx.strokeStyle = 'rgba(255,255,255,0.85)';
      ctx.lineWidth   = 2;
      ctx.lineCap     = 'round';
      ctx.beginPath();
      ctx.moveTo(Math.cos(rad) * r, Math.sin(rad) * r);
      ctx.lineTo(ax, ay);
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
      ctx.fillText(shortName, cx, cy + r + 11);
      ctx.restore();
    }
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

  _drawBombIcon(ctx, b, ageTicks) {
    const alpha = Math.max(0, 1 - ageTicks / BOMB_FLASH_TICKS);
    const evt   = b.event || '';
    const isDefuse = evt.toLowerCase().includes('defus');
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

  _drawGrenade(ctx, g, currentTick) {
    const state = this._grenadePositionAtTick(g, currentTick);
    if (!state) return;
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
