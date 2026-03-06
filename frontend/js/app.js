/**
 * app.js — CS2 AI Coach SPA
 * Handles navigation, upload flow, and all view rendering.
 * Depends on: api.js, replay.js
 */

// ── Global state ──────────────────────────────────────────────────────────────
const State = {
  demoId:       null,
  filename:     null,
  mapName:      null,
  totalRounds:  null,
  players:      [],
  teamAnalysis: null,
  analyses:     {},
  replayRounds: [],
  radarUrl:     null,
};

let replayEngine = null;

// ── DOM helpers ───────────────────────────────────────────────────────────────
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
  return isNaN(n) ? '—' : n.toFixed(digits);
}

// Value is already a percentage (0-100), just format it
function pctDirect(val) {
  const n = parseFloat(val);
  return isNaN(n) ? '—' : n.toFixed(1) + '%';
}

// ── HLTV-style rating color class ─────────────────────────────────────────────
function ratingClass(r) {
  const n = parseFloat(r);
  if (isNaN(n)) return '';
  if (n >= 1.15) return 'rating-great';
  if (n >= 1.00) return 'rating-good';
  if (n >= 0.85) return 'rating-ok';
  return 'rating-bad';
}

// ── Navigation ────────────────────────────────────────────────────────────────
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

// ── Upload flow ───────────────────────────────────────────────────────────────
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

async function startUpload(file) {
  $('#upload-progress').classList.remove('hidden');
  setStep('step-upload', 'active');
  setStatus('Uploading demo…');
  try {
    const up = await API.uploadDemo(file);
    State.demoId   = up.demo_id;
    State.filename = up.filename;
    setStep('step-upload', 'done');
    setStep('step-parse', 'active');
    setStatus('Parsing demo — this may take 20–60 s…');

    const parsed = await API.parseDemo(State.demoId);
    State.mapName     = parsed.map;
    State.totalRounds = parsed.total_rounds;
    State.players     = parsed.players || [];
    setStep('step-parse', 'done');
    setStep('step-ready', 'active');
    setStatus('Ready!');

    $('#meta-filename').textContent = State.filename;
    $('#meta-map').textContent      = State.mapName || '—';
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

// ── Scoreboard builder ────────────────────────────────────────────────────────
// scoreboard rows have: player, team, kills, deaths, kd_ratio, adr, kast (%), hs_rate (%), rating

function buildTeamTable(rows) {
  if (!rows.length) return '<div class="empty-state"><div class="empty-state-sub">No data</div></div>';
  const trs = rows.map(p => {
    const rc = ratingClass(p.rating);
    return `<tr data-player="${p.player}" style="cursor:pointer">
      <td class="highlight">${p.player}</td>
      <td class="mono">${p.kills ?? '—'}</td>
      <td class="mono">${p.deaths ?? '—'}</td>
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

// ── Overview view ─────────────────────────────────────────────────────────────
async function renderOverview() {
  const body = $('#overview-body');
  if (!State.demoId) return;
  body.innerHTML = '<div class="loading-placeholder">Loading…</div>';

  try {
    if (!State.teamAnalysis) {
      State.teamAnalysis = await API.getTeamAnalysis(State.demoId);
    }
    const ta = State.teamAnalysis;
    const sb = ta.scoreboard || [];

    body.innerHTML = '';

    // Overview stat cards
    const score = ta.score || {};
    const s1 = score.team1 ?? '—';
    const s2 = score.team2 ?? '—';
    const statsRow = el('div', 'overview-stats-row');
    [
      { label: 'Map',    value: State.mapName || '—',   sub: '' },
      { label: 'Rounds', value: State.totalRounds || '—', sub: 'played' },
      { label: 'Score',  value: `${s1} — ${s2}`,         sub: 'Team 1 vs Team 2' },
      { label: 'Players',value: sb.length || '—',         sub: 'tracked' },
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
            ADR ${fmt(agg.team_adr, 1)} · KAST ${pctDirect(agg.team_kast)} · Avg Rating ${fmt(agg.avg_rating, 2)}
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

// ── Player analysis view ──────────────────────────────────────────────────────
$('#btn-analyze').addEventListener('click', () => {
  const player = $('#player-select').value;
  if (player) analyzePlayer(player);
});

async function analyzePlayer(playerName) {
  const body   = $('#player-body');
  const status = $('#player-analyze-status');
  body.innerHTML = '';
  status.textContent = 'Analyzing…';
  try {
    if (!State.analyses[playerName]) {
      State.analyses[playerName] = await API.analyzePlayer(State.demoId, playerName);
    }
    status.textContent = '';
    renderPlayerBody(body, State.analyses[playerName]);
  } catch (err) {
    status.textContent = `Error: ${err.message}`;
  }
}

function renderPlayerBody(body, data) {
  body.innerHTML = '';

  // data.stats — core statistics from analyzer
  const stats = data.stats || {};
  const adv   = data.advanced || {};
  const pro   = adv.pro_metrics || {};

  // ── Core metrics grid ──────────────────────────────────────────────────────
  const coreGrid = el('div', 'metrics-grid');
  const kd_val = parseFloat(stats.kd_ratio);
  const adr_val = parseFloat(stats.adr);
  const coreItems = [
    { label: 'K/D',    value: fmt(stats.kd_ratio, 2),  cls: kd_val >= 1 ? 'good' : kd_val >= 0.75 ? '' : 'danger' },
    { label: 'ADR',    value: fmt(stats.adr, 1),        cls: adr_val >= 80 ? 'good' : adr_val >= 60 ? '' : 'danger' },
    { label: 'KAST',   value: pctDirect(adv.kast?.kast_percentage), cls: '' },
    { label: 'HS%',    value: pctDirect(stats.hs_rate), cls: '' },
    { label: 'Kills',  value: stats.kills  ?? '—',      cls: '' },
    { label: 'Deaths', value: stats.deaths ?? '—',      cls: '' },
    { label: 'Opening K', value: stats.opening_kills ?? '—', cls: '' },
    { label: 'Accuracy',  value: pctDirect(stats.accuracy), cls: '' },
  ];
  coreItems.forEach(m => {
    coreGrid.appendChild(el('div', 'metric-card', `
      <div class="metric-label">${m.label}</div>
      <div class="metric-value ${m.cls}">${m.value}</div>
    `));
  });
  body.appendChild(coreGrid);

  // ── Pro metrics ────────────────────────────────────────────────────────────
  const duels   = pro.duels || {};
  const utilEff = pro.utility_effectiveness || {};

  const proGrid = el('div', 'pro-metrics-grid');
  const proItems = [
    { label: 'HLTV 2.0',    value: fmt(pro.hltv_rating, 2),           cls: ratingClass(pro.hltv_rating) },
    { label: 'Impact',       value: fmt(pro.impact_rating, 2),         cls: '' },
    { label: 'Entry Rate',   value: pctDirect(pro.entry_success_rate), cls: '' },
    { label: 'Duel Win%',    value: pctDirect(duels.duel_win_rate),    cls: '' },
    { label: 'Utility Score',value: fmt(utilEff.utility_score, 1),     cls: '' },
  ];
  proItems.forEach(m => {
    proGrid.appendChild(el('div', 'pro-metric-card', `
      <div class="metric-label">${m.label}</div>
      <div class="metric-value ${m.cls}">${m.value}</div>
    `));
  });
  const proSec = el('div', 'section');
  proSec.appendChild(el('div', 'section-title', 'Pro Metrics'));
  proSec.appendChild(proGrid);
  body.appendChild(proSec);

  // ── T-side / CT-side split ─────────────────────────────────────────────────
  const sideStats = adv.side_stats || {};
  const tSide  = sideStats.t_side  || {};
  const ctSide = sideStats.ct_side || {};
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
          <div class="metric-card"><div class="metric-label">Kills</div><div class="metric-value">${tSide.kills ?? '—'}</div></div>
          <div class="metric-card"><div class="metric-label">HS%</div><div class="metric-value">${pctDirect(tSide.hs_rate)}</div></div>
        </div>
      </div>
      <div class="team-card">
        <div class="team-card-title"><span class="team-dot ct"></span>CT-Side (${ctSide.rounds} rounds)</div>
        <div class="metrics-grid" style="margin-bottom:0">
          <div class="metric-card"><div class="metric-label">K/D</div><div class="metric-value">${fmt(ctSide.kd_ratio, 2)}</div></div>
          <div class="metric-card"><div class="metric-label">Kills</div><div class="metric-value">${ctSide.kills ?? '—'}</div></div>
          <div class="metric-card"><div class="metric-label">HS%</div><div class="metric-value">${pctDirect(ctSide.hs_rate)}</div></div>
        </div>
      </div>`;
    sideSec.appendChild(sideGrid);
    body.appendChild(sideSec);
  }

  // ── Key findings ───────────────────────────────────────────────────────────
  const findings = data.findings || [];
  if (findings.length) {
    const sec = el('div', 'section');
    sec.appendChild(el('div', 'section-title', 'Key Findings'));
    const list = el('div', 'findings-list');
    findings.forEach(f => {
      const msg = typeof f === 'string' ? f : (f.message || '');
      const sev = typeof f === 'object' ? f.severity : '';
      const icon = sev === 'low' ? '✓' : sev === 'high' ? '✗' : 'i';
      const iconCls = sev === 'low' ? 'text-success' : sev === 'high' ? 'text-danger' : 'text-muted';
      list.appendChild(el('div', 'finding-item', `
        <span class="finding-icon ${iconCls}">${icon}</span>
        <span class="finding-text">${msg}</span>
      `));
    });
    sec.appendChild(list);
    body.appendChild(sec);
  }

  // ── Multi-kills & clutches row ─────────────────────────────────────────────
  const mk      = adv.multi_kills || {};
  const clutches = adv.clutches   || [];
  const clutchWon = clutches.filter(c => c.won).length;
  const tradeStats = adv.trade_stats || {};

  const extraItems = [];
  if (mk.total_aces > 0)    extraItems.push(['ACEs', mk.total_aces]);
  if (mk.total_4k  > 0)    extraItems.push(['4K rounds', mk.total_4k]);
  if (mk.total_3k  > 0)    extraItems.push(['3K rounds', mk.total_3k]);
  if (clutches.length > 0) extraItems.push(['Clutches', `${clutchWon}/${clutches.length}`]);
  if (tradeStats.player_deaths > 0) extraItems.push(['Trade rate', pctDirect(tradeStats.traded_rate)]);
  if (stats.accuracy > 0)  extraItems.push(['Shot accuracy', pctDirect(stats.accuracy)]);
  if (duels.total_duels > 0) extraItems.push(['Duels', `${duels.duels_won}W / ${duels.duels_lost}L`]);

  if (extraItems.length) {
    const sec = el('div', 'section');
    sec.appendChild(el('div', 'section-title', 'Advanced Stats'));
    const rows = extraItems.map(([k, v]) => `<tr><td>${k}</td><td class="mono highlight">${v}</td></tr>`).join('');
    sec.innerHTML += `<table class="data-table" style="max-width:360px"><tbody>${rows}</tbody></table>`;
    body.appendChild(sec);
  }
}

// ── Team view ─────────────────────────────────────────────────────────────────
async function renderTeam() {
  const body = $('#team-body');
  if (!State.demoId) return;
  body.innerHTML = '<div class="loading-placeholder">Loading team analysis…</div>';

  try {
    if (!State.teamAnalysis) {
      State.teamAnalysis = await API.getTeamAnalysis(State.demoId);
    }
    const ta = State.teamAnalysis;
    body.innerHTML = '';

    const teams = ta.teams || {};
    const sb    = ta.scoreboard || [];
    const coord = ta.coordination || {};

    // ── Team overview cards ────────────────────────────────────────────────
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

    // ── Separate scoreboards per team ──────────────────────────────────────
    [{ id: 'team1', dotCls: 't' }, { id: 'team2', dotCls: 'ct' }].forEach(({ id, dotCls }) => {
      const td   = teams[id] || {};
      const rows = sb.filter(p => p.team === id);
      if (!rows.length) return;

      const sec = el('div', 'section');
      sec.appendChild(el('div', 'section-title', `
        <span class="team-dot ${dotCls}" style="display:inline-block;margin-right:6px"></span>
        ${td.name || id} — Scoreboard
      `));
      sec.innerHTML += buildTeamTable(rows);
      body.appendChild(sec);
    });

    // ── CT Setup patterns ──────────────────────────────────────────────────
    const ctSetups = (ta.ct_setups || []).slice(0, 12);
    if (ctSetups.length) {
      const sec = el('div', 'section');
      sec.appendChild(el('div', 'section-title', 'CT Setup Patterns (first 12 rounds)'));
      const rows = ctSetups.map(s => `<tr>
        <td class="mono">Round ${s.round}</td>
        <td class="ct-side">${s.setup_type}</td>
        <td>
          ${s.a_players ? `<span style="color:var(--accent)">A:</span> ${s.a_players}` : ''}
          ${s.a_players && s.b_players ? ' · ' : ''}
          ${s.b_players ? `<span style="color:var(--ct-side)">B:</span> ${s.b_players}` : ''}
        </td>
      </tr>`).join('');
      sec.innerHTML += `<table class="data-table">
        <thead><tr><th>Round</th><th>Formation</th><th>Players</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
      body.appendChild(sec);
    }

    // ── T Execute patterns ─────────────────────────────────────────────────
    const tExec = (ta.t_executes || []).slice(0, 12);
    if (tExec.length) {
      const sec = el('div', 'section');
      sec.appendChild(el('div', 'section-title', 'T Execute Patterns'));
      const rows = tExec.map(e => `<tr>
        <td class="mono">Round ${e.round}</td>
        <td class="t-side" style="font-weight:700">${e.site || '—'}</td>
        <td class="mono">${e.site_kills} kills on site / ${e.total_team_kills} total</td>
        <td class="mono text-muted">${e.first_contact_s > 0 ? `${e.first_contact_s}s` : '—'}</td>
      </tr>`).join('');
      sec.innerHTML += `<table class="data-table">
        <thead><tr><th>Round</th><th>Site</th><th>Kills</th><th>Contact</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
      body.appendChild(sec);
    }

    // ── Round types ────────────────────────────────────────────────────────
    // Only show economy/buy-type tags — remove confusing clutch/entry tags
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

// ── Replay view ───────────────────────────────────────────────────────────────
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
  if (!rt) { disp.textContent = '—'; return; }
  const tags = (rt.tags || []).filter(t => shownTags.has(t));
  if (!tags.length) { disp.textContent = '—'; return; }
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

// ── Coaching view ─────────────────────────────────────────────────────────────
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

  status.textContent = 'Analyzing player…';
  report.classList.add('hidden');
  try {
    if (!State.analyses[player]) {
      State.analyses[player] = await API.analyzePlayer(State.demoId, player);
    }
    status.textContent = 'Generating AI coaching report…';
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
  status.textContent = 'Generating scouting report…';
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
  return text
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/^### (.+)$/gm, '<h3>$1</h3>')
    .replace(/^## (.+)$/gm, '<h2>$1</h2>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/^[•\-\*] (.+)$/gm, '<li>$1</li>')
    .replace(/(<li>[\s\S]*?<\/li>)/g, '<ul>$1</ul>')
    .replace(/\n{2,}/g, '\n');
}

// ── Init ──────────────────────────────────────────────────────────────────────
navigateTo('upload');
