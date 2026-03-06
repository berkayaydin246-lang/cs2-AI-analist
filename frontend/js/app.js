/**
 * app.js - CS2 AI Coach SPA
 * Handles navigation, upload flow, and all view rendering.
 * Depends on: api.js, replay.js
 */

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
  playerSideFilter: 'both',
  currentPlayer: null,
  replayRounds: [],
  radarUrl:     null,
};

let replayEngine = null;

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
  a.addEventListener('click', (e) => {
    e.preventDefault();
    const view = a.dataset.view;
    if (!State.demoId && view !== 'upload') return;
    navigateTo(view);
    if (view === 'overview') renderOverview();
    if (view === 'team')     renderTeam();
    if (view === 'replay')   initReplayView();
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

function resetDemoScopedState() {
  if (replayEngine) {
    replayEngine.stop();
    replayEngine = null;
  }

  State.teamAnalysis = null;
  State.analyses = {};
  State.playerVisuals = {};
  State.playerSideFilter = 'both';
  State.currentPlayer = null;
  State.replayRounds = [];
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
    State.mapName     = parsed.map;
    State.totalRounds = parsed.total_rounds;
    State.players     = parsed.players || [];
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
document.addEventListener('click', (e) => {
  const tr = e.target.closest('tr[data-player]');
  if (!tr) return;
  const player = tr.dataset.player;
  if (!player) return;
  navigateTo('player');
  $('#player-select').value = player;
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
    const ta = State.teamAnalysis;
    const sb = ta.scoreboard || [];

    body.innerHTML = '';

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
  if (State.currentPlayer !== playerName) {
    State.playerSideFilter = 'both';
    State.currentPlayer = playerName;
  }
  try {
    if (!State.analyses[playerName]) {
      State.analyses[playerName] = await API.analyzePlayer(State.demoId, playerName);
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
    status.textContent = '';
    safeRenderPlayerBody(body, State.analyses[playerName], State.playerVisuals[playerName]);
  } catch (err) {
    status.textContent = `Error: ${err.message}`;
  }
}

function renderPlayerBody(body, data, visuals = null) {
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

  topSection.innerHTML = `
    <div class="player-hero-head">
      <div>
        <h2 class="player-hero-title">${data.player || 'Player'}</h2>
        <div class="player-hero-sub">${data.map || '-'} | ${selectedRounds || '-'} rounds | ${sideLabel}</div>
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
      safeRenderPlayerBody(body, data, visuals);
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

function safeRenderPlayerBody(body, data, visuals = null) {
  try {
    renderPlayerBody(body, data, visuals);
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
        renderPlayerBody(body, data, visuals);
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
async function initReplayView() {
  if (!State.demoId) return;
  const pills = $('#round-pills');
  if (pills.dataset.loaded) return;

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
    if (rounds.length) loadReplayRound(rounds[0]);
  } catch (err) {
    console.error('Replay init failed:', err);
  }
}

async function loadReplayRound(roundNum) {
  $$('.round-pill').forEach(p => p.classList.toggle('active', parseInt(p.dataset.round) === roundNum));

  const overlay = $('#replay-loading-overlay');
  overlay.classList.remove('hidden');

  try {
    const data = await API.getReplayRound(State.demoId, roundNum);

    if (replayEngine) replayEngine.stop();

    const canvas = $('#replay-canvas');
    replayEngine = new ReplayEngine(canvas, data, State.radarUrl);

    replayEngine.onFrameChange = (idx, total) => {
      $('#rp-scrubber').max   = total - 1;
      $('#rp-scrubber').value = idx;
      $('#rp-frame-counter').textContent = `${idx + 1} / ${total}`;
      updateAlivePanelFromFrame(data, idx);
      updateKillFeedFromEngine();
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
  // Player select already populated on upload
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
