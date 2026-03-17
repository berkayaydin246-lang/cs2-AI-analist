/**
 * app.js - CS2 AI Coach SPA
 * Handles navigation, upload flow, and all view rendering.
 * Depends on: api.js, replay.js
 */

// ── Toast notifications ──────────────────────────────────────────────────────

function showToast(message, { type = 'info', title, duration = 4500 } = {}) {
  const container = document.getElementById('toast-container');
  if (!container) return;
  const el = document.createElement('div');
  el.className = `toast toast-${type}`;
  el.innerHTML = (title ? `<div class="toast-title">${title}</div>` : '') + message;
  container.appendChild(el);
  const timer = setTimeout(() => _dismissToast(el), duration);
  el.addEventListener('click', () => { clearTimeout(timer); _dismissToast(el); });
}
function _dismissToast(el) {
  el.classList.add('hiding');
  el.addEventListener('animationend', () => el.remove());
}

// ── Global state ─────────────────────────────────────────────────────────────

// â”€â”€ Global state â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
const State = {
  demoId:       null,
  filename:     null,
  mapName:      null,
  totalRounds:  null,
  players:      [],
  teamAnalysis: null,
  analyses:     {},
  playerVisuals: {},
  playerSteamProfiles: {},
  /** {player_name → steamid64} resolved by the parser from demo tick data */
  playerSteamIds: {},
  playerSideFilter: 'both',
  currentPlayer: null,
  highlightsBundle: null,
  clipPlansBundle: null,
  clipsBundle: null,
  selectedClipId: null,
  selectedClipPlanIds: [],
  selectedReplayHighlight: null,
  replayFocusMode: false,
  replayHighlightWindow: null,
  replayRounds: [],
  currentReplayRound: null,
  currentReplayData: null,
  radarUrl:     null,
  renderMode:   'cs2_ingame_capture',
  renderModes:  null,
  renderPreset: '',
  renderPresets: null,
  ingameHealth: null,
  queueStatus:  null,
  queuePollHandle: null,
  currentMoment: null,
};

let replayEngine = null;
const steamDebugEnabled = (() => {
  try {
    const qp = new URLSearchParams(window.location.search || '');
    return qp.get('steamDebug') === '1';
  } catch {
    return false;
  }
})();

function isLikelyHttpUrl(value) {
  const v = String(value || '').trim();
  return /^https?:\/\//i.test(v);
}

function availableRenderModes() {
  const modes = Array.isArray(State.renderModes?.available)
    ? State.renderModes.available.filter(Boolean)
    : [];
  return modes.length ? modes : ['cs2_ingame_capture'];
}

function getRenderModeMeta(mode) {
  return State.renderModes?.all?.[mode] || null;
}

function renderModeDisplayLabel(mode, { short = false } = {}) {
  if (mode === 'cs2_ingame_capture') {
    return short ? 'In-Game' : 'CS2 In-Game Capture';
  }
  const meta = getRenderModeMeta(mode);
  return meta?.label || mode || 'Unknown Render Mode';
}

function renderModeOptionsMarkup() {
  return availableRenderModes().map((mode) => {
    const meta = getRenderModeMeta(mode);
    const suffix = meta?.deprecated ? ' (Deprecated)' : '';
    return `<option value="${mode}">${renderModeDisplayLabel(mode)}${suffix}</option>`;
  }).join('');
}

async function ensureRenderModesLoaded(force = false) {
  if (State.renderModes && !force) return State.renderModes;
  try {
    State.renderModes = await API.getRenderModes();
  } catch {
    State.renderModes = {
      available: ['cs2_ingame_capture'],
      all: {
        cs2_ingame_capture: {
          label: 'CS2 In-Game Capture',
          available: true,
          deprecated: false,
        },
      },
    };
  }

  const modes = availableRenderModes();
  if (!modes.includes(State.renderMode)) {
    State.renderMode = modes[0];
  }
  return State.renderModes;
}

// â”€â”€ DOM helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
const $  = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];

function el(tag, cls, html) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (html !== undefined) e.innerHTML = html;
  return e;
}

// Number formatters
function fmt(val, digits = 2) {
  const n = parseFloat(val);
  return isNaN(n) ? '-' : n.toFixed(digits);
}

// Value is already a percentage (0-100), just format it
function pctDirect(val) {
  const n = parseFloat(val);
  return isNaN(n) ? '-' : n.toFixed(1) + '%';
}

function toNum(v, fallback = 0) {
  const n = parseFloat(v);
  return Number.isFinite(n) ? n : fallback;
}

function clamp(n, min, max) {
  return Math.min(max, Math.max(min, n));
}

function statusClassForRatio(v, strong, ok) {
  if (!Number.isFinite(v)) return 'status-avg';
  if (v >= strong) return 'status-strong';
  if (v >= ok) return 'status-avg';
  return 'status-weak';
}

function statusClassForInverse(v, strong, ok) {
  if (!Number.isFinite(v)) return 'status-avg';
  if (v <= strong) return 'status-strong';
  if (v <= ok) return 'status-avg';
  return 'status-weak';
}

function metricStatus(metricKey, rawValue) {
  const v = toNum(rawValue, NaN);
  switch (metricKey) {
    case 'rating':
      return statusClassForRatio(v, 1.15, 1.0);
    case 'adr':
      return statusClassForRatio(v, 85, 65);
    case 'kd':
      return statusClassForRatio(v, 1.15, 0.95);
    case 'kast':
      return statusClassForRatio(v, 73, 65);
    case 'opening':
      return statusClassForRatio(v, 55, 45);
    case 'entry':
      return statusClassForRatio(v, 55, 45);
    case 'trading':
      return statusClassForRatio(v, 36, 22);
    case 'utility':
      return statusClassForRatio(v, 62, 40);
    case 'sniping':
      return statusClassForRatio(v, 32, 15);
    case 'clutch':
      return statusClassForRatio(v, 35, 20);
    case 'impact':
      return statusClassForRatio(v, 1.1, 0.9);
    case 'hs':
      return statusClassForRatio(v, 48, 35);
    case 'accuracy':
      return statusClassForRatio(v, 30, 20);
    case 'dpr':
      return statusClassForInverse(v, 0.62, 0.78);
    case 'kpr':
      return statusClassForRatio(v, 0.80, 0.65);
    default:
      return 'status-avg';
  }
}

function statusTextFromClass(cls) {
  if (cls === 'status-strong') return 'Strong';
  if (cls === 'status-weak') return 'Weak';
  return 'Average';
}

function scaleToPct(value, min, max, inverse = false) {
  if (!Number.isFinite(value) || max <= min) return 0;
  let p = ((value - min) / (max - min)) * 100;
  if (inverse) p = 100 - p;
  return clamp(p, 0, 100);
}

function metricBarHtml(valuePct, avgPct) {
  const v = clamp(toNum(valuePct, 0), 0, 100);
  const a = clamp(toNum(avgPct, 50), 0, 100);
  return `
    <div class="hero-metric-bar">
      <span class="hero-metric-bar-fill" style="width:${v}%"></span>
      <span class="hero-metric-bar-avg" style="left:${a}%"></span>
    </div>
  `;
}

function statusClassFromBenchmark(value, avgValue, direction = 'higher', band = 0.12, rangeMin = NaN, rangeMax = NaN) {
  const v = toNum(value, NaN);
  const a = toNum(avgValue, NaN);
  if (!Number.isFinite(v) || !Number.isFinite(a)) return 'status-avg';

  const hasRange = Number.isFinite(rangeMin) && Number.isFinite(rangeMax) && rangeMax > rangeMin;
  const span = hasRange ? (rangeMax - rangeMin) : Math.max(Math.abs(a), 1);
  const tolerance = Math.max(Math.abs(a) * band, span * 0.04, 0.01);

  const delta = direction === 'lower' ? (a - v) : (v - a);
  if (delta > tolerance) return 'status-strong';
  if (delta < -tolerance) return 'status-weak';
  return 'status-avg';
}

function metricGroupHtml(title, cls, cards) {
  if (!cards.length) return '';
  return `
    <div class="hero-metric-group">
      <div class="hero-metric-group-title ${cls}">${title}</div>
      <div class="hero-metrics-grid">${cards.join('')}</div>
    </div>
  `;
}

function metricCardHtml(metricKey, label, valueHtml, subHtml = '', barCfg = null, statusClassOverride = null) {
  const cls = statusClassOverride || metricStatus(metricKey, valueHtml);
  return `
    <div class="hero-metric-card ${cls}">
      <div class="hero-metric-top">
        <span class="hero-metric-label">${label}</span>
        <span class="hero-metric-state">${statusTextFromClass(cls)}</span>
      </div>
      <div class="hero-metric-value">${valueHtml}</div>
      ${subHtml ? `<div class="hero-metric-sub">${subHtml}</div>` : ''}
      ${barCfg ? metricBarHtml(barCfg.valuePct, barCfg.avgPct) : ''}
      <div class="hero-metric-accent"></div>
    </div>
  `;
}

function scoreToStatusClass(score) {
  const s = toNum(score, 0);
  if (s >= 72) return 'status-strong';
  if (s >= 50) return 'status-avg';
  return 'status-weak';
}

function wrapperRowHtml(label, valueText, valuePct, avgPct) {
  const vp = clamp(toNum(valuePct, 0), 0, 100);
  const ap = clamp(toNum(avgPct, 50), 0, 100);
  return `
    <div class="metric-row">
      <div class="metric-row-head">
        <span class="metric-row-label">${label}</span>
        <span class="metric-row-value">${valueText}</span>
      </div>
      <div class="metric-row-track">
        <span class="metric-row-fill" style="width:${vp}%"></span>
        <span class="metric-row-avg" style="left:${ap}%"></span>
      </div>
    </div>
  `;
}

function metricWrapperHtml(title, score, rows, openByDefault = false) {
  const cls = scoreToStatusClass(score);
  const safeScore = clamp(Math.round(toNum(score, 0)), 0, 100);
  const rowsHtml = rows.map(r => wrapperRowHtml(r.label, r.valueText, r.valuePct, r.avgPct)).join('');
  return `
    <details class="metric-wrapper ${cls}" ${openByDefault ? 'open' : ''}>
      <summary class="metric-wrapper-head">
        <div class="metric-wrapper-head-top">
          <span class="metric-wrapper-title">${title}</span>
          <span class="metric-wrapper-right">
            <span class="metric-wrapper-score">${safeScore} / 100</span>
            <span class="metric-wrapper-chip"></span>
            <span class="metric-wrapper-arrow">▾</span>
          </span>
        </div>
        <div class="metric-wrapper-head-bar">
          <span class="metric-wrapper-head-fill" style="width:${safeScore}%"></span>
        </div>
      </summary>
      <div class="metric-wrapper-body">${rowsHtml}</div>
    </details>
  `;
}

function detailBlockHtml(title, items) {
  const rows = items
    .map((item) => `
      <div class="detail-stat-row">
        <span class="detail-stat-label">${item.label}</span>
        <span class="detail-stat-value">${item.value}</span>
      </div>
    `)
    .join('');
  return `
    <div class="detail-block-card">
      <div class="detail-block-title">${title}</div>
      <div class="detail-block-body">${rows}</div>
    </div>
  `;
}

async function ensureHighlightsLoaded() {
  if (!State.demoId) return { summary: {}, highlights: [] };
  if (!State.highlightsBundle) {
    State.highlightsBundle = await API.getHighlights(State.demoId);
  }
  return State.highlightsBundle;
}

async function ensureClipPlansLoaded() {
  if (!State.demoId) return { summary: {}, clip_plans: [] };
  if (!State.clipPlansBundle) {
    State.clipPlansBundle = await API.getClipPlans(State.demoId);
  }
  return State.clipPlansBundle;
}

async function ensureClipsLoaded(options = {}) {
  if (!State.demoId && !options.global) return { summary: {}, clips: [] };
  if (!State.clipsBundle || options.force) {
    State.clipsBundle = options.global || !State.demoId
      ? await API.getAllClips()
      : await API.getRenderedClips(State.demoId);
  }
  return State.clipsBundle;
}

function formatDurationSeconds(value) {
  const total = Math.max(0, Math.round(toNum(value, 0)));
  const mins = Math.floor(total / 60);
  const secs = total % 60;
  return `${mins}:${String(secs).padStart(2, '0')}`;
}

function buildMomentFromHighlight(highlight, source = 'highlight', extras = {}) {
  if (!highlight) return null;
  return {
    source,
    type: 'highlight',
    title: highlight.title || highlightTypeLabel(highlight.type),
    description: highlight.description || '',
    highlightId: highlight.highlight_id || '',
    clipId: extras.clipId || '',
    clipPlanId: extras.clipPlanId || '',
    player: highlight.primary_player || '',
    side: highlight.side || '',
    roundNumber: toNum(highlight.round_number, 0),
    startTick: toNum(highlight.start_tick, 0),
    anchorTick: toNum(highlight.anchor_tick, 0),
    endTick: toNum(highlight.end_tick, 0),
    score: toNum(highlight.score, 0),
    demoId: extras.demoId || State.demoId || '',
    timestampMs: Date.now(),
  };
}

function setCurrentMomentFromHighlight(highlight, source = 'highlight', extras = {}) {
  const moment = buildMomentFromHighlight(highlight, source, extras);
  if (!moment) return null;
  State.currentMoment = moment;
  return moment;
}

function setCurrentMomentFromClip(clip, source = 'clip') {
  if (!clip) return null;
  const sourceHighlight = findHighlightById(clip?.source_highlight_id) || clipFallbackHighlight(clip);
  const moment = buildMomentFromHighlight(sourceHighlight, source, {
    clipId: clip.clip_id || '',
    clipPlanId: clip.clip_plan_id || '',
    demoId: clip.demo_id || State.demoId || '',
  });
  if (!moment) return null;
  moment.type = 'clip';
  moment.title = clip.title || moment.title;
  moment.description = clip.description || moment.description;
  State.currentMoment = moment;
  return moment;
}

function setCurrentMomentPlayer(playerName, source = 'player-focus') {
  const player = String(playerName || '').trim();
  if (!player) return;
  const prev = State.currentMoment || {};
  State.currentMoment = {
    ...prev,
    source,
    title: prev.title || `Player Focus: ${player}`,
    player,
    demoId: prev.demoId || State.demoId || '',
    timestampMs: Date.now(),
  };
}

function clearCurrentMoment() {
  State.currentMoment = null;
}

function ensureReplayReadyForMoment(moment = State.currentMoment) {
  if (!State.demoId) {
    showToast('Load and parse a demo before opening replay context.', { type: 'warning', title: 'Replay unavailable' });
    return false;
  }
  const momentDemo = String(moment?.demoId || '');
  if (momentDemo && String(State.demoId) !== momentDemo) {
    showToast('Current moment belongs to a different demo. Load that demo to open replay context.', {
      type: 'warning',
      title: 'Replay context mismatch',
    });
    return false;
  }
  return true;
}

function buildHighlightFromMoment(moment) {
  if (!moment) return null;
  return {
    highlight_id: moment.highlightId || moment.clipId || '',
    type: 'highlight',
    title: moment.title || 'Review Moment',
    description: moment.description || '',
    round_number: toNum(moment.roundNumber, 0),
    start_tick: toNum(moment.startTick, 0),
    anchor_tick: toNum(moment.anchorTick, 0),
    end_tick: toNum(moment.endTick, 0),
    primary_player: moment.player || '',
    side: moment.side || '',
    score: toNum(moment.score, 0),
  };
}

function createCurrentMomentSection(options = {}) {
  const section = el('section', 'section');
  section.appendChild(el('div', 'section-title', options.title || 'Current Review Moment'));
  if (options.subtitle) section.appendChild(el('div', 'section-subtitle', options.subtitle));

  const moment = State.currentMoment;
  if (!moment) {
    section.appendChild(el('div', 'empty-state', `
      <div class="empty-state-sub">${options.emptyHint || 'Pick a highlight or clip to keep context across replay, clips, and coaching.'}</div>
    `));
    return section;
  }

  const score = Math.round(clamp(toNum(moment.score, 0), 0, 1) * 100);
  const sourceLabel = String(moment.type || 'moment').toUpperCase();
  section.innerHTML += `
    <div class="clip-detail-hero">
      <div class="clip-detail-copy">
        <div class="clip-detail-eyebrow">${sourceLabel} CONTEXT</div>
        <h3>${moment.title || 'Review Moment'}</h3>
        <p>${moment.description || 'Use this context to jump between replay, clips, player stats, and coaching.'}</p>
        <div class="clip-detail-meta">
          ${moment.player ? `<span>${moment.player}</span>` : ''}
          ${moment.roundNumber ? `<span>Round ${moment.roundNumber}</span>` : ''}
          ${moment.anchorTick ? `<span>Tick ${moment.anchorTick}</span>` : ''}
          ${moment.side ? `<span>${moment.side}</span>` : ''}
          ${score ? `<span>${score}</span>` : ''}
        </div>
        <div class="clip-detail-actions">
          <button type="button" class="btn btn-primary btn-sm" data-moment-action="replay">Open in Replay</button>
          <button type="button" class="btn btn-secondary btn-sm" data-moment-action="clips">Open Clips</button>
          ${moment.clipId ? '<button type="button" class="btn btn-secondary btn-sm" data-moment-action="view-clip">View Clip</button>' : ''}
          ${moment.player ? '<button type="button" class="btn btn-secondary btn-sm" data-moment-action="player">Open Player</button>' : ''}
          ${moment.player ? '<button type="button" class="btn btn-secondary btn-sm" data-moment-action="coach">Coach Player</button>' : ''}
          <button type="button" class="btn btn-secondary btn-sm" data-moment-action="team">Team View</button>
          <button type="button" class="btn btn-secondary btn-sm" data-moment-action="clear">Clear</button>
        </div>
      </div>
    </div>
  `;

  section.querySelector('[data-moment-action="replay"]')?.addEventListener('click', async () => {
    if (!ensureReplayReadyForMoment(moment)) return;
    const highlight = buildHighlightFromMoment(moment);
    if (!toNum(highlight?.round_number, 0)) {
      showToast('This moment does not have replay round/tick data.', { type: 'warning', title: 'Replay unavailable' });
      return;
    }
    await jumpToHighlightReplay(highlight, { seekMode: 'start', focusWindow: true });
  });
  section.querySelector('[data-moment-action="clips"]')?.addEventListener('click', async () => {
    navigateTo('clips');
    await renderClipsView();
  });
  section.querySelector('[data-moment-action="view-clip"]')?.addEventListener('click', async () => {
    if (moment.clipId) await navigateToClip(moment.clipId);
  });
  section.querySelector('[data-moment-action="player"]')?.addEventListener('click', async () => {
    if (!moment.player) return;
    navigateTo('player');
    const sel = $('#player-select');
    if (sel) sel.value = moment.player;
    await analyzePlayer(moment.player);
  });
  section.querySelector('[data-moment-action="coach"]')?.addEventListener('click', () => {
    if (moment.player) navigateToCoaching(moment.player);
  });
  section.querySelector('[data-moment-action="team"]')?.addEventListener('click', async () => {
    navigateTo('team');
    await renderTeam();
  });
  section.querySelector('[data-moment-action="clear"]')?.addEventListener('click', () => {
    clearCurrentMoment();
    section.replaceWith(createCurrentMomentSection(options));
  });

  return section;
}

function highlightCategory(highlight) {
  const type = String(highlight?.type || '').toLowerCase();
  if (type.includes('clutch')) return 'clutch';
  if (type.includes('grenade') || type.includes('flash') || type.includes('bomb')) return 'utility';
  return 'kill';
}

function highlightTypeLabel(type) {
  const value = String(type || '').replace(/_/g, ' ').trim();
  return value ? value.replace(/\b\w/g, (m) => m.toUpperCase()) : 'Highlight';
}

function formatHighlightTiming(highlight) {
  const roundNum = toNum(highlight?.round_number, 0);
  const anchorTick = toNum(highlight?.anchor_tick, 0);
  return `Round ${roundNum} · Tick ${anchorTick}`;
}

function filterHighlights(highlights, mode = 'all') {
  const rows = Array.isArray(highlights) ? highlights.slice() : [];
  const filtered = mode === 'all'
    ? rows
    : rows.filter((item) => highlightCategory(item) === mode);
  return filtered.sort((a, b) => {
    const scoreDiff = toNum(b?.score, 0) - toNum(a?.score, 0);
    if (Math.abs(scoreDiff) > 0.0001) return scoreDiff;
    const roundDiff = toNum(a?.round_number, 0) - toNum(b?.round_number, 0);
    if (roundDiff !== 0) return roundDiff;
    return toNum(a?.anchor_tick, 0) - toNum(b?.anchor_tick, 0);
  });
}

function findNearestReplayFrameIndex(frames, targetTick) {
  if (!Array.isArray(frames) || !frames.length) return 0;
  const tick = toNum(targetTick, 0);
  let bestIdx = 0;
  let bestDistance = Number.POSITIVE_INFINITY;
  frames.forEach((frame, idx) => {
    const distance = Math.abs(toNum(frame?.tick, 0) - tick);
    if (distance < bestDistance) {
      bestDistance = distance;
      bestIdx = idx;
    }
  });
  return bestIdx;
}

function replaySeekTickForHighlight(highlight, mode = 'start') {
  const startTick = toNum(highlight?.start_tick, 0);
  const anchorTick = toNum(highlight?.anchor_tick, 0);
  const endTick = toNum(highlight?.end_tick, 0);
  if (mode === 'anchor') return anchorTick || startTick || endTick || 0;
  if (mode === 'end') return endTick || anchorTick || startTick || 0;
  return startTick || anchorTick || endTick || 0;
}

function getMatchHighlights() {
  return Array.isArray(State.highlightsBundle?.highlights) ? State.highlightsBundle.highlights : [];
}

function getRoundHighlights(roundNum) {
  const rn = toNum(roundNum, 0);
  return getMatchHighlights()
    .filter((item) => toNum(item?.round_number, 0) === rn)
    .sort((a, b) => {
      const tickDiff = toNum(a?.anchor_tick, 0) - toNum(b?.anchor_tick, 0);
      if (tickDiff !== 0) return tickDiff;
      return toNum(b?.score, 0) - toNum(a?.score, 0);
    });
}

function getSelectedReplayHighlightForRound(roundNum) {
  const hl = State.selectedReplayHighlight;
  if (!hl) return null;
  return toNum(hl.round_number, 0) === toNum(roundNum, 0) ? hl : null;
}

function setSelectedReplayHighlight(highlight, options = {}) {
  if (!highlight) {
    State.selectedReplayHighlight = null;
    State.replayFocusMode = false;
    State.replayHighlightWindow = null;
    return;
  }
  State.selectedReplayHighlight = { ...highlight };
  State.replayFocusMode = options.focusWindow !== false;
  State.replayHighlightWindow = null;
}

function clearReplayHighlightSelection() {
  State.selectedReplayHighlight = null;
  State.replayFocusMode = false;
  State.replayHighlightWindow = null;
  replayEngine?.clearPlaybackWindow?.();
}

function buildReplayHighlightFrameWindow(data, highlight) {
  const frames = Array.isArray(data?.frames) ? data.frames : [];
  if (!frames.length || !highlight) return null;
  const startTick = replaySeekTickForHighlight(highlight, 'start');
  const anchorTick = replaySeekTickForHighlight(highlight, 'anchor');
  const endTickRaw = replaySeekTickForHighlight(highlight, 'end');
  const endTick = Math.max(anchorTick, endTickRaw || anchorTick);
  const startFrame = findNearestReplayFrameIndex(frames, startTick);
  const anchorFrame = findNearestReplayFrameIndex(frames, anchorTick);
  const endFrame = Math.max(anchorFrame, findNearestReplayFrameIndex(frames, endTick));
  return { startTick, anchorTick, endTick, startFrame, anchorFrame, endFrame };
}

function describeReplayHighlightProgress(currentTick, windowInfo) {
  if (!windowInfo) return 'Round playback';
  if (currentTick < windowInfo.startTick) return 'Before highlight window';
  if (currentTick > windowInfo.endTick) return 'After highlight window';
  if (Math.abs(currentTick - windowInfo.anchorTick) <= 32) return 'At highlight anchor';
  if (currentTick < windowInfo.anchorTick) return 'Approaching highlight anchor';
  return 'Inside highlight window';
}

function collectReplayHighlightEvents(data, windowInfo) {
  if (!data || !windowInfo) return [];
  const startTick = Math.max(0, windowInfo.startTick - 96);
  const endTick = windowInfo.endTick + 128;
  const items = [];

  (data.kills || []).forEach((kill) => {
    const tick = toNum(kill?.tick, 0);
    if (tick < startTick || tick > endTick) return;
    items.push({
      tick,
      label: `${kill.attacker || 'Unknown'} -> ${kill.victim || 'Unknown'}${kill.weapon ? ` (${kill.weapon})` : ''}`,
    });
  });

  (data.grenades || []).forEach((nade) => {
    const tick = toNum(nade?.tick, 0);
    if (tick < startTick || tick > endTick) return;
    items.push({
      tick,
      label: `${nade.thrower || 'Unknown'} threw ${highlightTypeLabel(nade.type || 'grenade')}`,
    });
  });

  (data.bombs || []).forEach((bomb) => {
    const tick = toNum(bomb?.tick, 0);
    if (tick < startTick || tick > endTick) return;
    items.push({
      tick,
      label: `${bomb.player || 'Unknown'} ${String(bomb.event || 'bomb event').toLowerCase()}`,
    });
  });

  return items
    .sort((a, b) => a.tick - b.tick)
    .slice(0, 5);
}

function renderReplayTimelineMarkers(data) {
  const layer = $('#rp-highlight-timeline');
  if (!layer) return;
  const roundNum = State.currentReplayRound;
  const roundHighlights = getRoundHighlights(roundNum);
  const frames = Array.isArray(data?.frames) ? data.frames : [];
  if (!frames.length || !roundHighlights.length) {
    layer.classList.add('hidden');
    layer.innerHTML = '';
    return;
  }

  const tickRange = Array.isArray(data?.tick_range) ? data.tick_range : [frames[0]?.tick || 0, frames[frames.length - 1]?.tick || 0];
  const roundStart = toNum(tickRange[0], 0);
  const roundEnd = Math.max(roundStart + 1, toNum(tickRange[1], roundStart + 1));
  const span = Math.max(1, roundEnd - roundStart);
  const selected = getSelectedReplayHighlightForRound(roundNum);
  const windowInfo = selected ? (State.replayHighlightWindow || buildReplayHighlightFrameWindow(data, selected)) : null;

  layer.classList.remove('hidden');
  layer.innerHTML = '';

  if (windowInfo) {
    const range = el('div', 'replay-highlight-range');
    const leftPct = clamp(((windowInfo.startTick - roundStart) / span) * 100, 0, 100);
    const widthPct = clamp(((windowInfo.endTick - windowInfo.startTick) / span) * 100, 0.6, 100);
    range.style.left = `${leftPct}%`;
    range.style.width = `${widthPct}%`;
    layer.appendChild(range);
  }

  roundHighlights.forEach((highlight) => {
    const marker = el('button', 'replay-highlight-marker');
    const anchorPct = clamp(((toNum(highlight?.anchor_tick, roundStart) - roundStart) / span) * 100, 0, 100);
    marker.type = 'button';
    marker.style.left = `${anchorPct}%`;
    marker.title = `${highlight.title || highlightTypeLabel(highlight.type)} • Round ${highlight.round_number}`;
    marker.dataset.highlightId = highlight.highlight_id || '';
    if (selected && selected.highlight_id === highlight.highlight_id) {
      marker.classList.add('active');
    }
    marker.addEventListener('click', async () => {
      await jumpToHighlightReplay(highlight, { navigate: false, seekMode: 'anchor', focusWindow: true });
    });
    layer.appendChild(marker);
  });
}

function renderReplayHighlightContext(data) {
  const container = $('#replay-highlight-context');
  if (!container) return;
  const roundNum = State.currentReplayRound;
  const roundHighlights = getRoundHighlights(roundNum);
  const selected = getSelectedReplayHighlightForRound(roundNum);
  if (!selected && !roundHighlights.length) {
    container.classList.add('hidden');
    container.innerHTML = '';
    return;
  }

  const windowInfo = selected ? (State.replayHighlightWindow || buildReplayHighlightFrameWindow(data, selected)) : null;
  if (selected) setCurrentMomentFromHighlight(selected, 'replay-context');
  if (selected && windowInfo) State.replayHighlightWindow = windowInfo;
  const currentTick = replayEngine?.getFrameTick?.() ?? toNum(data?.frames?.[0]?.tick, 0);
  const progressText = describeReplayHighlightProgress(currentTick, windowInfo);
  const nearbyEvents = collectReplayHighlightEvents(data, windowInfo);

  container.classList.remove('hidden');
  if (!selected) {
    container.innerHTML = `
      <div class="replay-highlight-context-head">
        <div>
          <div class="replay-highlight-kicker">Round Highlights</div>
          <div class="replay-highlight-title">Detected moments in Round ${roundNum}</div>
          <div class="replay-highlight-desc">Select a highlight to focus replay playback around its tick window.</div>
        </div>
      </div>
      <div class="replay-highlight-chip-row">
        ${roundHighlights.map((item) => `
          <button type="button" class="replay-highlight-chip" data-highlight-id="${item.highlight_id || ''}">
            <span>${item.title || highlightTypeLabel(item.type)}</span>
            <span class="text-muted">Tick ${toNum(item.anchor_tick, 0)}</span>
          </button>
        `).join('')}
      </div>
    `;
  } else {
    container.innerHTML = `
      <div class="replay-highlight-context-head">
        <div>
          <div class="replay-highlight-kicker">${highlightTypeLabel(selected.type)}</div>
          <div class="replay-highlight-title">${selected.title || highlightTypeLabel(selected.type)} - Round ${selected.round_number}</div>
          <div class="replay-highlight-desc">${selected.description || 'Selected highlight moment'}</div>
        </div>
        <div class="replay-highlight-score">${Math.round(clamp(toNum(selected.score, 0), 0, 1) * 100)}</div>
      </div>
      <div class="replay-highlight-meta-row">
        <span>${selected.primary_player || 'Unknown player'}</span>
        <span>${selected.side || 'N/A'}</span>
        <span id="rp-highlight-status">${progressText}</span>
        ${State.replayFocusMode ? '<span class="replay-highlight-focus-pill">Focus Window</span>' : ''}
      </div>
      <div class="replay-highlight-window-grid">
        <div class="replay-highlight-window-stat"><label>Start</label><strong>${windowInfo?.startTick || '-'}</strong></div>
        <div class="replay-highlight-window-stat"><label>Anchor</label><strong>${windowInfo?.anchorTick || '-'}</strong></div>
        <div class="replay-highlight-window-stat"><label>End</label><strong>${windowInfo?.endTick || '-'}</strong></div>
      </div>
      <div class="replay-highlight-actions">
        <button type="button" class="btn btn-primary btn-sm" data-rp-action="play-window">Play Highlight</button>
        <button type="button" class="btn btn-secondary btn-sm" data-rp-action="seek-start">Jump to Start</button>
        <button type="button" class="btn btn-secondary btn-sm" data-rp-action="seek-anchor">Jump to Anchor</button>
        <button type="button" class="btn btn-secondary btn-sm" data-rp-action="toggle-focus">${State.replayFocusMode ? 'Disable Focus' : 'Enable Focus'}</button>
        ${(() => {
          const sc = findClipForHighlight(selected.highlight_id);
          const cp = findClipPlanForHighlight(selected.highlight_id);
          if (sc) return '<button type="button" class="btn btn-secondary btn-sm" data-rp-action="view-clip">View Clip</button>';
          if (cp) return '<button type="button" class="btn btn-secondary btn-sm" data-rp-action="render-clip">Render Clip</button>';
          return '';
        })()}
        ${selected.primary_player ? '<button type="button" class="btn btn-secondary btn-sm" data-rp-action="coach-player">Coach Player</button>' : ''}
        <button type="button" class="btn btn-secondary btn-sm" data-rp-action="clear-highlight">Clear</button>
      </div>
      ${nearbyEvents.length ? `
        <div class="replay-highlight-events">
          ${nearbyEvents.map((item) => `
            <div class="replay-highlight-event">
              <span class="text-mono">${item.tick}</span>
              <span>${item.label}</span>
            </div>
          `).join('')}
        </div>
      ` : ''}
      <div class="replay-highlight-chip-row">
        ${roundHighlights.map((item) => `
          <button type="button" class="replay-highlight-chip ${item.highlight_id === selected.highlight_id ? 'active' : ''}" data-highlight-id="${item.highlight_id || ''}">
            <span>${item.title || highlightTypeLabel(item.type)}</span>
            <span class="text-muted">Tick ${toNum(item.anchor_tick, 0)}</span>
          </button>
        `).join('')}
      </div>
    `;
  }

  container.querySelectorAll('.replay-highlight-chip').forEach((node) => {
    node.addEventListener('click', async () => {
      const target = roundHighlights.find((item) => (item.highlight_id || '') === (node.dataset.highlightId || ''));
      if (!target) return;
      await jumpToHighlightReplay(target, { navigate: false, seekMode: 'start', focusWindow: true });
    });
  });

  container.querySelector('[data-rp-action="play-window"]')?.addEventListener('click', () => {
    if (!replayEngine || !windowInfo) return;
    State.replayFocusMode = true;
    replayEngine.playWindow(windowInfo.startFrame, windowInfo.endFrame, { seekToStart: true });
    renderReplayHighlightContext(data);
  });
  container.querySelector('[data-rp-action="seek-start"]')?.addEventListener('click', () => {
    if (!replayEngine || !windowInfo) return;
    replayEngine.pause();
    replayEngine.seekTo(windowInfo.startFrame);
  });
  container.querySelector('[data-rp-action="seek-anchor"]')?.addEventListener('click', () => {
    if (!replayEngine || !windowInfo) return;
    replayEngine.pause();
    replayEngine.seekTo(windowInfo.anchorFrame);
  });
  container.querySelector('[data-rp-action="toggle-focus"]')?.addEventListener('click', () => {
    State.replayFocusMode = !State.replayFocusMode;
    if (replayEngine && windowInfo) {
      if (State.replayFocusMode) replayEngine.setPlaybackWindow(windowInfo.startFrame, windowInfo.endFrame);
      else replayEngine.clearPlaybackWindow();
    }
    renderReplayHighlightContext(data);
  });
  container.querySelector('[data-rp-action="clear-highlight"]')?.addEventListener('click', () => {
    clearReplayHighlightSelection();
    renderReplayHighlightContext(data);
    renderReplayTimelineMarkers(data);
  });
  container.querySelector('[data-rp-action="view-clip"]')?.addEventListener('click', async () => {
    const sc = findClipForHighlight(selected.highlight_id);
    if (sc) await navigateToClip(sc.clip_id);
  });
  container.querySelector('[data-rp-action="render-clip"]')?.addEventListener('click', async () => {
    const cp = findClipPlanForHighlight(selected.highlight_id);
    if (!cp || !State.demoId) return;
    try {
      await API.queueEnqueue(State.demoId, cp.clip_plan_id, State.renderMode, State.renderPreset);
    } catch { /* silent */ }
    navigateTo('clips');
    await renderClipsView();
  });
  container.querySelector('[data-rp-action="coach-player"]')?.addEventListener('click', () => {
    if (selected.primary_player) navigateToCoaching(selected.primary_player);
  });
}

function updateReplayHighlightLiveState(data, frameIdx) {
  const status = $('#rp-highlight-status');
  if (!status || !State.replayHighlightWindow) return;
  const frame = data?.frames?.[frameIdx];
  if (!frame) return;
  status.textContent = describeReplayHighlightProgress(toNum(frame.tick, 0), State.replayHighlightWindow);
}

function applyReplayHighlightSelection(data, options = {}) {
  const selected = getSelectedReplayHighlightForRound(State.currentReplayRound);
  if (!selected) {
    State.replayHighlightWindow = null;
    replayEngine?.clearPlaybackWindow?.();
    renderReplayHighlightContext(data);
    renderReplayTimelineMarkers(data);
    return null;
  }

  const windowInfo = buildReplayHighlightFrameWindow(data, selected);
  State.replayHighlightWindow = windowInfo;
  if (replayEngine && windowInfo) {
    if (State.replayFocusMode) replayEngine.setPlaybackWindow(windowInfo.startFrame, windowInfo.endFrame);
    else replayEngine.clearPlaybackWindow();
  }
  renderReplayHighlightContext(data);
  renderReplayTimelineMarkers(data);

  if (!replayEngine || !windowInfo) return windowInfo;
  if (options.seekMode) {
    const frameIndex =
      options.seekMode === 'anchor' ? windowInfo.anchorFrame :
      options.seekMode === 'end' ? windowInfo.endFrame :
      windowInfo.startFrame;
    replayEngine.pause();
    replayEngine.seekTo(frameIndex);
  }
  if (options.autoplay) {
    replayEngine.playWindow(windowInfo.startFrame, windowInfo.endFrame, { seekToStart: options.seekMode !== 'anchor' });
  }
  return windowInfo;
}

function createHighlightCard(highlight, options = {}) {
  const card = el('article', 'highlight-card');
  const category = highlightCategory(highlight);
  const tags = Array.isArray(highlight.tags) ? highlight.tags.slice(0, 4) : [];
  const score = Math.round(clamp(toNum(highlight.score, 0), 0, 1) * 100);
  const primaryPlayer = highlight.primary_player || 'Unknown';
  const showPlayer = options.showPlayer !== false;

  card.innerHTML = `
    <div class="highlight-card-top">
      <div class="highlight-card-title-wrap">
        <span class="highlight-type-pill type-${category}">${highlightTypeLabel(highlight.type)}</span>
        <h3 class="highlight-card-title">${highlight.title || highlightTypeLabel(highlight.type)}</h3>
      </div>
      <div class="highlight-score-pill">${score}</div>
    </div>
    <div class="highlight-card-desc">${highlight.description || 'Detected match moment'}</div>
    <div class="highlight-card-meta">
      ${showPlayer ? `<span>${primaryPlayer}</span>` : ''}
      <span>${formatHighlightTiming(highlight)}</span>
      <span>${highlight.side || 'N/A'}</span>
    </div>
    ${tags.length ? `<div class="highlight-tags">${tags.map((tag) => `<span class="highlight-tag">${tag}</span>`).join('')}</div>` : ''}
    <div class="highlight-card-actions">
      <button class="btn btn-primary btn-sm highlight-watch-btn" type="button">Watch in Replay</button>
      ${showPlayer ? '<button class="btn btn-secondary btn-sm highlight-player-btn" type="button">Open Player</button>' : ''}
      ${(() => {
        const savedClip = findClipForHighlight(highlight.highlight_id);
        const clipPlan = findClipPlanForHighlight(highlight.highlight_id);
        if (savedClip) return '<button class="btn btn-secondary btn-sm highlight-viewclip-btn" type="button">View Clip</button>';
        if (clipPlan) return '<button class="btn btn-secondary btn-sm highlight-renderclip-btn" type="button">Render Clip</button>';
        return '';
      })()}
      ${showPlayer ? '<button class="btn btn-secondary btn-sm highlight-coach-btn" type="button">Coach Player</button>' : ''}
    </div>
    ${(() => {
      const savedClip = findClipForHighlight(highlight.highlight_id);
      const clipPlan = findClipPlanForHighlight(highlight.highlight_id);
      if (savedClip) return '<span class="highlight-clip-badge clip-rendered">Clip Rendered</span>';
      if (clipPlan) return '<span class="highlight-clip-badge clip-planned">Clip Planned</span>';
      return '';
    })()}
  `;

  card.querySelector('.highlight-watch-btn')?.addEventListener('click', () => {
    setCurrentMomentFromHighlight(highlight, 'highlight-watch');
    jumpToHighlightReplay(highlight);
  });
  card.querySelector('.highlight-player-btn')?.addEventListener('click', async () => {
    const playerName = highlight.primary_player;
    if (!playerName) return;
    setCurrentMomentFromHighlight(highlight, 'highlight-player');
    navigateTo('player');
    $('#player-select').value = playerName;
    await analyzePlayer(playerName);
  });
  card.querySelector('.highlight-viewclip-btn')?.addEventListener('click', async () => {
    const savedClip = findClipForHighlight(highlight.highlight_id);
    if (savedClip) {
      setCurrentMomentFromClip(savedClip, 'highlight-view-clip');
      await navigateToClip(savedClip.clip_id);
    }
  });
  card.querySelector('.highlight-renderclip-btn')?.addEventListener('click', async () => {
    const clipPlan = findClipPlanForHighlight(highlight.highlight_id);
    if (!clipPlan || !State.demoId) return;
    setCurrentMomentFromHighlight(highlight, 'highlight-render', { clipPlanId: clipPlan.clip_plan_id || '' });
    try {
      await API.queueEnqueue(State.demoId, clipPlan.clip_plan_id, State.renderMode, State.renderPreset);
    } catch { /* silent */ }
    navigateTo('clips');
    await renderClipsView();
  });
  card.querySelector('.highlight-coach-btn')?.addEventListener('click', () => {
    if (highlight.primary_player) {
      setCurrentMomentFromHighlight(highlight, 'highlight-coach');
      navigateToCoaching(highlight.primary_player);
    }
  });
  return card;
}

function renderHighlightsSection({ title, subtitle = '', highlights = [], emptyMessage = 'No highlights detected.', showPlayer = true }) {
  const section = el('section', 'section highlight-section');
  section.appendChild(el('div', 'section-title', title));
  if (subtitle) section.appendChild(el('div', 'section-subtitle', subtitle));

  const toolbar = el('div', 'highlight-toolbar');
  toolbar.innerHTML = `
    <div class="highlight-filter-group">
      <button class="highlight-filter-btn active" data-filter="all" type="button">All</button>
      <button class="highlight-filter-btn" data-filter="kill" type="button">Kills</button>
      <button class="highlight-filter-btn" data-filter="clutch" type="button">Clutches</button>
      <button class="highlight-filter-btn" data-filter="utility" type="button">Utility</button>
    </div>
    <div class="highlight-toolbar-meta">${highlights.length} moments</div>
  `;
  section.appendChild(toolbar);

  const list = el('div', 'highlight-list');
  section.appendChild(list);

  const renderList = (mode) => {
    list.innerHTML = '';
    const rows = filterHighlights(highlights, mode);
    if (!rows.length) {
      list.innerHTML = `<div class="empty-state"><div class="empty-state-sub">${emptyMessage}</div></div>`;
      return;
    }
    rows.forEach((item) => list.appendChild(createHighlightCard(item, { showPlayer })));
  };

  toolbar.querySelectorAll('.highlight-filter-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      toolbar.querySelectorAll('.highlight-filter-btn').forEach((node) => node.classList.remove('active'));
      btn.classList.add('active');
      renderList(btn.dataset.filter || 'all');
    });
  });

  renderList('all');
  return section;
}

function findHighlightById(highlightId) {
  if (!highlightId) return null;
  return getMatchHighlights().find((item) => String(item?.highlight_id || '') === String(highlightId)) || null;
}

/** Find a saved clip whose source_highlight_id matches the given highlight */
function findClipForHighlight(highlightId) {
  if (!highlightId) return null;
  const clips = Array.isArray(State.clipsBundle?.clips) ? State.clipsBundle.clips : [];
  return clips.find((c) => String(c?.source_highlight_id || '') === String(highlightId)) || null;
}

/** Find a clip plan whose source_highlight_id matches the given highlight */
function findClipPlanForHighlight(highlightId) {
  if (!highlightId) return null;
  const plans = Array.isArray(State.clipPlansBundle?.clip_plans) ? State.clipPlansBundle.clip_plans : [];
  return plans.find((p) => String(p?.source_highlight_id || '') === String(highlightId)) || null;
}

/** Navigate to coaching view with a pre-selected player */
function navigateToCoaching(playerName) {
  if (playerName) {
    setCurrentMomentPlayer(playerName, 'coaching-target');
  }
  navigateTo('coaching');
  renderCoachingView();
  if (playerName) {
    const sel = $('#coaching-player-select');
    if (sel) sel.value = playerName;
  }
}

/** Navigate to clips view and select a specific clip */
async function navigateToClip(clipId) {
  State.selectedClipId = clipId || null;
  const clips = Array.isArray(State.clipsBundle?.clips) ? State.clipsBundle.clips : [];
  const selected = clips.find((item) => item.clip_id === State.selectedClipId);
  if (selected) setCurrentMomentFromClip(selected, 'clip-nav');
  navigateTo('clips');
  await renderClipsView();
}

function clipTypeLabel(value) {
  const label = String(value || 'clip').replace(/_/g, ' ').trim();
  return label ? label.replace(/\b\w/g, (m) => m.toUpperCase()) : 'Clip';
}

function filterAndSortClips(clips, filters = {}) {
  const typeFilter = String(filters.type || 'all');
  const playerFilter = String(filters.player || 'all');
  const sortMode = String(filters.sort || 'newest');

  let rows = Array.isArray(clips) ? clips.slice() : [];
  if (typeFilter !== 'all') {
    rows = rows.filter((clip) => String(clip?.clip_type || '') === typeFilter);
  }
  if (playerFilter !== 'all') {
    rows = rows.filter((clip) => String(clip?.primary_player || '') === playerFilter);
  }

  rows.sort((a, b) => {
    if (sortMode === 'score') {
      const scoreDiff = toNum(b?.score, 0) - toNum(a?.score, 0);
      if (Math.abs(scoreDiff) > 0.0001) return scoreDiff;
    } else if (sortMode === 'round') {
      const roundDiff = toNum(a?.round_number, 0) - toNum(b?.round_number, 0);
      if (roundDiff !== 0) return roundDiff;
    }
    return String(b?.created_at || '').localeCompare(String(a?.created_at || ''));
  });
  return rows;
}

function clipFallbackHighlight(clip) {
  if (!clip) return null;
  return {
    highlight_id: clip.source_highlight_id || clip.clip_id,
    type: clip.clip_type || 'highlight',
    title: clip.title || 'Clip',
    description: clip.description || '',
    round_number: toNum(clip.round_number, 0),
    start_tick: toNum(clip.start_tick, 0),
    anchor_tick: toNum(clip.anchor_tick, 0),
    end_tick: toNum(clip.end_tick, 0),
    primary_player: clip.primary_player || '',
    involved_players: Array.isArray(clip.involved_players) ? clip.involved_players : [],
    side: clip.side || '',
    score: toNum(clip.score, 0),
    tags: Array.isArray(clip.tags) ? clip.tags : [],
  };
}

async function openClipInReplay(clip) {
  setCurrentMomentFromClip(clip, 'clip-replay');
  if (!ensureReplayReadyForMoment(State.currentMoment)) return;
  const sourceHighlight = findHighlightById(clip?.source_highlight_id) || clipFallbackHighlight(clip);
  if (!sourceHighlight) return;
  await jumpToHighlightReplay(sourceHighlight, { seekMode: 'start', focusWindow: true });
}

function createClipCard(clip, selected = false) {
  const card = el('article', `clip-card${selected ? ' active' : ''}`);
  const score = Math.round(clamp(toNum(clip?.score, 0), 0, 1) * 100);
  const duration = formatDurationSeconds(clip?.duration_s);
  const thumbUrl = clip?.thumbnail_url || '';
  const metaTags = Array.isArray(clip?.tags) ? clip.tags.slice(0, 4) : [];
  const status = String(clip?.status || 'unknown');

  card.innerHTML = `
    <div class="clip-card-thumb-wrap">
      ${thumbUrl
        ? `<img class="clip-card-thumb" src="${thumbUrl}" alt="${clip?.title || 'Clip thumbnail'}" />`
        : '<div class="clip-card-thumb clip-card-thumb-fallback">No Thumbnail</div>'}
      <span class="clip-card-duration">${duration}</span>
    </div>
    <div class="clip-card-body">
      <div class="clip-card-top">
        <div>
          <div class="clip-card-title">${clip?.title || 'Clip'}</div>
          <div class="clip-card-sub">${clip?.primary_player || 'Unknown'} · Round ${toNum(clip?.round_number, 0)} · ${clip?.demo_id || 'saved'}</div>
        </div>
        <div class="clip-card-score">${score}</div>
      </div>
      <div class="clip-card-meta">
        <span>${clipTypeLabel(clip?.clip_type)}</span>
        <span>${clipTypeLabel(clip?.render_mode)}</span>
        <span class="clip-card-status ${status}">${status}</span>
      </div>
      ${metaTags.length ? `<div class="clip-tags">${metaTags.map((tag) => `<span class="clip-tag">${tag}</span>`).join('')}</div>` : ''}
      <div class="clip-card-actions">
        <button type="button" class="btn btn-primary btn-sm" data-clip-action="select">Preview</button>
        <button type="button" class="btn btn-secondary btn-sm" data-clip-action="replay">Open in Replay</button>
      </div>
    </div>
  `;
  return card;
}

// ── Stage label helpers ──────────────────────────────────────────────────────

const STAGE_LABEL_MAP = {
  validating_environment: 'Checking env',
  launching_cs2: 'Launching CS2',
  loading_demo: 'Loading demo',
  preparing_playback: 'Preparing',
  seeking_target: 'Seeking',
  configuring_camera: 'Configuring',
  starting_capture: 'Starting OBS',
  recording: 'Recording',
  stopping_capture: 'Stopping',
  finalizing: 'Finalizing',
  starting: 'Starting',
  done: 'Done',
};

function stageDisplayLabel(stage) {
  return STAGE_LABEL_MAP[stage] || stage.replace(/_/g, ' ');
}

function normalizeQueueStatus(status) {
  const s = String(status || '').toLowerCase();
  if (s === 'pending') return 'queued';
  if (s === 'processing') return 'running';
  return s;
}

function getPlanJobPriority(j) {
  const activeStatuses = ['running', 'validating', 'preparing', 'recording', 'finalizing'];
  const status = normalizeQueueStatus(j?.status || j?.legacy_status);
  if (activeStatuses.includes(status)) return 3;
  if (status === 'queued') return 2;
  if (status === 'failed') return 1;
  return 0;
}

function isClipPlanSelected(clipPlanId) {
  return State.selectedClipPlanIds.includes(String(clipPlanId || ''));
}

function setClipPlanSelected(clipPlanId, selected) {
  const id = String(clipPlanId || '');
  if (!id) return;
  if (selected) {
    if (!State.selectedClipPlanIds.includes(id)) {
      State.selectedClipPlanIds = [...State.selectedClipPlanIds, id];
    }
    return;
  }
  State.selectedClipPlanIds = State.selectedClipPlanIds.filter((value) => value !== id);
}

function createClipPlanRow(plan, renderedClipId = '', queueJob = null) {
  const isSelected = isClipPlanSelected(plan?.clip_plan_id);
  const row = el('article', `clip-plan-row${isSelected ? ' is-selected' : ''}`);
  const score = Math.round(clamp(toNum(plan?.score, 0), 0, 1) * 100);
  const existing = renderedClipId ? `<span class="clip-plan-state ready">Rendered</span>` : '<span class="clip-plan-state">Planned</span>';
  const modeLabel = renderModeDisplayLabel(State.renderMode, { short: true });
  const planMeta = plan?.metadata || {};
  const presetInfo = planMeta.render_preset || {};
  const planProfile = planMeta.planning_profile || {};
  const presetLabel = presetInfo.name ? presetInfo.name.replace(/_/g, ' ') : '';
  const povHint = planProfile.pov_strategy || '';
  const qualityTier = presetInfo.quality_tier || '';

  // Queue state badge + button state
  const activeStatuses = ['running', 'validating', 'preparing', 'recording', 'finalizing'];
  let queueBadge = '';
  let renderBtnDisabled = false;
  let renderBtnLabel = renderedClipId ? 'Re-render' : 'Render Clip';
  if (queueJob) {
    const qStatus = normalizeQueueStatus(queueJob.status || queueJob.legacy_status);
    if (activeStatuses.includes(qStatus)) {
      const stageLabel = queueJob.progress_stage ? stageDisplayLabel(queueJob.progress_stage) : 'Active';
      queueBadge = `<span class="clip-plan-queue-badge queue-badge-active">${stageLabel}&hellip;</span>`;
      renderBtnDisabled = true;
      renderBtnLabel = 'Rendering\u2026';
    } else if (qStatus === 'queued') {
      queueBadge = `<span class="clip-plan-queue-badge queue-badge-queued">Queued</span>`;
      renderBtnDisabled = true;
      renderBtnLabel = 'Queued';
    } else if (qStatus === 'failed') {
      queueBadge = `<span class="clip-plan-queue-badge queue-badge-failed" title="${queueJob.error || 'Render failed'}">Failed</span>`;
    }
  }

  row.innerHTML = `
    <div class="clip-plan-head">
      <label class="clip-plan-select" title="Select for batch queueing">
        <input type="checkbox" data-plan-action="select" ${isSelected ? 'checked' : ''} />
        <span>Select</span>
      </label>
      <div class="clip-plan-copy">
        <div class="clip-plan-title">${plan?.title || 'Clip Plan'}</div>
        <div class="clip-plan-desc">${plan?.description || 'Prepared clip window from a detected highlight.'}</div>
        <div class="clip-plan-meta">
          <span>${plan?.primary_player || 'Unknown'}</span>
          <span>Round ${toNum(plan?.round_number, 0)}</span>
          <span>${clipTypeLabel(plan?.clip_type)}</span>
          <span>${score}</span>
          ${presetLabel ? `<span class="clip-plan-preset-badge" title="Preset: ${presetLabel}">${presetLabel}</span>` : ''}
          ${povHint ? `<span class="clip-plan-pov-hint" title="${povHint}">${(plan?.pov_mode || '').replace(/_/g, ' ')}</span>` : ''}
        </div>
      </div>
    </div>
    <div class="clip-plan-actions">
      ${existing}
      ${queueBadge}
      <span class="clip-plan-mode-badge">${modeLabel}</span>
      <button type="button" class="btn btn-secondary btn-sm" data-plan-action="replay">Watch in Replay</button>
      <button type="button" class="btn ${renderedClipId ? 'btn-secondary' : 'btn-primary'} btn-sm" data-plan-action="render" ${renderBtnDisabled ? 'disabled' : ''}>
        ${renderBtnLabel}
      </button>
    </div>
  `;
  row.querySelector('[data-plan-action="replay"]')?.addEventListener('click', () => {
    const sourceHighlight = findHighlightById(plan?.source_highlight_id);
    const fallback = {
      highlight_id: plan?.source_highlight_id || plan?.clip_plan_id,
      type: plan?.clip_type || 'highlight',
      title: plan?.title || 'Clip Plan',
      round_number: toNum(plan?.round_number, 0),
      start_tick: toNum(plan?.start_tick, 0),
      anchor_tick: toNum(plan?.anchor_tick, 0),
      end_tick: toNum(plan?.end_tick, 0),
      primary_player: plan?.primary_player || '',
      score: toNum(plan?.score, 0),
    };
    jumpToHighlightReplay(sourceHighlight || fallback, { seekMode: 'start', focusWindow: true });
  });
  return row;
}

async function renderClipsView() {
  const body = $('#clips-body');
  body.innerHTML = '<div class="loading-placeholder">Loading clips...</div>';

  try {
    const loaders = [ensureClipsLoaded({ global: true, force: true }), ensureRenderModesLoaded()];
    if (State.demoId) loaders.push(ensureClipPlansLoaded());
    if (State.demoId) loaders.push(ensureHighlightsLoaded());
    const results = await Promise.all(loaders);

    const clipsBundle = results[0] || { summary: {}, clips: [] };
    // results: [0]=clips, [1]=renderModes, [2]=clipPlans, [3]=highlights
    const clipPlansBundle = State.demoId ? (results[2] || { summary: {}, clip_plans: [] }) : { summary: {}, clip_plans: [] };
    const clipPlans = Array.isArray(clipPlansBundle?.clip_plans) ? clipPlansBundle.clip_plans : [];
    let clips = Array.isArray(clipsBundle?.clips) ? clipsBundle.clips : [];
    const clipSummary = clipsBundle?.summary || {};
    const availablePlanIds = new Set(clipPlans.map((item) => String(item?.clip_plan_id || '')).filter(Boolean));
    State.selectedClipPlanIds = State.selectedClipPlanIds.filter((id) => availablePlanIds.has(id));

    if (!State.selectedClipId || !clips.some((item) => item.clip_id === State.selectedClipId)) {
      State.selectedClipId = clips[0]?.clip_id || null;
    }

    body.innerHTML = `
      <div class="clips-overview-row">
        <div class="overview-stat-card">
          <div class="osc-label">Saved Clips</div>
          <div class="osc-value">${toNum(clipSummary.total_clips, clips.length)}</div>
          <div class="osc-sub">Indexed across all demos</div>
        </div>
        <div class="overview-stat-card">
          <div class="osc-label">Planned Clips</div>
          <div class="osc-value">${clipPlans.length}</div>
          <div class="osc-sub">${State.demoId ? 'Available for current demo' : 'Load a demo to render new clips'}</div>
        </div>
        <div class="overview-stat-card">
          <div class="osc-label">Highest Score</div>
          <div class="osc-value">${clips.length ? Math.round(Math.max(...clips.map((item) => toNum(item.score, 0))) * 100) : 0}</div>
          <div class="osc-sub">Top clip score</div>
        </div>
        <div class="overview-stat-card">
          <div class="osc-label">Missing Files</div>
          <div class="osc-value">${toNum(clipSummary.missing_files, 0)}</div>
          <div class="osc-sub">Validation warnings</div>
        </div>
      </div>
      <section class="section clips-gallery-section">
        <div class="section-title">Clips Gallery</div>
        <div class="section-subtitle">Saved clips are listed globally. Render queue below applies to the currently loaded demo.</div>
        <div class="clip-toolbar">
          <div class="clip-toolbar-group">
            <select class="select clip-sort-select" id="clip-sort-select">
              <option value="newest">Newest First</option>
              <option value="score">Highest Score</option>
              <option value="round">Round Order</option>
            </select>
            <select class="select clip-type-select" id="clip-type-select">
              <option value="all">All Types</option>
            </select>
            <select class="select clip-player-select" id="clip-player-filter">
              <option value="all">All Players</option>
            </select>
          </div>
          <div class="clip-toolbar-meta">${clips.length} saved ? ${clipPlans.length} planned</div>
        </div>
        <div class="clips-layout">
          <div class="clip-list-panel">
            <div id="clips-list" class="clip-list"></div>
          </div>
          <div class="clip-detail-panel" id="clip-detail-panel"></div>
        </div>
      </section>
      <section class="section clip-plans-section">
        <div class="section-title">Render Queue</div>
        <div class="section-subtitle">${State.demoId ? 'Queue clip renders individually or in batch. Jobs process sequentially in the background.' : 'Load and parse a demo to render new clips. Saved clips above remain browsable without an active demo.'}</div>
        <div id="ingame-readiness" class="ingame-readiness-banner hidden"></div>
        <div class="render-queue-toolbar" id="render-queue-toolbar">
          <label class="render-mode-label">Render Mode</label>
          <select class="select render-mode-select" id="render-mode-select">
            ${renderModeOptionsMarkup()}
          </select>
          <label class="render-mode-label">Preset</label>
          <select class="select render-preset-select" id="render-preset-select">
            <option value="">Auto (by highlight)</option>
            <option value="quick_review">Quick Review</option>
            <option value="standard_highlight">Standard Highlight</option>
            <option value="cinematic">Cinematic</option>
            <option value="tactical_focus">Tactical Focus</option>
          </select>
          <button class="btn btn-secondary btn-sm" id="btn-refresh-readiness" title="Check in-game readiness">Check Readiness</button>
          <div class="render-queue-toolbar-spacer"></div>
          <button class="btn btn-primary btn-sm" id="btn-batch-top5" title="Queue top 5 clip plans by score" ${State.demoId ? '' : 'disabled'}>Batch Top 5</button>
          <button class="btn btn-secondary btn-sm" id="btn-batch-selected" title="Queue selected clip plans" ${State.demoId ? '' : 'disabled'}>Batch Selected</button>
          <button class="btn btn-secondary btn-sm" id="btn-batch-all" title="Queue all clip plans" ${State.demoId ? '' : 'disabled'}>Queue All</button>
          <button class="btn btn-secondary btn-sm" id="btn-cancel-all" title="Cancel all queued jobs">Cancel Queued</button>
        </div>
        <div id="queue-status-panel" class="queue-status-panel"></div>
        <div id="clip-plan-list" class="clip-plan-list"></div>
      </section>
    `;
    body.prepend(
      createCurrentMomentSection({
        subtitle: 'Selected moment stays available while you queue, preview, and review clips.',
      }),
    );

    const sortSelect = $('#clip-sort-select');
    const typeSelect = $('#clip-type-select');
    const playerSelect = $('#clip-player-filter');
    const listNode = $('#clips-list');
    const detailNode = $('#clip-detail-panel');
    const planListNode = $('#clip-plan-list');
    const queuePanel = $('#queue-status-panel');
    const batchSelectedBtn = $('#btn-batch-selected');

    [...new Set(clips.map((item) => String(item?.clip_type || '')).filter(Boolean))].sort().forEach((type) => {
      const opt = document.createElement('option');
      opt.value = type;
      opt.textContent = clipTypeLabel(type);
      typeSelect.appendChild(opt);
    });
    [...new Set(clips.map((item) => String(item?.primary_player || '')).filter(Boolean))].sort().forEach((player) => {
      const opt = document.createElement('option');
      opt.value = player;
      opt.textContent = player;
      playerSelect.appendChild(opt);
    });

    const renderDetail = (clip) => {
      if (!clip) {
        detailNode.innerHTML = `
          <div class="empty-state">
            <div class="empty-state-title">No clip selected</div>
            <div class="empty-state-sub">Render a clip or choose one from the gallery.</div>
          </div>
        `;
        return;
      }
      setCurrentMomentFromClip(clip, 'clip-detail');
      const sourceHighlight = findHighlightById(clip?.source_highlight_id);
      const validationWarnings = Array.isArray(clip?.metadata?.validation_warnings) ? clip.metadata.validation_warnings : [];
      const fileUrl = clip?.file_url || '';
      const posterUrl = clip?.thumbnail_url || '';
      detailNode.innerHTML = `
        <div class="clip-detail-hero">
          <div class="clip-detail-copy">
            <div class="clip-detail-eyebrow">${clipTypeLabel(clip?.clip_type)} ? Round ${toNum(clip?.round_number, 0)}</div>
            <h3>${clip?.title || 'Clip'}</h3>
            <p>${clip?.description || 'Rendered clip artifact from a detected highlight.'}</p>
            <div class="clip-detail-meta">
              <span>${clip?.primary_player || 'Unknown'}</span>
              <span>${formatDurationSeconds(clip?.duration_s)}</span>
              <span>${clipTypeLabel(clip?.pov_mode)}</span>
              <span>${Math.round(clamp(toNum(clip?.score, 0), 0, 1) * 100)}</span>
            </div>
            <div class="clip-detail-actions">
              <button type="button" class="btn btn-primary btn-sm" data-clip-detail="replay">Open in Replay</button>
              ${clip?.primary_player ? '<button type="button" class="btn btn-secondary btn-sm" data-clip-detail="player">Open Player</button>' : ''}
              ${sourceHighlight ? '<button type="button" class="btn btn-secondary btn-sm" data-clip-detail="highlight">View Source Highlight</button>' : ''}
              ${clip?.primary_player ? '<button type="button" class="btn btn-secondary btn-sm" data-clip-detail="coach">Coach Player</button>' : ''}
            </div>
            ${validationWarnings.length ? `<div class="clip-detail-warnings">${validationWarnings.join(' ? ')}</div>` : ''}
          </div>
          <div class="clip-detail-player">
            <div class="clip-detail-player-label">Source</div>
            <div class="clip-detail-player-value">${sourceHighlight?.title || clip?.metadata?.source_highlight_title || 'Clip Plan'}</div>
          </div>
        </div>
        <div class="clip-video-shell">
          ${fileUrl
            ? `<video class="clip-video-player" controls preload="metadata" poster="${posterUrl}" src="${fileUrl}"></video>`
            : `<div class="empty-state"><div class="empty-state-title">Video file missing</div><div class="empty-state-sub">The clip record exists but the mp4 file could not be found.</div></div>`}
        </div>
        <div class="clip-detail-grid">
          <div class="detail-block-card">
            <div class="detail-block-title">Clip Timing</div>
            <div class="detail-block-body">
              <div class="detail-stat-row"><span class="detail-stat-label">Start Tick</span><span class="detail-stat-value">${toNum(clip?.start_tick, 0)}</span></div>
              <div class="detail-stat-row"><span class="detail-stat-label">Anchor Tick</span><span class="detail-stat-value">${toNum(clip?.anchor_tick, 0)}</span></div>
              <div class="detail-stat-row"><span class="detail-stat-label">End Tick</span><span class="detail-stat-value">${toNum(clip?.end_tick, 0)}</span></div>
              <div class="detail-stat-row"><span class="detail-stat-label">Render Mode</span><span class="detail-stat-value">${clipTypeLabel(clip?.render_mode)}</span></div>
            </div>
          </div>
          <div class="detail-block-card">
            <div class="detail-block-title">Context</div>
            <div class="detail-block-body">
              <div class="detail-stat-row"><span class="detail-stat-label">Clip Plan</span><span class="detail-stat-value">${clip?.clip_plan_id || '-'}</span></div>
              <div class="detail-stat-row"><span class="detail-stat-label">Highlight</span><span class="detail-stat-value">${clip?.source_highlight_id || '-'}</span></div>
              <div class="detail-stat-row"><span class="detail-stat-label">Demo</span><span class="detail-stat-value">${clip?.demo_id || '-'}</span></div>
              <div class="detail-stat-row"><span class="detail-stat-label">Created</span><span class="detail-stat-value">${String(clip?.created_at || '-').replace('T', ' ').replace('Z', '')}</span></div>
              <div class="detail-stat-row"><span class="detail-stat-label">Tags</span><span class="detail-stat-value">${(clip?.tags || []).join(', ') || '-'}</span></div>
            </div>
          </div>
        </div>
      `;
      detailNode.querySelector('[data-clip-detail="replay"]')?.addEventListener('click', () => openClipInReplay(clip));
      detailNode.querySelector('[data-clip-detail="player"]')?.addEventListener('click', async () => {
        if (!clip?.primary_player) return;
        navigateTo('player');
        $('#player-select').value = clip.primary_player;
        await analyzePlayer(clip.primary_player);
      });
      detailNode.querySelector('[data-clip-detail="highlight"]')?.addEventListener('click', async () => {
        if (!sourceHighlight) return;
        await jumpToHighlightReplay(sourceHighlight, { seekMode: 'start', focusWindow: true });
      });
      detailNode.querySelector('[data-clip-detail="coach"]')?.addEventListener('click', () => {
        if (clip?.primary_player) navigateToCoaching(clip.primary_player);
      });
    };

    const renderClipList = () => {
      const filtered = filterAndSortClips(clips, {
        sort: sortSelect.value,
        type: typeSelect.value,
        player: playerSelect.value,
      });
      listNode.innerHTML = '';
      if (!filtered.length) {
        listNode.innerHTML = `
          <div class="empty-state">
            <div class="empty-state-title">No clips found</div>
            <div class="empty-state-sub">Render clips from the current demo or view saved clips after they are indexed.</div>
          </div>
        `;
        if (!clips.length) {
          State.selectedClipId = null;
        }
        renderDetail(null);
        return;
      }
      if (!filtered.some((item) => item.clip_id === State.selectedClipId)) {
        State.selectedClipId = filtered[0].clip_id;
      }
      filtered.forEach((clip) => {
        const card = createClipCard(clip, clip.clip_id === State.selectedClipId);
        card.addEventListener('click', () => {
          State.selectedClipId = clip.clip_id;
          setCurrentMomentFromClip(clip, 'clip-list-select');
          renderClipList();
        });
        card.querySelector('[data-clip-action="select"]')?.addEventListener('click', (ev) => {
          ev.stopPropagation();
          State.selectedClipId = clip.clip_id;
          setCurrentMomentFromClip(clip, 'clip-list-preview');
          renderClipList();
        });
        card.querySelector('[data-clip-action="replay"]')?.addEventListener('click', async (ev) => {
          ev.stopPropagation();
          await openClipInReplay(clip);
        });
        listNode.appendChild(card);
      });
      renderDetail(filtered.find((item) => item.clip_id === State.selectedClipId) || filtered[0]);
    };

    const clipsByPlan = new Map(clips.map((item) => [String(item?.clip_plan_id || ''), item]));
    const plannedRows = clipPlans
      .slice()
      .sort((a, b) => toNum(b?.score, 0) - toNum(a?.score, 0))
      .slice(0, 24);

    // ── Render mode selector wiring ──────────────────────────────────────
    const modeSelect = $('#render-mode-select');
    const presetSelect = $('#render-preset-select');
    const readinessBanner = $('#ingame-readiness');
    const refreshBtn = $('#btn-refresh-readiness');

    if (modeSelect) {
      modeSelect.value = State.renderMode;
      modeSelect.addEventListener('change', () => {
        State.renderMode = modeSelect.value;
        rebuildPlanRows();
        if (State.renderMode === 'cs2_ingame_capture') {
          checkIngameReadiness();
        } else {
          readinessBanner?.classList.add('hidden');
        }
      });
    }
    if (presetSelect) {
      presetSelect.value = State.renderPreset;
      presetSelect.addEventListener('change', () => {
        State.renderPreset = presetSelect.value;
      });
    }
    if (refreshBtn) {
      refreshBtn.addEventListener('click', checkIngameReadiness);
    }
    if (State.renderMode === 'cs2_ingame_capture') {
      checkIngameReadiness();
    }

    async function checkIngameReadiness() {
      if (!readinessBanner) return;
      readinessBanner.classList.remove('hidden');
      readinessBanner.innerHTML = '<div class="readiness-loading"><div class="spinner-sm"></div> Checking in-game capture readiness...</div>';
      try {
        const health = await API.getIngameHealth(State.demoId || '');
        State.ingameHealth = health;
        renderReadinessBanner(health);
      } catch (err) {
        readinessBanner.innerHTML = `<div class="readiness-row readiness-blocked"><span class="readiness-icon">&#10005;</span> Failed to check readiness: ${err.message}</div>`;
      }
    }

    function renderReadinessBanner(health) {
      if (!readinessBanner || !health) return;
      const r = health.readiness;
      const cls = r === 'ready' ? 'readiness-ready' : r === 'partially_ready' ? 'readiness-partial' : 'readiness-blocked';
      const icon = r === 'ready' ? '&#10003;' : r === 'partially_ready' ? '&#9888;' : '&#10005;';
      const label = r === 'ready' ? 'Ready to capture' : r === 'partially_ready' ? 'Partially ready' : 'Blocked';
      const checks = (health.checks || []).map(c => {
        const ok = c.status === 'ok';
        const name = (c.check || '').replace(/_/g, ' ');
        return `<span class="readiness-check ${ok ? 'check-ok' : (c.status === 'error' ? 'check-fail' : '')}">${ok ? '&#10003;' : (c.status === 'error' ? '&#10005;' : '&#9679;')} ${name}</span>`;
      }).join('');
      const blockers = (health.blockers || []).map(b => `<div class="readiness-blocker">${b}</div>`).join('');
      const warnings = (health.warnings || []).map(w => `<div class="readiness-warning">${w}</div>`).join('');
      readinessBanner.innerHTML = `
        <div class="readiness-row ${cls}">
          <span class="readiness-icon">${icon}</span>
          <span class="readiness-label">${label}</span>
          <div class="readiness-checks">${checks}</div>
        </div>
        ${blockers}${warnings}
      `;
    }

    // ── Queue status panel ───────────────────────────────────────────────

    let lastCompletedCount = 0;

    async function pollQueueStatus() {
      try {
        const qs = await API.getQueueStatus();
        State.queueStatus = qs;
        renderQueuePanel(qs);
        rebuildPlanRows();
        // If new jobs completed since last poll, refresh the gallery
        const nowCompleted = qs.completed_count || 0;
        if (nowCompleted > lastCompletedCount) {
          await ensureClipsLoaded({ global: true, force: true });
          // Re-render clip list without full view rebuild
          const freshClips = State.clipsBundle?.clips || [];
          if (freshClips.length !== clips.length) {
            clips = freshClips;
            renderClipList();
          }
        }
        lastCompletedCount = nowCompleted;
      } catch { /* silent */ }
    }

    function renderQueuePanel(qs) {
      if (!queuePanel) return;
      const jobs = qs.jobs || [];
      if (!jobs.length) {
        queuePanel.innerHTML = '';
        return;
      }

      const active = jobs.filter(j => ['running','validating','preparing','recording','finalizing'].includes(j.status));
      const queued = jobs.filter(j => j.status === 'queued');
      const failed = jobs.filter(j => j.status === 'failed');
      const completed = jobs.filter(j => j.status === 'completed');
      const cancelled = jobs.filter(j => j.status === 'cancelled');

      let html = '<div class="queue-summary">';
      html += `<span class="queue-stat">${queued.length} queued</span>`;
      if (active.length) html += `<span class="queue-stat queue-stat-active">${active.length} active</span>`;
      html += `<span class="queue-stat queue-stat-done">${completed.length} done</span>`;
      if (failed.length) html += `<span class="queue-stat queue-stat-fail">${failed.length} failed</span>`;
      if (cancelled.length) html += `<span class="queue-stat">${cancelled.length} cancelled</span>`;
      // Bulk actions
      if (failed.length) html += `<button class="btn btn-secondary btn-xs" data-queue-action="retry-all">Retry Failed</button>`;
      if (failed.length) html += `<button class="btn btn-secondary btn-xs" data-queue-action="clear-failed">Clear Failed</button>`;
      if (completed.length || cancelled.length) html += `<button class="btn btn-secondary btn-xs" data-queue-action="clear">Clear Done</button>`;
      html += '</div>';

      const metaBits = [];
      if (qs.last_completed_job) {
        metaBits.push(`<span class="queue-job-detail-pill">Last done: ${qs.last_completed_job.title || qs.last_completed_job.clip_plan_id || qs.last_completed_job.job_id}</span>`);
      }
      if (qs.last_failed_job) {
        metaBits.push(`<span class="queue-job-detail-pill queue-stat-fail">Last failed: ${qs.last_failed_job.title || qs.last_failed_job.clip_plan_id || qs.last_failed_job.job_id}</span>`);
      }
      if (metaBits.length) {
        html += `<div class="queue-meta-line">${metaBits.join('')}</div>`;
      }

      // Job rows (show active + queued + recent failed, limit total)
      const visible = [...active, ...queued, ...failed.slice(0, 5), ...completed.slice(0, 3), ...cancelled.slice(0, 2)];
      if (visible.length) {
        html += '<div class="queue-jobs-list">';
        visible.forEach(j => {
          const statusCls = queueJobStatusClass(j.status);
          const statusLabel = (j.progress_stage && j.status !== 'completed' && j.status !== 'failed' && j.status !== 'cancelled')
            ? stageDisplayLabel(j.progress_stage)
            : j.status;
          const title = j.title || j.clip_plan_id || 'Job';
          const modeLabel = renderModeDisplayLabel(j.render_mode, { short: true });
          let actions = '';
          if (j.status === 'queued') {
            actions = `<button class="btn btn-secondary btn-xs" data-queue-job-action="cancel" data-job-id="${j.job_id}">Cancel</button>`;
          } else if (['running','validating','preparing','recording','finalizing'].includes(j.status)) {
            actions = `<button class="btn btn-secondary btn-xs" data-queue-job-action="cancel" data-job-id="${j.job_id}">Request Cancel</button>`;
          } else if (j.status === 'failed' || j.status === 'cancelled') {
            actions = `<button class="btn btn-secondary btn-xs" data-queue-job-action="retry" data-job-id="${j.job_id}">Retry</button>`;
          }
          html += `
            <div class="queue-job-row ${statusCls}">
              <div class="queue-job-info">
                <span class="queue-job-title">${title}</span>
                <span class="queue-job-meta">R${j.round_number} &middot; ${j.primary_player || '?'} &middot; ${modeLabel}</span>
                <div class="queue-job-details">
                  ${j.render_preset ? `<span class="queue-job-detail-pill">${j.render_preset.replace(/_/g, ' ')}</span>` : ''}
                  ${j.retry_count ? `<span class="queue-job-detail-pill">Retry ${j.retry_count}</span>` : ''}
                </div>
                ${j.error ? `<span class="queue-job-error">${j.error}</span>` : ''}
              </div>
              <span class="queue-job-status">${statusLabel}</span>
              <div class="queue-job-actions">${actions}</div>
            </div>
          `;
        });
        html += '</div>';
      }

      queuePanel.innerHTML = html;

      // Wire queue actions
      queuePanel.querySelector('[data-queue-action="retry-all"]')?.addEventListener('click', async () => {
        await API.queueRetryAllFailed();
        await pollQueueStatus();
      });
      queuePanel.querySelector('[data-queue-action="clear-failed"]')?.addEventListener('click', async () => {
        await API.queueClearFailed();
        await pollQueueStatus();
      });
      queuePanel.querySelector('[data-queue-action="clear"]')?.addEventListener('click', async () => {
        await API.queueClearCompleted();
        await pollQueueStatus();
      });
      queuePanel.querySelectorAll('[data-queue-job-action="cancel"]').forEach(btn => {
        btn.addEventListener('click', async () => {
          await API.queueCancel(btn.dataset.jobId);
          await pollQueueStatus();
        });
      });
      queuePanel.querySelectorAll('[data-queue-job-action="retry"]').forEach(btn => {
        btn.addEventListener('click', async () => {
          await API.queueRetry(btn.dataset.jobId);
          await pollQueueStatus();
        });
      });
    }

    function queueJobStatusClass(status) {
      if (['running','validating','preparing','recording','finalizing'].includes(status)) return 'queue-job-active';
      if (status === 'completed') return 'queue-job-completed';
      if (status === 'failed') return 'queue-job-failed';
      if (status === 'cancelled') return 'queue-job-cancelled';
      return '';
    }

    // ── Plan rows with enqueue-to-queue ──────────────────────────────────

    function updateBatchSelectedButton() {
      if (!batchSelectedBtn) return;
      const count = State.selectedClipPlanIds.length;
      batchSelectedBtn.textContent = count ? `Batch Selected (${count})` : 'Batch Selected';
      batchSelectedBtn.disabled = !State.demoId || count === 0;
    }

    function rebuildPlanRows() {
      if (!State.demoId || !plannedRows.length) return;

      // Build map: clip_plan_id → highest-priority queue job
      const planJobMap = new Map();
      (State.queueStatus?.jobs || []).forEach(j => {
        if (!j.clip_plan_id) return;
        const existing = planJobMap.get(j.clip_plan_id);
        if (!existing || getPlanJobPriority(j) > getPlanJobPriority(existing)) {
          planJobMap.set(j.clip_plan_id, j);
        }
      });

      planListNode.innerHTML = '';
      plannedRows.forEach((plan) => {
        const existing = clipsByPlan.get(String(plan?.clip_plan_id || ''));
        const queueJob = planJobMap.get(plan?.clip_plan_id) || null;
        const row = createClipPlanRow(plan, existing?.clip_id || '', queueJob);
        attachEnqueueHandler(row, plan);
        planListNode.appendChild(row);
      });
      updateBatchSelectedButton();
    }

    function attachEnqueueHandler(row, plan) {
      row.querySelector('[data-plan-action="select"]')?.addEventListener('change', (ev) => {
        setClipPlanSelected(plan.clip_plan_id, ev.currentTarget.checked);
        row.classList.toggle('is-selected', ev.currentTarget.checked);
        updateBatchSelectedButton();
      });

      row.querySelector('[data-plan-action="render"]')?.addEventListener('click', async (ev) => {
        const btn = ev.currentTarget;
        btn.disabled = true;
        const original = btn.textContent;
        btn.textContent = 'Queuing...';
        try {
          await API.queueEnqueue(State.demoId, plan.clip_plan_id, State.renderMode, State.renderPreset);
          btn.textContent = 'Queued';
          await pollQueueStatus();
        } catch (err) {
          btn.disabled = false;
          btn.textContent = original;
          const prevErr = row.querySelector('.render-error-panel');
          if (prevErr) prevErr.remove();
          const errPanel = el('div', 'render-error-panel');
          errPanel.innerHTML = `<div class="render-error-msg">${err.message}</div>`;
          row.appendChild(errPanel);
          showToast(err.message, { type: 'error', title: 'Render queue failed' });
        }
      });
    }

    // ── Batch action buttons ─────────────────────────────────────────────

    $('#btn-batch-top5')?.addEventListener('click', async () => {
      const btn = $('#btn-batch-top5');
      btn.disabled = true;
      btn.textContent = 'Queuing...';
      try {
        const result = await API.queueEnqueueBatch(State.demoId, { mode: 'top', count: 5, renderMode: State.renderMode, renderPreset: State.renderPreset });
        btn.textContent = `Queued ${result.enqueued_count || 0}`;
        if (result.skipped_count) {
          showToast(`Queued ${result.enqueued_count || 0}, skipped ${result.skipped_count}`, { type: 'info', title: 'Partial batch queued' });
        }
        await pollQueueStatus();
      } catch (err) {
        btn.textContent = 'Batch Top 5';
        btn.disabled = false;
        showToast(err.message, { type: 'error', title: 'Batch queue failed' });
      }
    });

    batchSelectedBtn?.addEventListener('click', async () => {
      const original = batchSelectedBtn.textContent;
      batchSelectedBtn.disabled = true;
      batchSelectedBtn.textContent = 'Queuing...';
      try {
        const result = await API.queueEnqueueBatch(State.demoId, {
          mode: 'selected',
          clipPlanIds: State.selectedClipPlanIds,
          renderMode: State.renderMode,
          renderPreset: State.renderPreset,
        });
        batchSelectedBtn.textContent = `Queued ${result.enqueued_count || 0}`;
        if (result.skipped_count) {
          showToast(`Queued ${result.enqueued_count || 0}, skipped ${result.skipped_count}`, { type: 'info', title: 'Partial batch queued' });
        }
        await pollQueueStatus();
      } catch (err) {
        batchSelectedBtn.textContent = original;
        showToast(err.message, { type: 'error', title: 'Batch queue failed' });
      } finally {
        updateBatchSelectedButton();
      }
    });

    $('#btn-batch-all')?.addEventListener('click', async () => {
      const btn = $('#btn-batch-all');
      btn.disabled = true;
      btn.textContent = 'Queuing...';
      try {
        const result = await API.queueEnqueueBatch(State.demoId, { mode: 'all', renderMode: State.renderMode, renderPreset: State.renderPreset });
        btn.textContent = `Queued ${result.enqueued_count || 0}`;
        if (result.skipped_count) {
          showToast(`Queued ${result.enqueued_count || 0}, skipped ${result.skipped_count}`, { type: 'info', title: 'Partial batch queued' });
        }
        await pollQueueStatus();
      } catch (err) {
        btn.textContent = 'Queue All';
        btn.disabled = false;
        showToast(err.message, { type: 'error', title: 'Batch queue failed' });
      }
    });

    $('#btn-cancel-all')?.addEventListener('click', async () => {
      await API.queueCancelAll();
      await pollQueueStatus();
    });

    // ── Plan list and queue initialization ───────────────────────────────

    if (!State.demoId) {
      planListNode.innerHTML = `
        <div class="empty-state">
          <div class="empty-state-title">No active demo</div>
          <div class="empty-state-sub">Upload and parse a demo to generate new clips. Saved clips are already listed above.</div>
        </div>
      `;
    } else if (!plannedRows.length) {
      planListNode.innerHTML = `
        <div class="empty-state">
          <div class="empty-state-title">No clip plans available</div>
          <div class="empty-state-sub">Parse a demo and detect highlights first.</div>
        </div>
      `;
    } else {
      rebuildPlanRows();
    }

    updateBatchSelectedButton();

    // Initial queue poll + start polling interval
    pollQueueStatus();
    stopQueuePolling();
    State.queuePollHandle = setInterval(pollQueueStatus, 3000);

    [sortSelect, typeSelect, playerSelect].forEach((node) => {
      node.addEventListener('change', renderClipList);
    });
    renderClipList();
  } catch (err) {
    body.innerHTML = `<div class="empty-state"><div class="empty-state-title">Failed to load clips</div><div class="empty-state-sub">${err.message}</div></div>`;
  }
}

function stopQueuePolling() {
  if (State.queuePollHandle) {
    clearInterval(State.queuePollHandle);
    State.queuePollHandle = null;
  }
}

async function jumpToHighlightReplay(highlight, options = {}) {
  setCurrentMomentFromHighlight(highlight, options.source || 'replay-jump');
  if (!ensureReplayReadyForMoment(State.currentMoment)) return;
  const roundNum = toNum(highlight?.round_number, 0);
  const seekTick = replaySeekTickForHighlight(highlight, options.seekMode || 'start');
  if (!roundNum) return;
  setSelectedReplayHighlight(highlight, { focusWindow: options.focusWindow !== false });
  if (options.navigate !== false) navigateTo('replay');
  await initReplayView(roundNum, seekTick, { autoplayHighlight: !!options.autoplay, seekMode: options.seekMode || 'start' });
}

// â”€â”€ HLTV-style rating color class â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
function ratingClass(r) {
  const n = parseFloat(r);
  if (isNaN(n)) return '';
  if (n >= 1.15) return 'rating-great';
  if (n >= 1.00) return 'rating-good';
  if (n >= 0.85) return 'rating-ok';
  return 'rating-bad';
}

// â”€â”€ Navigation â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
function navigateTo(viewName) {
  $$('.nav-item').forEach(a => a.classList.toggle('active', a.dataset.view === viewName));
  $$('.view').forEach(v => v.classList.toggle('active', v.id === `view-${viewName}`));
}

$$('.nav-item').forEach(a => {
  a.addEventListener('click', async (e) => {
    e.preventDefault();
    const view = a.dataset.view;
    if (!State.demoId && !['upload', 'clips'].includes(view)) return;
    if (view !== 'clips') stopQueuePolling();
    navigateTo(view);
    if (view === 'overview') renderOverview();
    if (view === 'team')     renderTeam();
    if (view === 'replay')   await initReplayView();
    if (view === 'clips')    await renderClipsView();
    if (view === 'coaching') renderCoachingView();
  });
});

// â”€â”€ Upload flow â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
const uploadZone = $('#upload-zone');
const fileInput  = $('#file-input');

$('#btn-browse').addEventListener('click', () => fileInput.click());
fileInput.addEventListener('change', () => { if (fileInput.files[0]) startUpload(fileInput.files[0]); });

uploadZone.addEventListener('click', () => fileInput.click());
uploadZone.addEventListener('dragover',  (e) => { e.preventDefault(); uploadZone.classList.add('drag-over'); });
uploadZone.addEventListener('dragleave', ()  => uploadZone.classList.remove('drag-over'));
uploadZone.addEventListener('drop', (e) => {
  e.preventDefault();
  uploadZone.classList.remove('drag-over');
  const f = e.dataTransfer.files[0];
  if (f && f.name.endsWith('.dem')) startUpload(f);
});

function setStep(stepId, state) {
  const s = $(`#${stepId}`);
  if (!s) return;
  s.classList.toggle('active', state === 'active');
  s.classList.toggle('done',   state === 'done');
}

function setStatus(msg) { $('#upload-status-msg').textContent = msg; }

async function runLocalReadinessCheck() {
  try {
    const doctor = await API.getLocalDoctor();
    if (!doctor || doctor.status === 'ready') return;
    const blockers = Array.isArray(doctor.blockers) ? doctor.blockers : [];
    const actions = Array.isArray(doctor.next_actions) ? doctor.next_actions : [];
    const headline = blockers.length
      ? `Setup attention required: ${blockers[0]}`
      : 'Local setup needs attention before full workflow use.';
    const hint = actions.length ? ` ${actions[0]}` : '';
    showToast(`${headline}${hint}`, {
      type: 'warning',
      title: 'Local Readiness',
      duration: 8000,
    });
  } catch {
    // Keep startup resilient if diagnostics endpoint is unavailable.
  }
}

function resetDemoScopedState() {
  if (replayEngine) {
    replayEngine.stop();
    replayEngine = null;
  }

  State.teamAnalysis = null;
  State.analyses = {};
  State.playerVisuals = {};
  State.playerSteamProfiles = {};
  State.playerSteamIds = {};
  State.playerSideFilter = 'both';
  State.currentPlayer = null;
  State.highlightsBundle = null;
  State.clipPlansBundle = null;
  State.clipsBundle = null;
  State.selectedClipId = null;
  State.selectedReplayHighlight = null;
  State.replayFocusMode = false;
  State.replayHighlightWindow = null;
  State.currentMoment = null;
  State.replayRounds = [];
  State.currentReplayRound = null;
  State.currentReplayData = null;
  State.radarUrl = null;

  const pills = $('#round-pills');
  if (pills) {
    pills.innerHTML = '';
    if (pills.dataset.loaded) delete pills.dataset.loaded;
  }

  const scrubber = $('#rp-scrubber');
  if (scrubber) {
    scrubber.min = '0';
    scrubber.max = '0';
    scrubber.value = '0';
  }
  const frameCounter = $('#rp-frame-counter');
  if (frameCounter) frameCounter.textContent = '0 / 0';

  const alivePanel = $('#alive-panel');
  if (alivePanel) alivePanel.innerHTML = '';
  const killFeed = $('#kill-feed');
  if (killFeed) killFeed.innerHTML = '';
  const roundTags = $('#round-tags-display');
  if (roundTags) roundTags.textContent = '-';
  const highlightContext = $('#replay-highlight-context');
  if (highlightContext) {
    highlightContext.innerHTML = '';
    highlightContext.classList.add('hidden');
  }
  const highlightTimeline = $('#rp-highlight-timeline');
  if (highlightTimeline) {
    highlightTimeline.innerHTML = '';
    highlightTimeline.classList.add('hidden');
  }
}

async function startUpload(file) {
  resetDemoScopedState();
  $('#upload-progress').classList.remove('hidden');
  setStep('step-upload', 'active');
  setStatus('Uploading demo...');
  try {
    const up = await API.uploadDemo(file);
    State.demoId   = up.demo_id;
    State.filename = up.filename;
    setStep('step-upload', 'done');
    setStep('step-parse', 'active');
    setStatus('Parsing demo - this may take 20-60 s...');

    const parsed = await API.parseDemo(State.demoId);
    State.mapName       = parsed.map;
    State.totalRounds   = parsed.total_rounds;
    State.players       = parsed.players || [];
    // Store steamid64 per player — resolved directly from demo tick data (not by name)
    State.playerSteamIds = parsed.player_steamids || {};
    if (steamDebugEnabled) {
      console.debug('[steam-ids] Resolved from demo:', State.playerSteamIds);
    }
    setStep('step-parse', 'done');
    setStep('step-ready', 'active');
    setStatus('Ready!');

    $('#meta-filename').textContent = State.filename;
    $('#meta-map').textContent      = State.mapName || '-';
    $('#meta-rounds').textContent   = `${State.totalRounds} rounds`;
    $('#demo-meta').classList.remove('hidden');

    State.radarUrl = await API.getRadarUrl(State.mapName);

    populatePlayerSelects();

    setTimeout(() => {
      setStep('step-ready', 'done');
      navigateTo('overview');
      renderOverview();
    }, 600);

  } catch (err) {
    setStatus(`Error: ${err.message}`);
    setStep('step-upload', '');
    setStep('step-parse', '');
    showToast(err.message, { type: 'error', title: 'Upload failed' });
  }
}

function populatePlayerSelects() {
  const players = State.players;
  ['#player-select', '#coaching-player-select'].forEach(sel => {
    const elSel = $(sel);
    if (!elSel) return;
    elSel.innerHTML = '';
    players.forEach(p => {
      const o = document.createElement('option');
      o.value = p;
      o.textContent = p;
      elSel.appendChild(o);
    });
  });
}

// â”€â”€ Scoreboard builder â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
// scoreboard rows have: player, team, kills, deaths, kd_ratio, adr, kast (%), hs_rate (%), rating

function buildTeamTable(rows) {
  if (!rows.length) return '<div class="empty-state"><div class="empty-state-sub">No data</div></div>';
  const trs = rows.map(p => {
    const rc = ratingClass(p.rating);
    return `<tr data-player="${p.player}" style="cursor:pointer">
      <td class="highlight">${p.player}</td>
      <td class="mono">${p.kills ?? '-'}</td>
      <td class="mono">${p.deaths ?? '-'}</td>
      <td class="mono">${fmt(p.kd_ratio, 2)}</td>
      <td class="mono">${fmt(p.adr, 1)}</td>
      <td class="mono">${pctDirect(p.hs_rate)}</td>
      <td class="mono">${pctDirect(p.kast)}</td>
      <td class="mono ${rc}">${fmt(p.rating, 2)}</td>
    </tr>`;
  }).join('');
  return `<table class="data-table">
    <thead><tr>
      <th>Player</th><th>K</th><th>D</th><th>K/D</th>
      <th>ADR</th><th>HS%</th><th>KAST</th><th>Rating</th>
    </tr></thead>
    <tbody>${trs}</tbody>
  </table>`;
}

// Click-through to player analysis
document.addEventListener('click', async (e) => {
  const tr = e.target.closest('tr[data-player]');
  if (!tr) return;
  const player = tr.dataset.player;
  if (!player) return;
  setCurrentMomentPlayer(player, 'team-scoreboard');
  navigateTo('player');
  $('#player-select').value = player;
  await analyzePlayer(player);
});

// â”€â”€ Overview view â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
async function renderOverview() {
  const body = $('#overview-body');
  if (!State.demoId) return;
  body.innerHTML = '<div class="loading-placeholder">Loading...</div>';

  try {
    if (!State.teamAnalysis) {
      State.teamAnalysis = await API.getTeamAnalysis(State.demoId);
    }
    let highlightBundle = { highlights: [] };
    try {
      highlightBundle = await ensureHighlightsLoaded();
    } catch (highlightErr) {
      console.warn('Highlights failed to load:', highlightErr);
    }
    const ta = State.teamAnalysis;
    const sb = ta.scoreboard || [];

    body.innerHTML = '';
    body.appendChild(
      createCurrentMomentSection({
        subtitle: 'Keep one selected moment while moving between replay, clips, team, and coaching.',
      }),
    );

    // Overview stat cards
    const score = ta.score || {};
    const s1 = score.team1 ?? '-';
    const s2 = score.team2 ?? '-';
    const statsRow = el('div', 'overview-stats-row');
    [
      { label: 'Map',    value: State.mapName || '-',   sub: '' },
      { label: 'Rounds', value: State.totalRounds || '-', sub: 'played' },
      { label: 'Score',  value: `${s1} - ${s2}`,         sub: 'Team 1 vs Team 2' },
      { label: 'Players',value: sb.length || '-',         sub: 'tracked' },
    ].forEach(c => {
      statsRow.appendChild(el('div', 'overview-stat-card', `
        <div class="osc-label">${c.label}</div>
        <div class="osc-value" style="font-size:${c.label === 'Score' ? '22px' : '28px'}">${c.value}</div>
        ${c.sub ? `<div class="osc-sub">${c.sub}</div>` : ''}
      `));
    });
    body.appendChild(statsRow);

    // Split scoreboard: Team 1 | Team 2
    const teams   = ta.teams || {};
    const team1   = teams.team1 || {};
    const team2   = teams.team2 || {};
    const sb1     = sb.filter(p => p.team === 'team1');
    const sb2     = sb.filter(p => p.team === 'team2');

    const splitWrap = el('div', 'teams-split');

    [{ id: 'team1', data: team1, rows: sb1, dotCls: 't' },
     { id: 'team2', data: team2, rows: sb2, dotCls: 'ct' }].forEach(({ id, data, rows, dotCls }) => {
      const agg = data.aggregate || {};
      const card = el('div', 'team-card');
      card.innerHTML = `
        <div class="team-card-title">
          <span class="team-dot ${dotCls}"></span>
          ${data.name || id} &nbsp;
          <span style="font-size:11px;font-weight:400;color:var(--text-muted)">
            ADR ${fmt(agg.team_adr, 1)}  |  KAST ${pctDirect(agg.team_kast)}  |  Avg Rating ${fmt(agg.avg_rating, 2)}
          </span>
        </div>
      `;
      card.innerHTML += buildTeamTable(rows);
      splitWrap.appendChild(card);
    });

    body.appendChild(splitWrap);
    body.appendChild(
      renderHighlightsSection({
        title: 'Match Highlights',
        subtitle: 'Important moments detected from the parsed match timeline.',
        highlights: highlightBundle.highlights || [],
        emptyMessage: 'No match highlights were detected for this demo.',
        showPlayer: true,
      }),
    );

  } catch (err) {
    body.innerHTML = `<div class="empty-state"><div class="empty-state-title">Failed to load</div><div class="empty-state-sub">${err.message}</div></div>`;
  }
}

// â”€â”€ Player analysis view â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
$('#btn-analyze').addEventListener('click', () => {
  const player = $('#player-select').value;
  if (player) analyzePlayer(player);
});

async function analyzePlayer(playerName) {
  const body   = $('#player-body');
  const status = $('#player-analyze-status');
  body.innerHTML = '';
  status.textContent = 'Analyzing...';
  setCurrentMomentPlayer(playerName, 'player-analysis');
  if (State.currentPlayer !== playerName) {
    State.playerSideFilter = 'both';
    State.currentPlayer = playerName;
  }
  try {
    if (!State.analyses[playerName]) {
      State.analyses[playerName] = await API.analyzePlayer(State.demoId, playerName);
    }
    const cachedSteam = State.playerSteamProfiles[playerName] || null;
    const steamAgeMs = cachedSteam ? (Date.now() - Number(cachedSteam._fetchedAt || 0)) : Number.POSITIVE_INFINITY;
    const needsSteamFetch =
      !cachedSteam ||
      !cachedSteam.available ||
      steamAgeMs > 5 * 60 * 1000;
    if (needsSteamFetch) {
      try {
        // Always pass steamid64 directly — avoids wrong-profile issues from name-based lookup
        const knownSteamId = State.playerSteamIds[playerName] || '';
        const steamRes = await API.getPlayerSteamProfile(State.demoId, playerName, {
          refresh: Boolean(cachedSteam && !cachedSteam.available),
          debug: steamDebugEnabled,
          steamid64: knownSteamId,
        });
        State.playerSteamProfiles[playerName] = {
          ...steamRes,
          _fetchedAt: Date.now(),
        };

        // Debug log — always shown when steamDebugEnabled, otherwise only on failure
        const resolvedSid = steamRes?.steamid64 || knownSteamId || 'unknown';
        const profileUrl  = steamRes?.profile_url || 'unavailable';
        if (steamDebugEnabled) {
          console.debug('[steam-profile-debug]', steamRes.debug || {});
          console.log(`[steam] Player: ${playerName}  SteamID64: ${resolvedSid}  Profile: ${profileUrl}`);
        }
        if (!steamRes?.available) {
          const prevReason = String(cachedSteam?.reason || '').trim();
          const nextReason = String(steamRes?.reason || '').trim();
          if (steamDebugEnabled || prevReason !== nextReason) {
            console.warn('[steam-profile-unavailable]', {
              player: playerName,
              steamid64: resolvedSid,
              profile_url: profileUrl,
              reason: nextReason || 'unknown',
            });
          }
        }
      } catch (steamErr) {
        State.playerSteamProfiles[playerName] = {
          player: playerName,
          available: false,
          steamid64: null,
          personaname: playerName,
          avatar_url: null,
          profile_url: null,
          reason: steamErr.message || 'steam_request_failed',
          _fetchedAt: Date.now(),
        };
        if (steamDebugEnabled) {
          console.error('[steam-profile-request-error]', steamErr);
        }
      }
    }
    if (!State.playerVisuals[playerName]) {
      status.textContent = 'Rendering visuals...';
      try {
        State.playerVisuals[playerName] = await API.getPlayerVisuals(State.demoId, playerName);
      } catch (visErr) {
        State.playerVisuals[playerName] = {
          error: visErr.message || 'Failed to generate visuals',
        };
      }
    }
    if (!State.highlightsBundle) {
      status.textContent = 'Loading highlights...';
      try {
        await ensureHighlightsLoaded();
      } catch (highlightErr) {
        console.warn('Player highlights failed to load:', highlightErr);
      }
    }
    status.textContent = '';
    safeRenderPlayerBody(
      body,
      State.analyses[playerName],
      State.playerVisuals[playerName],
      State.playerSteamProfiles[playerName] || null,
    );
  } catch (err) {
    status.textContent = `Error: ${err.message}`;
    showToast(err.message, { type: 'error', title: 'Analysis failed' });
  }
}

function renderPlayerBody(body, data, visuals = null, steamProfile = null) {
  body.innerHTML = '';

  const stats = data.stats || {};
  const adv   = data.advanced || {};
  const pro   = adv.pro_metrics || {};
  const sideStats = adv.side_stats || {};
  const tSide  = sideStats.t_side  || {};
  const ctSide = sideStats.ct_side || {};
  if (!['both', 'ct', 't'].includes(State.playerSideFilter)) State.playerSideFilter = 'both';
  const selectedSide = State.playerSideFilter;
  const activeSide = selectedSide === 't' ? tSide : selectedSide === 'ct' ? ctSide : null;
  const sideProfiles = pro.sides || {};
  const sideProfile = selectedSide === 'both'
    ? (sideProfiles.both || {})
    : (sideProfiles[selectedSide] || {});

  const selectedRounds = selectedSide === 'both'
    ? toNum(data.total_rounds, toNum(sideProfile.rounds, 0))
    : toNum(sideProfile.rounds, toNum(activeSide?.rounds, 0));

  const mkGlobal = adv.multi_kills || {};
  const clutchesGlobal = adv.clutches || [];

  const openingData = selectedSide === 'both'
    ? (pro.opening || {})
    : (sideProfile.opening || {});
  const snipingData = selectedSide === 'both'
    ? (pro.sniping || {})
    : (sideProfile.sniping || {});
  const utilEff = selectedSide === 'both'
    ? (pro.utility_effectiveness || {})
    : (sideProfile.utility_effectiveness || {});
  const tradeStats = selectedSide === 'both'
    ? (adv.trade_stats || {})
    : (sideProfile.trading || {});
  const duels = selectedSide === 'both'
    ? (pro.duels || {})
    : (sideProfile.duels || {});
  const mkSelected = selectedSide === 'both'
    ? mkGlobal
    : (sideProfile.multi_kills || {});

  const killsSelected = selectedSide === 'both'
    ? toNum(stats.kills, toNum(sideProfile.kills, 0))
    : toNum(sideProfile.kills, toNum(activeSide?.kills, 0));
  const deathsSelected = selectedSide === 'both'
    ? toNum(stats.deaths, toNum(sideProfile.deaths, 0))
    : toNum(sideProfile.deaths, toNum(activeSide?.deaths, 0));
  const assistsVal = selectedSide === 'both'
    ? toNum(stats.assists, toNum(sideProfile.assists, 0))
    : toNum(sideProfile.assists, toNum(activeSide?.assists, 0));
  const adr = selectedSide === 'both'
    ? toNum(stats.adr, toNum(sideProfile.adr, 0))
    : toNum(sideProfile.adr, toNum(activeSide?.adr, 0));
  const kd = selectedSide === 'both'
    ? toNum(stats.kd_ratio, toNum(sideProfile.kd_ratio, 0))
    : toNum(sideProfile.kd_ratio, toNum(activeSide?.kd_ratio, 0));
  const hsRate = selectedSide === 'both'
    ? toNum(stats.hs_rate, toNum(sideProfile.hs_rate, 0))
    : toNum(sideProfile.hs_rate, toNum(activeSide?.hs_rate, 0));
  const kpr = selectedSide === 'both'
    ? toNum(pro.kpr, toNum(stats.kills_per_round, toNum(sideProfile.kpr, 0)))
    : toNum(sideProfile.kpr, toNum(activeSide?.kpr, 0));
  const dpr = selectedSide === 'both'
    ? toNum(pro.dpr, toNum(stats.deaths_per_round, toNum(sideProfile.dpr, 0)))
    : toNum(sideProfile.dpr, toNum(activeSide?.dpr, 0));
  const apr = selectedSide === 'both'
    ? toNum(stats.assists, 0) / Math.max(selectedRounds || 1, 1)
    : toNum(sideProfile.apr, toNum(activeSide?.apr, 0));
  const kast = selectedSide === 'both'
    ? toNum(adv.kast?.kast_percentage, toNum(sideProfile.kast_percentage, 0))
    : toNum(sideProfile.kast_percentage, toNum(adv.kast?.kast_percentage, 0));
  const rating = selectedSide === 'both'
    ? toNum(pro.hltv_rating, toNum(sideProfile.hltv_rating, 0))
    : toNum(sideProfile.hltv_rating, toNum(pro.hltv_rating, 0));
  const impact = selectedSide === 'both'
    ? toNum(pro.impact_rating, toNum(sideProfile.impact_rating, 0))
    : toNum(sideProfile.impact_rating, toNum(pro.impact_rating, 0));
  const accuracy = selectedSide === 'both'
    ? toNum(stats.accuracy, toNum(sideProfile.accuracy, 0))
    : toNum(sideProfile.accuracy, toNum(stats.accuracy, 0));
  const totalDamageSelected = selectedSide === 'both'
    ? toNum(stats.total_damage, toNum(sideProfile.total_damage, 0))
    : toNum(sideProfile.total_damage, toNum(sideProfile.adr, 0) * Math.max(selectedRounds, 0));
  const openingKillsSelected = selectedSide === 'both'
    ? toNum(stats.opening_kills, toNum(openingData.opening_kills, 0))
    : toNum(openingData.opening_kills, toNum(activeSide?.opening_kills, 0));
  const openingDeathsSelected = selectedSide === 'both'
    ? toNum(stats.opening_deaths, toNum(openingData.opening_deaths, 0))
    : toNum(openingData.opening_deaths, toNum(activeSide?.opening_deaths, 0));
  const openingRate = selectedSide === 'both'
    ? toNum(stats.opening_win_rate, toNum(openingData.opening_success, 0))
    : toNum(openingData.opening_success, toNum(activeSide?.opening_success, 0));
  const entryRate = selectedSide === 'both'
    ? toNum(pro.entry_success_rate, openingRate)
    : toNum(sideProfile.entry_success_rate, openingRate);
  const openingKprSelected = openingKillsSelected / Math.max(selectedRounds || 1, 1);
  const openingDprSelected = openingDeathsSelected / Math.max(selectedRounds || 1, 1);
  const openingAttemptsPrSelected = (openingKillsSelected + openingDeathsSelected) / Math.max(selectedRounds || 1, 1);
  const openingSuccessSelected = openingRate;
  const tradeKillRate = toNum(tradeStats.trade_kill_rate, 0);
  const tradedRate = toNum(tradeStats.traded_rate, 0);
  const tradeKillsPr = toNum(tradeStats.trade_kills, 0) / Math.max(selectedRounds || 1, 1);
  const trading = (tradeKillRate * 0.65) + (tradedRate * 0.35);
  const utilityScore = toNum(utilEff.utility_score, 0);
  const duelHasData = toNum(duels.total_duels, 0) > 0;
  const duelWin = duelHasData ? toNum(duels.duel_win_rate, 0) : NaN;
  const duelText = duelHasData ? pctDirect(duelWin) : 'N/A';
  const sniperKillsSelected = selectedSide === 'both'
    ? toNum(snipingData.sniper_kills, 0)
    : toNum(sideProfile.sniping?.sniper_kills, toNum(activeSide?.sniper_kills, 0));
  const sniperPct = selectedSide === 'both'
    ? toNum(snipingData.sniper_kill_percentage, 0)
    : (sniperKillsSelected / Math.max(killsSelected, 1)) * 100;
  const sniperKpr = selectedSide === 'both'
    ? toNum(snipingData.sniper_kills_per_round, 0)
    : toNum(sideProfile.sniping?.sniper_kills_per_round, toNum(activeSide?.sniper_kills_per_round, 0));
  const roundsWithKillPct = toNum(openingData.rounds_with_kill_pct, 0);
  const roundsWithMultiKillPct = toNum(openingData.rounds_with_multi_kill_pct, 0);
  const clutchAttempts = selectedSide === 'both'
    ? clutchesGlobal.length
    : toNum(sideProfile.clutches?.attempts, 0);
  const clutchWon = selectedSide === 'both'
    ? clutchesGlobal.filter(c => c.won).length
    : toNum(sideProfile.clutches?.won, 0);
  const clutchRate = selectedSide === 'both'
    ? (clutchAttempts ? (clutchWon / clutchAttempts) * 100 : 0)
    : toNum(sideProfile.clutches?.win_rate, 0);
  const mvpRounds = (mkSelected.total_4k ? (mkSelected.total_4k + (mkSelected.total_aces || 0)) : (mkSelected.total_aces || 0));
  const sideLabel = selectedSide === 'both' ? 'Both Sides' : selectedSide === 'ct' ? 'CT Side' : 'T Side';
  const sideTag = selectedSide.toUpperCase();

  const row = (label, valueText, rawValue, min, max, avgValue, inverse = false) => ({
    label,
    valueText,
    valuePct: scaleToPct(rawValue, min, max, inverse),
    avgPct: scaleToPct(avgValue, min, max, inverse),
  });

  const topSection = el('div', 'player-hero');
  const ratingStatusClass = metricStatus('rating', rating);
  const ratingStatusText = statusTextFromClass(ratingStatusClass);
  const totalDamageAvg = 74.9 * Math.max(selectedRounds || 1, 1);

  const heroMetricDefs = [
    {
      metricKey: 'adr',
      label: 'ADR',
      valueHtml: fmt(adr, 1),
      subHtml: 'Avg damage / round',
      barCfg: { valuePct: scaleToPct(adr, 0, 140), avgPct: scaleToPct(74.9, 0, 140) },
      eval: { value: adr, avg: 74.9, direction: 'higher', rangeMin: 0, rangeMax: 140 },
    },
    {
      metricKey: 'kd',
      label: 'K/D',
      valueHtml: fmt(kd, 2),
      subHtml: `${killsSelected} / ${deathsSelected}`,
      barCfg: { valuePct: scaleToPct(kd, 0.4, 1.8), avgPct: scaleToPct(1.0, 0.4, 1.8) },
      eval: { value: kd, avg: 1.0, direction: 'higher', rangeMin: 0.4, rangeMax: 1.8 },
    },
    {
      metricKey: 'kast',
      label: 'KAST',
      valueHtml: pctDirect(kast),
      subHtml: 'Kill/Assist/Survive/Trade',
      barCfg: { valuePct: scaleToPct(kast, 45, 85), avgPct: scaleToPct(71.9, 45, 85) },
      eval: { value: kast, avg: 71.9, direction: 'higher', rangeMin: 45, rangeMax: 85 },
    },
    {
      metricKey: 'hs',
      label: 'HS%',
      valueHtml: pctDirect(hsRate),
      subHtml: `Accuracy: ${pctDirect(accuracy)}`,
      barCfg: { valuePct: scaleToPct(hsRate, 10, 65), avgPct: scaleToPct(40, 10, 65) },
      eval: { value: hsRate, avg: 40, direction: 'higher', rangeMin: 10, rangeMax: 65 },
    },
    {
      metricKey: 'dpr',
      label: 'DPR',
      valueHtml: fmt(dpr, 2),
      subHtml: 'Deaths per round',
      barCfg: { valuePct: scaleToPct(dpr, 0.35, 1.1, true), avgPct: scaleToPct(0.65, 0.35, 1.1, true) },
      eval: { value: dpr, avg: 0.65, direction: 'lower', rangeMin: 0.35, rangeMax: 1.1 },
    },
    {
      metricKey: 'kpr',
      label: 'KPR',
      valueHtml: fmt(kpr, 2),
      subHtml: 'Kills per round',
      barCfg: { valuePct: scaleToPct(kpr, 0.25, 1.05), avgPct: scaleToPct(0.72, 0.25, 1.05) },
      eval: { value: kpr, avg: 0.72, direction: 'higher', rangeMin: 0.25, rangeMax: 1.05 },
    },
    {
      metricKey: 'opening',
      label: 'Opening W/L',
      valueHtml: `${openingKillsSelected}/${openingDeathsSelected}`,
      subHtml: `${pctDirect(openingRate)} success`,
      barCfg: { valuePct: scaleToPct(openingRate, 20, 80), avgPct: scaleToPct(50, 20, 80) },
      eval: { value: openingRate, avg: 50, direction: 'higher', rangeMin: 20, rangeMax: 80 },
    },
    {
      metricKey: 'adr',
      label: 'Total Damage',
      valueHtml: Math.round(totalDamageSelected),
      subHtml: `${selectedRounds} rounds`,
      barCfg: {
        valuePct: scaleToPct(totalDamageSelected, 0, Math.max(selectedRounds, 1) * 160),
        avgPct: scaleToPct(totalDamageAvg, 0, Math.max(selectedRounds, 1) * 160),
      },
      eval: { value: totalDamageSelected, avg: totalDamageAvg, direction: 'higher', rangeMin: 0, rangeMax: Math.max(selectedRounds, 1) * 160 },
    },
    {
      metricKey: 'accuracy',
      label: 'Accuracy',
      valueHtml: pctDirect(accuracy),
      subHtml: `Side: ${sideTag}`,
      barCfg: { valuePct: scaleToPct(accuracy, 8, 45), avgPct: scaleToPct(23, 8, 45) },
      eval: { value: accuracy, avg: 23, direction: 'higher', rangeMin: 8, rangeMax: 45 },
    },
    {
      metricKey: 'entry',
      label: 'A / Round',
      valueHtml: fmt(apr, 2),
      subHtml: `${assistsVal} assists`,
      barCfg: { valuePct: scaleToPct(apr, 0, 0.35), avgPct: scaleToPct(0.11, 0, 0.35) },
      eval: { value: apr, avg: 0.11, direction: 'higher', rangeMin: 0, rangeMax: 0.35 },
    },
  ];

  const heroGroups = { strong: [], avg: [], weak: [] };
  heroMetricDefs.forEach((metric) => {
    const statusCls = statusClassFromBenchmark(
      metric.eval.value,
      metric.eval.avg,
      metric.eval.direction,
      0.12,
      metric.eval.rangeMin,
      metric.eval.rangeMax,
    );
    const cardHtml = metricCardHtml(
      metric.metricKey,
      metric.label,
      metric.valueHtml,
      metric.subHtml,
      metric.barCfg,
      statusCls,
    );
    if (statusCls === 'status-strong') heroGroups.strong.push(cardHtml);
    else if (statusCls === 'status-avg') heroGroups.avg.push(cardHtml);
    else heroGroups.weak.push(cardHtml);
  });

  const profile = steamProfile || {};
  const avatarUrl = String(profile.avatar_url || '').trim();
  const profileUrl = String(profile.profile_url || '').trim();
  const hasAvatar = isLikelyHttpUrl(avatarUrl);
  const hasProfileUrl = isLikelyHttpUrl(profileUrl);
  const displayPlayerName = data.player || 'Player';
  const personaName = typeof profile.personaname === 'string' ? profile.personaname.trim() : '';
  const steamReason = String(profile.reason || '').trim();
  const initial = (displayPlayerName || 'P').trim().charAt(0).toUpperCase();
  const avatarHtml = hasAvatar
    ? `<img class="player-hero-avatar" src="${avatarUrl}" alt="${displayPlayerName} avatar" loading="lazy" referrerpolicy="no-referrer" />`
    : `<div class="player-hero-avatar-fallback">${initial}</div>`;
  const profileBtnHtml = hasProfileUrl
    ? `<a class="btn btn-secondary player-profile-btn" href="${profileUrl}" target="_blank" rel="noopener noreferrer">Player Profile</a>`
    : `<button class="btn btn-secondary player-profile-btn" type="button" disabled title="Steam profile unavailable">Player Profile</button>`;

  topSection.innerHTML = `
    <div class="player-hero-head">
      <div class="player-hero-identity">
        <div class="player-hero-avatar-wrap">${avatarHtml}</div>
        <div class="player-hero-meta">
          <h2 class="player-hero-title">${displayPlayerName}</h2>
          ${personaName && personaName !== displayPlayerName ? `<div class="player-hero-persona">Steam: ${personaName}</div>` : ''}
          ${!hasAvatar && steamReason ? `<div class="player-hero-persona">Steam unavailable: ${steamReason}</div>` : ''}
        <div class="player-hero-sub">${data.map || '-'} | ${selectedRounds || '-'} rounds | ${sideLabel}</div>
          <div class="player-hero-profile-wrap">${profileBtnHtml}</div>
        </div>
      </div>
      <div class="player-hero-rating ${ratingStatusClass}">
        <span>Rating</span>
        <strong>${fmt(rating, 2)}</strong>
        <em>${ratingStatusText}</em>
      </div>
    </div>
    <div class="hero-metric-groups">
      ${metricGroupHtml('Strong Performance', 'status-strong', heroGroups.strong)}
      ${metricGroupHtml('Average Performance', 'status-avg', heroGroups.avg)}
      ${metricGroupHtml('Weak Performance', 'status-weak', heroGroups.weak)}
    </div>
  `;
  body.appendChild(topSection);

  const categorySection = el('div', 'section');
  categorySection.appendChild(el('div', 'section-title', 'Category Metrics'));
  const categoryToolbar = el('div', 'metric-toolbar');
  categoryToolbar.innerHTML = `
    <div class="metric-toolbar-group">
      <span class="metric-toolbar-label">Side:</span>
      <button class="metric-side-btn ${selectedSide === 'both' ? 'active' : ''}" data-side="both">Both Sides</button>
      <button class="metric-side-btn ${selectedSide === 'ct' ? 'active' : ''}" data-side="ct">CT Side</button>
      <button class="metric-side-btn ${selectedSide === 't' ? 'active' : ''}" data-side="t">T Side</button>
    </div>
    <div class="metric-toolbar-meta">
      <span>Stats per:</span>
      <strong>Round</strong>
      <span>${selectedRounds || 0} rounds</span>
    </div>
  `;
  categorySection.appendChild(categoryToolbar);
  const firepowerScore = clamp((adr / 110) * 35 + (kpr / 0.9) * 40 + (hsRate / 55) * 25, 0, 100);
  const entryScore = clamp((openingRate / 65) * 65 + (openingKillsSelected / 8) * 35, 0, 100);
  const openingScore = clamp((openingSuccessSelected / 70) * 65 + (openingAttemptsPrSelected / 0.35) * 35, 0, 100);
  const tradingScore = clamp((trading / 60) * 100, 0, 100);
  const snipingScore = clamp((sniperPct / 60) * 60 + (sniperKpr / 0.45) * 40, 0, 100);
  const utilityScoreCard = clamp((utilityScore / 90) * 80 + (toNum(utilEff.flash_assists, 0) / 6) * 20, 0, 100);
  const clutchScore = clamp((clutchRate / 60) * 75 + (clutchWon / 4) * 25, 0, 100);
  const consistencyScore = clamp((kast / 80) * 70 + (toNum(pro.spr, 0.5) / 0.9) * 30, 0, 100);
  const impactScore = clamp((impact / 1.4) * 60 + ((duelHasData ? duelWin : 50) / 65) * 40, 0, 100);
  const positioningScore = clamp(
    (100 - clamp((adv.death_clusters?.clusters?.[0]?.count || 0) * 16, 0, 100)) * 0.45 +
    clamp((toNum(pro.spr, 0.5) / 0.9) * 55, 0, 100) * 0.55,
    0,
    100,
  );

  const wrappers = [
    { title: 'Firepower', score: firepowerScore, rows: [
      row('Kills per round', fmt(kpr, 2), kpr, 0.2, 1.1, 0.72),
      row('Damage per round', fmt(adr, 1), adr, 30, 140, 74.9),
      row('Rating 1.0', fmt(rating, 2), rating, 0.5, 1.6, 1.0),
      row('Rounds with a kill', pctDirect(roundsWithKillPct), roundsWithKillPct, 0, 100, 48.6),
      row('Rounds with multi-kill', pctDirect(roundsWithMultiKillPct), roundsWithMultiKillPct, 0, 100, 17.9),
    ]},
    { title: 'Entrying', score: entryScore, rows: [
      row('Opening kills per round', fmt(openingKprSelected, 2), openingKprSelected, 0, 0.35, 0.08),
      row('Opening deaths per round', fmt(openingDprSelected, 2), openingDprSelected, 0, 0.35, 0.08, true),
      row('Opening attempts per round', fmt(openingAttemptsPrSelected, 2), openingAttemptsPrSelected, 0, 0.5, 0.19),
      row('Opening success', pctDirect(openingSuccessSelected), openingSuccessSelected, 0, 100, 50),
      row('Assists per round', fmt(apr, 2), apr, 0, 0.4, 0.11),
    ]},
    { title: 'Trading', score: tradingScore, rows: [
      row('Trade kills per round', fmt(tradeKillsPr, 2), tradeKillsPr, 0, 0.35, 0.14),
      row('Trade kill percentage', pctDirect(tradeKillRate), tradeKillRate, 0, 100, 19.7),
      row('Traded deaths percentage', pctDirect(tradedRate), tradedRate, 0, 100, 22.0),
      row('Assisted kills percentage', pctDirect(assistsVal / Math.max(killsSelected, 1) * 100), assistsVal / Math.max(killsSelected, 1) * 100, 0, 100, 19.2),
      row('Damage per kill', fmt(totalDamageSelected / Math.max(killsSelected, 1), 0), totalDamageSelected / Math.max(killsSelected, 1), 40, 160, 103),
    ]},
    { title: 'Opening', score: openingScore, rows: [
      row('Opening kills per round', fmt(openingKprSelected, 2), openingKprSelected, 0, 0.35, 0.12),
      row('Opening deaths per round', fmt(openingDprSelected, 2), openingDprSelected, 0, 0.3, 0.08, true),
      row('Opening attempts', pctDirect(openingAttemptsPrSelected * 100), openingAttemptsPrSelected * 100, 0, 40, 19.1),
      row('Opening success', pctDirect(openingSuccessSelected), openingSuccessSelected, 0, 100, 60.3),
      row('Entry success', pctDirect(entryRate), entryRate, 0, 100, 55),
    ]},
    { title: 'Clutching', score: clutchScore, rows: [
      row('Clutch points per round', fmt(clutchWon / Math.max(selectedRounds || 1, 1), 2), clutchWon / Math.max(selectedRounds || 1, 1), 0, 0.2, 0.03),
      row('Clutch attempts / round', fmt(clutchAttempts / Math.max(selectedRounds || 1, 1), 2), clutchAttempts / Math.max(selectedRounds || 1, 1), 0, 0.4, 0.14),
      row('1vX win percentage', pctDirect(clutchRate), clutchRate, 0, 100, 61.5),
      row('Survival per round', fmt(selectedSide === 'both' ? toNum(pro.spr, 0) : toNum(sideProfile.spr, 0), 2), selectedSide === 'both' ? toNum(pro.spr, 0) : toNum(sideProfile.spr, 0), 0.2, 1.0, 0.70),
      row('Saves / round', fmt((Math.max(selectedRounds, 0) - deathsSelected) / Math.max(selectedRounds || 1, 1), 2), (Math.max(selectedRounds, 0) - deathsSelected) / Math.max(selectedRounds || 1, 1), 0, 1.0, 0.45),
    ]},
    { title: 'Sniping', score: snipingScore, rows: [
      row('Sniper kills per round', fmt(sniperKpr, 2), sniperKpr, 0, 0.6, 0.39),
      row('Sniper kills percentage', pctDirect(sniperPct), sniperPct, 0, 100, 54.3),
      row('Rounds with sniper kills %', pctDirect(toNum(snipingData.rounds_with_sniper_kill_percentage, 0)), toNum(snipingData.rounds_with_sniper_kill_percentage, 0), 0, 100, 30.0),
      row('Sniper multi-kill rounds', fmt(toNum(snipingData.sniper_multi_kill_rounds_per_round, 0), 2), toNum(snipingData.sniper_multi_kill_rounds_per_round, 0), 0, 0.3, 0.10),
      row('Sniper opening kills / round', fmt(toNum(snipingData.sniper_opening_kills_per_round, 0), 2), toNum(snipingData.sniper_opening_kills_per_round, 0), 0, 0.2, 0.09),
    ]},
    { title: 'Utility', score: utilityScoreCard, rows: [
      row('Utility damage / round', fmt(toNum(utilEff.utility_damage, 0) / Math.max(selectedRounds || 1, 1), 2), toNum(utilEff.utility_damage, 0) / Math.max(selectedRounds || 1, 1), 0, 20, 2.62),
      row('Utility throws / round', fmt(toNum(utilEff.total_utility, 0) / Math.max(selectedRounds || 1, 1), 2), toNum(utilEff.total_utility, 0) / Math.max(selectedRounds || 1, 1), 0, 2.2, 0.66),
      row('Flash assists / round', fmt(toNum(utilEff.flash_assists, 0) / Math.max(selectedRounds || 1, 1), 2), toNum(utilEff.flash_assists, 0) / Math.max(selectedRounds || 1, 1), 0, 0.3, 0.06),
      row('Smoke throws / round', fmt(toNum(utilEff.smoke_count, 0) / Math.max(selectedRounds || 1, 1), 2), toNum(utilEff.smoke_count, 0) / Math.max(selectedRounds || 1, 1), 0, 0.8, 0.25),
      row('Utility score', fmt(utilityScore, 1), utilityScore, 0, 100, 51),
    ]},
    { title: 'Impact', score: impactScore, rows: [
      row('Impact rating', fmt(impact, 2), impact, 0.3, 1.8, 1.0),
      row('Duel win percentage', duelText, duelHasData ? duelWin : 50, 0, 100, 50),
      row('Entry success', pctDirect(entryRate), entryRate, 0, 100, 55),
      row('KAST', pctDirect(kast), kast, 40, 85, 71.9),
      row('ADR', fmt(adr, 1), adr, 20, 140, 74.9),
    ]},
    { title: 'Consistency', score: consistencyScore, rows: [
      row('DPR', fmt(dpr, 2), dpr, 0.35, 1.1, 0.61, true),
      row('KAST', pctDirect(kast), kast, 45, 85, 71.9),
      row('KPR', fmt(kpr, 2), kpr, 0.2, 1.05, 0.72),
      row('SPR', fmt(selectedSide === 'both' ? toNum(pro.spr, 0) : toNum(sideProfile.spr, 0), 2), selectedSide === 'both' ? toNum(pro.spr, 0) : toNum(sideProfile.spr, 0), 0.2, 1.0, 0.70),
      row('Rating', fmt(rating, 2), rating, 0.4, 1.7, 1.0),
    ]},
    { title: 'Positioning', score: positioningScore, rows: [
      row('DPR (inverse)', fmt(dpr, 2), dpr, 0.35, 1.1, 0.61, true),
      row('Survival per round', fmt(selectedSide === 'both' ? toNum(pro.spr, 0) : toNum(sideProfile.spr, 0), 2), selectedSide === 'both' ? toNum(pro.spr, 0) : toNum(sideProfile.spr, 0), 0.2, 1.0, 0.70),
      row('Death cluster risk', String(adv.death_clusters?.clusters?.[0]?.count || 0), toNum(adv.death_clusters?.clusters?.[0]?.count, 0), 0, 8, 3, true),
      row('Avg step distance', fmt(stats.avg_step_distance, 1), toNum(stats.avg_step_distance, 0), 0, 220, 95),
      row('Stationary ratio', pctDirect(stats.stationary_ratio), toNum(stats.stationary_ratio, 0), 0, 100, 35, true),
    ]},
  ];

  const grouped = { strong: [], avg: [], weak: [] };
  wrappers.forEach((w) => {
    const cls = scoreToStatusClass(w.score);
    if (cls === 'status-strong') grouped.strong.push(w);
    else if (cls === 'status-avg') grouped.avg.push(w);
    else grouped.weak.push(w);
  });

  const groupSpecs = [
    { key: 'strong', title: 'Strong Metrics' },
    { key: 'avg', title: 'Average Metrics' },
    { key: 'weak', title: 'Weak Metrics' },
  ];

  groupSpecs.forEach((g) => {
    if (!grouped[g.key].length) return;
    const block = el('div', 'metric-color-group');
    block.appendChild(el('div', 'metric-color-group-title', g.title));
    const groupGrid = el('div', 'metric-wrapper-grid');
    grouped[g.key].forEach((w) => {
      groupGrid.appendChild(el('div', '', metricWrapperHtml(w.title, w.score, w.rows, false)));
    });
    block.appendChild(groupGrid);
    categorySection.appendChild(block);
  });

  body.appendChild(categorySection);
  categorySection.querySelectorAll('.metric-side-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      const nextSide = btn.dataset.side || 'both';
      if (nextSide === State.playerSideFilter) return;
      State.playerSideFilter = nextSide;
      safeRenderPlayerBody(body, data, visuals, steamProfile);
    });
  });

  const detailSection = el('div', 'section');
  detailSection.appendChild(el('div', 'section-title', 'Detailed Statistics'));
  const detailGrid = el('div', 'detail-blocks-grid');
  const detailBlocks = [
    {
      title: 'Combat Totals',
      items: [
        { label: 'Total Kills', value: killsSelected },
        { label: 'Total Deaths', value: deathsSelected },
        { label: 'Total Assists', value: assistsVal },
        { label: 'Total Damage', value: Math.round(totalDamageSelected) },
        { label: 'HS%', value: pctDirect(hsRate) },
        { label: 'Accuracy', value: pctDirect(accuracy) },
      ],
    },
    {
      title: 'Per-Round Profile',
      items: [
        { label: 'Rounds', value: selectedRounds },
        { label: 'ADR', value: fmt(adr, 1) },
        { label: 'KPR', value: fmt(kpr, 2) },
        { label: 'DPR', value: fmt(dpr, 2) },
        { label: 'APR', value: fmt(apr, 2) },
        { label: 'KAST', value: pctDirect(kast) },
      ],
    },
    {
      title: 'Opening / Clutch',
      items: [
        { label: 'Opening W/L', value: `${openingKillsSelected}/${openingDeathsSelected}` },
        { label: 'Opening Success', value: pctDirect(openingRate) },
        { label: 'Entry Success', value: pctDirect(entryRate) },
        { label: 'Clutch W/L', value: `${clutchWon}/${clutchAttempts}` },
        { label: 'Duel Win', value: duelText },
        { label: 'MVP Rounds', value: mvpRounds },
      ],
    },
    {
      title: 'Utility / Sniping',
      items: [
        { label: 'Utility Score', value: fmt(utilityScore, 1) },
        { label: 'Utility Damage', value: utilEff.utility_damage ?? 0 },
        { label: 'Utility Throws', value: utilEff.total_utility ?? 0 },
        { label: 'Flash Assists', value: utilEff.flash_assists ?? 0 },
        { label: 'Sniper Kills', value: sniperKillsSelected },
        { label: 'Sniper KPR', value: fmt(sniperKpr, 2) },
      ],
    },
  ];
  detailBlocks.forEach((block) => {
    detailGrid.appendChild(el('div', '', detailBlockHtml(block.title, block.items)));
  });
  detailSection.appendChild(detailGrid);
  body.appendChild(detailSection);

  const allHighlights = State.highlightsBundle?.highlights || [];
  const playerHighlights = filterHighlights(
    allHighlights.filter((item) => {
      const primary = String(item?.primary_player || '');
      const involved = Array.isArray(item?.involved_players) ? item.involved_players : [];
      return primary === data.player || involved.includes(data.player);
    }),
    'all',
  ).slice(0, 10);
  body.appendChild(
    renderHighlightsSection({
      title: 'Player Highlights',
      subtitle: `Moments involving ${data.player}.`,
      highlights: playerHighlights,
      emptyMessage: 'No player-specific highlights were detected.',
      showPlayer: false,
    }),
  );

  if (tSide.rounds || ctSide.rounds) {
    const sideSec = el('div', 'section');
    sideSec.appendChild(el('div', 'section-title', 'T-Side / CT-Side'));
    const sideGrid = el('div', 'teams-split', '');
    sideGrid.style.marginBottom = '0';
    sideGrid.innerHTML = `
      <div class="team-card">
        <div class="team-card-title"><span class="team-dot t"></span>T-Side (${tSide.rounds} rounds)</div>
        <div class="metrics-grid" style="margin-bottom:0">
          <div class="metric-card"><div class="metric-label">K/D</div><div class="metric-value">${fmt(tSide.kd_ratio, 2)}</div></div>
          <div class="metric-card"><div class="metric-label">Kills</div><div class="metric-value">${tSide.kills ?? '-'}</div></div>
          <div class="metric-card"><div class="metric-label">HS%</div><div class="metric-value">${pctDirect(tSide.hs_rate)}</div></div>
        </div>
      </div>
      <div class="team-card">
        <div class="team-card-title"><span class="team-dot ct"></span>CT-Side (${ctSide.rounds} rounds)</div>
        <div class="metrics-grid" style="margin-bottom:0">
          <div class="metric-card"><div class="metric-label">K/D</div><div class="metric-value">${fmt(ctSide.kd_ratio, 2)}</div></div>
          <div class="metric-card"><div class="metric-label">Kills</div><div class="metric-value">${ctSide.kills ?? '-'}</div></div>
          <div class="metric-card"><div class="metric-label">HS%</div><div class="metric-value">${pctDirect(ctSide.hs_rate)}</div></div>
        </div>
      </div>`;
    sideSec.appendChild(sideGrid);
    body.appendChild(sideSec);
  }

  const findings = data.findings || [];
  if (findings.length) {
    const sec = el('div', 'section');
    sec.appendChild(el('div', 'section-title', 'Key Findings'));
    const list = el('div', 'findings-list');
    findings.forEach(f => {
      const msg = typeof f === 'string' ? f : (f.message || '');
      const sev = typeof f === 'object' ? f.severity : '';
      const icon = sev === 'low' ? '&#10003;' : sev === 'high' ? '&#10007;' : 'i';
      const iconCls = sev === 'low' ? 'text-success' : sev === 'high' ? 'text-danger' : 'text-muted';
      list.appendChild(el('div', 'finding-item', `
        <span class="finding-icon ${iconCls}">${icon}</span>
        <span class="finding-text">${msg}</span>
      `));
    });
    sec.appendChild(list);
    body.appendChild(sec);
  }
  renderPlayerVisualsSection(body, visuals);
}

function safeRenderPlayerBody(body, data, visuals = null, steamProfile = null) {
  try {
    renderPlayerBody(body, data, visuals, steamProfile);
  } catch (err) {
    console.error('Player body render failed:', err);
    if (body) {
      body.innerHTML = `
        <div class="empty-state">
          <div class="empty-state-title">UI render error</div>
          <div class="empty-state-sub">Failed to render player metrics. Falling back to Both Sides.</div>
        </div>
      `;
    }
    if (State.playerSideFilter !== 'both') {
      State.playerSideFilter = 'both';
      try {
        renderPlayerBody(body, data, visuals, steamProfile);
      } catch (fallbackErr) {
        console.error('Player body fallback render failed:', fallbackErr);
      }
    }
  }
}

function renderPlayerVisualsSection(body, visuals) {
  const section = el('div', 'section');
  section.appendChild(el('div', 'section-title', 'Player Visuals'));

  if (!visuals) {
    section.appendChild(el('div', 'empty-state', '<div class="empty-state-sub">Visuals not loaded yet.</div>'));
    body.appendChild(section);
    return;
  }

  if (visuals.error) {
    section.appendChild(
      el(
        'div',
        'empty-state',
        `<div class="empty-state-title">Visual generation failed</div><div class="empty-state-sub">${visuals.error}</div>`,
      ),
    );
    body.appendChild(section);
    return;
  }

  const cards = [
    { key: 'heatmap_t_url', title: 'T-Side Heatmap', type: 'image' },
    { key: 'heatmap_ct_url', title: 'CT-Side Heatmap', type: 'image' },
    { key: 'utility_url', title: 'Utility Map', type: 'image' },
    { key: 'route_gif_url', title: 'Round Route GIF', type: 'gif' },
  ].filter((item) => Boolean(visuals[item.key]));

  if (!cards.length) {
    section.appendChild(el('div', 'empty-state', '<div class="empty-state-sub">No visual data for this player.</div>'));
    body.appendChild(section);
    return;
  }

  const grid = el('div', 'player-visuals-grid');
  cards.forEach((item) => {
    const card = el('div', 'player-visual-card');
    const mediaHtml =
      item.type === 'gif'
        ? `<img src="${visuals[item.key]}" alt="${item.title}" loading="lazy" />`
        : `<img src="${visuals[item.key]}" alt="${item.title}" loading="lazy" />`;
    card.innerHTML = `
      <div class="player-visual-head">
        <span>${item.title}</span>
        <a class="player-visual-link" href="${visuals[item.key]}" target="_blank" rel="noopener">Open</a>
      </div>
      <div class="player-visual-media">${mediaHtml}</div>
    `;
    grid.appendChild(card);
  });

  section.appendChild(grid);
  body.appendChild(section);
}

// â”€â”€ Team view â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
async function renderTeam() {
  const body = $('#team-body');
  if (!State.demoId) return;
  body.innerHTML = '<div class="loading-placeholder">Loading team analysis...</div>';

  try {
    if (!State.teamAnalysis) {
      State.teamAnalysis = await API.getTeamAnalysis(State.demoId);
    }
    const ta = State.teamAnalysis;
    body.innerHTML = '';
    body.appendChild(
      createCurrentMomentSection({
        subtitle: 'Anchor team analysis to your selected highlight, clip, or player focus.',
      }),
    );

    const teams = ta.teams || {};
    const sb    = ta.scoreboard || [];
    const coord = ta.coordination || {};

    // â”€â”€ Team overview cards â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    const split = el('div', 'teams-split');
    [{ id: 'team1', dotCls: 't' }, { id: 'team2', dotCls: 'ct' }].forEach(({ id, dotCls }) => {
      const td  = teams[id] || {};
      const agg = td.aggregate || {};
      const crd = coord[id]   || {};
      const card = el('div', 'team-card');
      card.innerHTML = `
        <div class="team-card-title">
          <span class="team-dot ${dotCls}"></span>
          ${td.name || id}
        </div>
        <div class="metrics-grid" style="margin-bottom:0">
          <div class="metric-card">
            <div class="metric-label">Team ADR</div>
            <div class="metric-value">${fmt(agg.team_adr, 1)}</div>
          </div>
          <div class="metric-card">
            <div class="metric-label">Team KAST</div>
            <div class="metric-value">${pctDirect(agg.team_kast)}</div>
          </div>
          <div class="metric-card">
            <div class="metric-label">Avg Rating</div>
            <div class="metric-value ${ratingClass(agg.avg_rating)}">${fmt(agg.avg_rating, 2)}</div>
          </div>
          <div class="metric-card">
            <div class="metric-label">Coordination</div>
            <div class="metric-value">${fmt(crd.coordination_score, 0)}</div>
            <div class="metric-sub">Trade rate: ${pctDirect(crd.traded_rate)}</div>
          </div>
        </div>`;
      split.appendChild(card);
    });
    body.appendChild(split);

    // â”€â”€ Separate scoreboards per team â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    [{ id: 'team1', dotCls: 't' }, { id: 'team2', dotCls: 'ct' }].forEach(({ id, dotCls }) => {
      const td   = teams[id] || {};
      const rows = sb.filter(p => p.team === id);
      if (!rows.length) return;

      const sec = el('div', 'section');
      sec.appendChild(el('div', 'section-title', `
        <span class="team-dot ${dotCls}" style="display:inline-block;margin-right:6px"></span>
        ${td.name || id} - Scoreboard
      `));
      sec.innerHTML += buildTeamTable(rows);
      body.appendChild(sec);
    });

    // â”€â”€ CT Setup patterns â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    const ctSetups = (ta.ct_setups || []).slice(0, 12);
    if (ctSetups.length) {
      const sec = el('div', 'section');
      sec.appendChild(el('div', 'section-title', 'CT Setup Patterns (first 12 rounds)'));
      const rows = ctSetups.map(s => `<tr>
        <td class="mono">Round ${s.round}</td>
        <td class="ct-side">${s.setup_type}</td>
        <td>
          ${s.a_players ? `<span style="color:var(--accent)">A:</span> ${s.a_players}` : ''}
          ${s.a_players && s.b_players ? ' | ' : ''}
          ${s.b_players ? `<span style="color:var(--ct-side)">B:</span> ${s.b_players}` : ''}
        </td>
      </tr>`).join('');
      sec.innerHTML += `<table class="data-table">
        <thead><tr><th>Round</th><th>Formation</th><th>Players</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
      body.appendChild(sec);
    }

    // â”€â”€ T Execute patterns â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    const tExec = (ta.t_executes || []).slice(0, 12);
    if (tExec.length) {
      const sec = el('div', 'section');
      sec.appendChild(el('div', 'section-title', 'T Execute Patterns'));
      const rows = tExec.map(e => `<tr>
        <td class="mono">Round ${e.round}</td>
        <td class="t-side" style="font-weight:700">${e.site || '-'}</td>
        <td class="mono">${e.site_kills} kills on site / ${e.total_team_kills} total</td>
        <td class="mono text-muted">${e.first_contact_s > 0 ? `${e.first_contact_s}s` : '-'}</td>
      </tr>`).join('');
      sec.innerHTML += `<table class="data-table">
        <thead><tr><th>Round</th><th>Site</th><th>Kills</th><th>Contact</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
      body.appendChild(sec);
    }

    // â”€â”€ Round types â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    // Only show economy/buy-type tags - remove confusing clutch/entry tags
    const SHOWN_TAGS = new Set(['pistol','eco_t','eco_ct','force_t','force_ct','full_buy','anti_eco','ace']);
    const TAG_LABELS = {
      pistol: 'Pistol', eco_t: 'T Eco', eco_ct: 'CT Eco',
      force_t: 'T Force', force_ct: 'CT Force',
      full_buy: 'Full Buy', anti_eco: 'Anti-Eco', ace: 'ACE',
    };

    const roundTags = (ta.round_tags || []).filter(rt => {
      const tags = rt.tags || [];
      return tags.some(t => SHOWN_TAGS.has(t));
    });

    if (roundTags.length) {
      const sec = el('div', 'section');
      sec.appendChild(el('div', 'section-title', 'Round Types (economy)'));
      const grid = el('div', '');
      grid.style.cssText = 'display:flex;flex-direction:column;gap:5px;';
      roundTags.forEach(rt => {
        const tags = (rt.tags || []).filter(t => SHOWN_TAGS.has(t));
        if (!tags.length) return;
        const row = el('div', '');
        row.style.cssText = 'display:flex;align-items:center;gap:8px;';
        row.innerHTML =
          `<span class="text-mono text-muted" style="min-width:54px;font-size:11px">Round ${rt.round}</span>` +
          tags.map(t => `<span class="round-tag ${_tagClass(t)}">${TAG_LABELS[t] || t}</span>`).join('');
        grid.appendChild(row);
      });
      sec.appendChild(grid);
      body.appendChild(sec);
    }

  } catch (err) {
    body.innerHTML = `<div class="empty-state"><div class="empty-state-title">Error</div><div class="empty-state-sub">${err.message}</div></div>`;
  }
}

function _tagClass(tag) {
  if (tag === 'pistol')     return 'tag-pistol';
  if (tag === 'eco_t' || tag === 'eco_ct')     return 'tag-eco';
  if (tag === 'force_t' || tag === 'force_ct') return 'tag-force';
  if (tag === 'full_buy')   return 'tag-full-buy';
  if (tag === 'anti_eco')   return 'tag-full-buy';
  if (tag === 'ace')        return 'tag-ace';
  return 'tag-default';
}

// â”€â”€ Replay view â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
async function initReplayView(targetRound = null, targetTick = null, options = {}) {
  if (!State.demoId) return;
  try {
    await ensureHighlightsLoaded();
  } catch (err) {
    console.warn('Highlights unavailable for replay context:', err);
  }
  const pills = $('#round-pills');
  if (pills.dataset.loaded) {
    if (targetRound != null) {
      await loadReplayRound(targetRound, targetTick, options);
    } else if (State.currentReplayData) {
      renderReplayHighlightContext(State.currentReplayData);
      renderReplayTimelineMarkers(State.currentReplayData);
    }
    return;
  }

  try {
    const { rounds } = await API.getReplayRounds(State.demoId);
    State.replayRounds = rounds;
    pills.dataset.loaded = '1';
    pills.innerHTML = '';
    rounds.forEach(r => {
      const pill = el('button', 'round-pill', String(r));
      pill.dataset.round = r;
      pill.addEventListener('click', () => loadReplayRound(r));
      pills.appendChild(pill);
    });
    if (targetRound != null) {
      await loadReplayRound(targetRound, targetTick, options);
    } else if (rounds.length) {
      await loadReplayRound(rounds[0]);
    }
  } catch (err) {
    console.error('Replay init failed:', err);
  }
}

async function loadReplayRound(roundNum, seekTick = null, options = {}) {
  $$('.round-pill').forEach(p => p.classList.toggle('active', parseInt(p.dataset.round) === roundNum));

  if (!getSelectedReplayHighlightForRound(roundNum) && State.selectedReplayHighlight) {
    clearReplayHighlightSelection();
  }

  if (replayEngine && State.currentReplayRound === roundNum && State.currentReplayData) {
    applyReplayHighlightSelection(State.currentReplayData, { seekMode: null, autoplay: false });
    if (seekTick != null) {
      replayEngine.pause();
      replayEngine.seekTo(findNearestReplayFrameIndex(State.currentReplayData.frames || [], seekTick));
      updateAlivePanelFromFrame(State.currentReplayData, replayEngine.currentFrame);
      updateKillFeedFromEngine();
      updateReplayHighlightLiveState(State.currentReplayData, replayEngine.currentFrame);
    }
    return;
  }

  const overlay = $('#replay-loading-overlay');
  overlay.classList.remove('hidden');

  try {
    const data = await API.getReplayRound(State.demoId, roundNum);
    State.currentReplayRound = roundNum;
    State.currentReplayData = data;

    if (replayEngine) replayEngine.stop();

    const canvas = $('#replay-canvas');
    replayEngine = new ReplayEngine(canvas, data, State.radarUrl);

    replayEngine.onFrameChange = (idx, total) => {
      $('#rp-scrubber').max   = total - 1;
      $('#rp-scrubber').value = idx;
      $('#rp-frame-counter').textContent = `${idx + 1} / ${total}`;
      updateAlivePanelFromFrame(data, idx);
      updateKillFeedFromEngine();
      updateReplayHighlightLiveState(data, idx);
    };
    replayEngine.onPlayStateChange = (playing) => {
      $('#rp-play').innerHTML = playing ? '&#9646;&#9646;' : '&#9654;';
    };

    // Show round tags for this round (only economy tags)
    const SHOWN_TAGS = new Set(['pistol','eco_t','eco_ct','force_t','force_ct','full_buy','anti_eco','ace']);
    const TAG_LABELS = {
      pistol: 'Pistol', eco_t: 'T Eco', eco_ct: 'CT Eco',
      force_t: 'T Force', force_ct: 'CT Force',
      full_buy: 'Full Buy', anti_eco: 'Anti-Eco', ace: 'ACE',
    };
    const rt = (State.teamAnalysis?.round_tags || []).find(r => r.round === roundNum);
    renderRoundTagsDisplay(rt, SHOWN_TAGS, TAG_LABELS);

    await replayEngine.init();
    overlay.classList.add('hidden');

    $('#rp-scrubber').max   = data.frame_count - 1;
    $('#rp-scrubber').value = 0;
    $('#rp-frame-counter').textContent = `1 / ${data.frame_count}`;
    updateAlivePanelFromFrame(data, 0);
    renderReplayHighlightContext(data);
    renderReplayTimelineMarkers(data);
    const autoHighlight = getSelectedReplayHighlightForRound(roundNum);
    const seekMode = options.seekMode || (autoHighlight ? 'start' : null);
    applyReplayHighlightSelection(data, {
      seekMode,
      autoplay: !!options.autoplayHighlight,
    });
    if (seekTick != null && !autoHighlight) {
      const targetFrame = findNearestReplayFrameIndex(data.frames || [], seekTick);
      replayEngine.pause();
      replayEngine.seekTo(targetFrame);
      updateAlivePanelFromFrame(data, targetFrame);
      updateKillFeedFromEngine();
      updateReplayHighlightLiveState(data, targetFrame);
    }

  } catch (err) {
    overlay.classList.add('hidden');
    console.error('Load round failed:', err);
  }
}

function updateAlivePanelFromFrame(data, frameIdx) {
  const panel = $('#alive-panel');
  if (!panel) return;
  const frame = data.frames[frameIdx];
  if (!frame) return;

  panel.innerHTML = '';
  const players = [...(frame.players || [])].sort((a, b) => {
    const aA = a.hp > 0 ? 0 : 1;
    const bA = b.hp > 0 ? 0 : 1;
    if (aA !== bA) return aA - bA;
    if (a.side !== b.side) return a.side === 'CT' ? -1 : 1;
    return 0;
  });

  players.forEach(p => {
    const dead  = p.hp <= 0;
    const isT   = p.side !== 'CT';
    const hp    = Math.max(0, p.hp);
    const hpCls = hp > 60 ? '' : hp > 30 ? ' low' : ' crit';
    const row = el('div', `alive-player${dead ? ' dead' : ''}`);
    row.innerHTML = `
      <span class="alive-dot ${isT ? 't' : 'ct'}"></span>
      <span class="alive-name">${p.name}</span>
      ${!dead ? `<span class="alive-hp${hpCls}">${Math.round(hp)}</span>` : ''}
    `;
    panel.appendChild(row);
  });
}

function updateKillFeedFromEngine() {
  if (!replayEngine) return;
  const feed = $('#kill-feed');
  if (!feed) return;

  const active = replayEngine._activeKills;
  if (!active.length) return;

  active.forEach(k => {
    const id = `kf-${k.tick}-${k.victim}`;
    if (feed.querySelector(`[data-id="${id}"]`)) return;

    const attSide = (k.attacker_side || '').toLowerCase();
    const vicSide = (k.victim_side   || '').toLowerCase();
    const entry = el('div', 'kf-entry fresh', `
      <span class="kf-att ${attSide}">${k.attacker}</span>
      <span class="kf-weapon">${k.weapon}</span>
      <span class="kf-vic ${vicSide}">${k.victim}</span>
      ${k.headshot ? '<span class="kf-hs">HS</span>' : ''}
    `);
    entry.dataset.id = id;
    feed.prepend(entry);
    setTimeout(() => entry.classList.remove('fresh'), 1200);
    while (feed.children.length > 12) feed.removeChild(feed.lastChild);
  });
}

function renderRoundTagsDisplay(rt, shownTags, labels) {
  const disp = $('#round-tags-display');
  if (!disp) return;
  disp.innerHTML = '';
  if (!rt) { disp.textContent = '-'; return; }
  const tags = (rt.tags || []).filter(t => shownTags.has(t));
  if (!tags.length) { disp.textContent = '-'; return; }
  tags.forEach(t => {
    disp.appendChild(el('span', `round-tag ${_tagClass(t)}`, labels[t] || t));
  });
}

// Replay control wiring
$('#rp-play').addEventListener('click',       () => replayEngine?.toggle());
$('#rp-prev-frame').addEventListener('click', () => replayEngine?.prevFrame());
$('#rp-next-frame').addEventListener('click', () => replayEngine?.nextFrame());
$('#rp-scrubber').addEventListener('input',  (e) => replayEngine?.seekTo(parseInt(e.target.value)));
$('#rp-speed').addEventListener('change',    (e) => replayEngine?.setSpeed(parseFloat(e.target.value)));
$('#rp-trails').addEventListener('change',   (e) => replayEngine?.setTrails(e.target.checked));
$('#rp-labels').addEventListener('change',   (e) => replayEngine?.setLabels(e.target.checked));

// â”€â”€ Coaching view â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
function renderCoachingView() {
  const individual = $('#coaching-individual');
  if (!individual) return;

  let contextNode = $('#coaching-context-slot');
  if (!contextNode) {
    contextNode = el('div', '');
    contextNode.id = 'coaching-context-slot';
    individual.prepend(contextNode);
  }
  contextNode.innerHTML = '';
  contextNode.appendChild(
    createCurrentMomentSection({
      subtitle: 'Generate coaching from the same moment you reviewed in replay or clips.',
      emptyHint: 'Select a clip or highlight first, or choose a player below for broad coaching.',
    }),
  );

  const playerSel = $('#coaching-player-select');
  const player = String(State.currentMoment?.player || '').trim();
  if (playerSel && player && [...playerSel.options].some((opt) => opt.value === player)) {
    playerSel.value = player;
  }

  const scoutingSel = $('#scouting-team-select');
  const side = String(State.currentMoment?.side || '').toUpperCase();
  if (scoutingSel && (side === 'CT' || side === 'T')) {
    scoutingSel.value = side === 'CT' ? 'team2' : 'team1';
  }
}

$$('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    $$('.tab-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    const tab = btn.dataset.tab;
    $$('.coaching-section').forEach(s => s.classList.toggle('active', s.id === `coaching-${tab}`));
  });
});

$('#coaching-individual').classList.add('active');

$('#btn-get-coaching').addEventListener('click', async () => {
  const player = $('#coaching-player-select').value;
  const status = $('#coaching-status');
  const report = $('#coaching-report');
  if (!player) return;
  setCurrentMomentPlayer(player, 'coaching-report');

  status.textContent = 'Analyzing player...';
  report.classList.add('hidden');
  try {
    if (!State.analyses[player]) {
      State.analyses[player] = await API.analyzePlayer(State.demoId, player);
    }
    status.textContent = 'Generating AI coaching report...';
    const res = await API.getCoaching(State.demoId, player);
    status.textContent = '';
    report.classList.remove('hidden');
    report.innerHTML = renderMarkdown(res.report || '');
  } catch (err) {
    status.textContent = `Error: ${err.message}`;
    showToast(err.message, { type: 'error', title: 'Coaching report failed' });
  }
});

$('#btn-get-scouting').addEventListener('click', async () => {
  const team   = $('#scouting-team-select').value;
  const status = $('#scouting-status');
  const report = $('#scouting-report');
  status.textContent = 'Generating scouting report...';
  report.classList.add('hidden');
  try {
    const res = await API.getScouting(State.demoId, team);
    status.textContent = '';
    report.classList.remove('hidden');
    report.innerHTML = renderMarkdown(res.report || '');
  } catch (err) {
    status.textContent = `Error: ${err.message}`;
    showToast(err.message, { type: 'error', title: 'Scouting report failed' });
  }
});

function renderMarkdown(text) {
  const esc = String(text || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');

  const lines = esc.split(/\r?\n/);
  const out = [];
  let inList = false;

  const inlineFormat = (s) => s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');

  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line) {
      if (inList) {
        out.push('</ul>');
        inList = false;
      }
      continue;
    }

    if (line.startsWith('### ')) {
      if (inList) {
        out.push('</ul>');
        inList = false;
      }
      out.push(`<h3>${inlineFormat(line.slice(4))}</h3>`);
      continue;
    }

    if (line.startsWith('## ')) {
      if (inList) {
        out.push('</ul>');
        inList = false;
      }
      out.push(`<h2>${inlineFormat(line.slice(3))}</h2>`);
      continue;
    }

    if (/^[-*•]\s+/.test(line)) {
      if (!inList) {
        out.push('<ul>');
        inList = true;
      }
      out.push(`<li>${inlineFormat(line.replace(/^[-*•]\s+/, ''))}</li>`);
      continue;
    }

    if (inList) {
      out.push('</ul>');
      inList = false;
    }
    out.push(`<p>${inlineFormat(line)}</p>`);
  }

  if (inList) out.push('</ul>');
  return out.join('\n');
}

// â”€â”€ Init â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
navigateTo('upload');
runLocalReadinessCheck();
