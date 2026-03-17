/**
 * api.js — Thin fetch wrapper for CS2 Coach backend
 * All calls return parsed JSON or throw an Error with a human-readable message.
 */

const API = (() => {
  async function _fetch(method, url, body = null) {
    const opts = {
      method,
      headers: {},
    };
    if (body instanceof FormData) {
      opts.body = body;
    } else if (body !== null) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    const res = await fetch(url, opts);
    if (!res.ok) {
      let msg = `HTTP ${res.status}`;
      let detail = null;
      try {
        const j = await res.json();
        detail = j?.detail ?? null;
        if (typeof detail === 'string') {
          msg = detail;
        } else if (detail && typeof detail === 'object') {
          msg = detail.message || detail.error || msg;
          if (Array.isArray(detail.hints) && detail.hints.length) {
            msg += ` Hint: ${detail.hints[0]}`;
          }
        }
      } catch {}
      const err = new Error(msg);
      err.status = res.status;
      err.detail = detail;
      throw err;
    }
    return res.json();
  }

  const GET  = (url)        => _fetch('GET',  url);
  const POST = (url, body)  => _fetch('POST', url, body || null);

  return {
    /** Upload a .dem file; returns {demo_id, filename, size_mb} */
    uploadDemo(file) {
      const fd = new FormData();
      fd.append('file', file);
      return _fetch('POST', '/api/demo/upload', fd);
    },

    /** Parse the uploaded demo; returns {map, total_rounds, players} */
    parseDemo(demoId) {
      return POST(`/api/demo/${demoId}/parse`);
    },

    /** Get cached demo info */
    getDemoInfo(demoId) {
      return GET(`/api/demo/${demoId}/info`);
    },

    /** Backend/runtime health summary */
    getHealth() {
      return GET('/api/health');
    },

    /** End-to-end local operator diagnostics */
    getLocalDoctor(demoId = '') {
      const q = demoId ? `?demo_id=${encodeURIComponent(demoId)}` : '';
      return GET(`/api/local/doctor${q}`);
    },

    /** Fetch radar PNG as blob URL for a map name */
    async getRadarUrl(mapName) {
      const res = await fetch(`/api/radar/${mapName}`);
      if (!res.ok) return null;
      const blob = await res.blob();
      return URL.createObjectURL(blob);
    },

    /** Run player statistical analysis; returns full analysis dict */
    analyzePlayer(demoId, playerName) {
      return POST(`/api/demo/${demoId}/analyze/${encodeURIComponent(playerName)}`);
    },

    /** Fetch Steam profile metadata for selected player (avatar/profile url).
     *  opts.steamid64  — when provided, the backend skips name-based lookup entirely,
     *                    preventing wrong-profile issues from duplicate/changed names.
     */
    getPlayerSteamProfile(demoId, playerName, opts = {}) {
      const refresh = opts.refresh ? 1 : 0;
      const debug = opts.debug ? 1 : 0;
      const sid = opts.steamid64 ? `&steamid64=${encodeURIComponent(opts.steamid64)}` : '';
      return GET(`/api/demo/${demoId}/player/${encodeURIComponent(playerName)}/steam?refresh=${refresh}&debug=${debug}${sid}`);
    },

    /** Get generated player visuals (heatmaps, utility map, route gif) */
    getPlayerVisuals(demoId, playerName) {
      return GET(`/api/demo/${demoId}/player/${encodeURIComponent(playerName)}/visuals`);
    },

    /** Get team analysis (all 10 players, setups, executes, round tags) */
    getTeamAnalysis(demoId) {
      return GET(`/api/demo/${demoId}/team`);
    },

    /** Get available round numbers for replay */
    getReplayRounds(demoId) {
      return GET(`/api/demo/${demoId}/replay/rounds`);
    },

    /** Get replay frame data for a specific round */
    getReplayRound(demoId, roundNum) {
      return GET(`/api/demo/${demoId}/replay/${roundNum}`);
    },

    /** Get detected match highlights and summary */
    getHighlights(demoId) {
      return GET(`/api/demo/${demoId}/highlights`);
    },

    /** Get planned future clip windows derived from detected highlights */
    getClipPlans(demoId) {
      return GET(`/api/demo/${demoId}/clip-plans`);
    },

    /** Get rendered clip artifacts for a demo session */
    getRenderedClips(demoId) {
      return GET(`/api/demo/${demoId}/clips`);
    },

    /** Get all indexed clips across saved demo sessions */
    getAllClips() {
      return GET('/api/clips');
    },

    /** Get a single indexed clip record by clip_id */
    getClipRecord(clipId, demoId = '') {
      const q = demoId ? `?demo_id=${encodeURIComponent(demoId)}` : '';
      return GET(`/api/clips/${encodeURIComponent(clipId)}${q}`);
    },

    /** Queue a render job for an existing clip plan */
    renderClipPlan(demoId, clipPlanId, payload = {}) {
      return POST(`/api/demo/${demoId}/clips/render/${encodeURIComponent(clipPlanId)}`, payload);
    },

    /** Generate AI coaching report for a player */
    getCoaching(demoId, playerName) {
      return POST(`/api/demo/${demoId}/coaching/${encodeURIComponent(playerName)}`);
    },

    /** Generate AI scouting report for a team ('team1' | 'team2') */
    getScouting(demoId, targetTeam) {
      return POST(`/api/demo/${demoId}/scouting/${targetTeam}`);
    },

    /** List known render modes and their availability */
    getRenderModes() {
      return GET('/api/render-modes');
    },

    /** List available render quality presets */
    getRenderPresets() {
      return GET('/api/render-presets');
    },

    /** Comprehensive health check for local in-game capture readiness */
    getIngameHealth(demoId) {
      const q = demoId ? `?demo_id=${encodeURIComponent(demoId)}` : '';
      return GET(`/api/ingame/health${q}`);
    },

    /**
     * Queue a clip render request and surface structured API errors.
     */
    async renderClipPlanDetailed(demoId, clipPlanId, payload = {}) {
      const url = `/api/demo/${demoId}/clips/render/${encodeURIComponent(clipPlanId)}`;
      const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const body = await res.json();
      if (!res.ok) {
        const err = new Error(typeof body.detail === 'string' ? body.detail : (body.detail?.error || `HTTP ${res.status}`));
        err.detail = body.detail;
        throw err;
      }
      return body;
    },

    // ── Render queue ──────────────────────────────────────────────────────

    /** Get full queue status */
    getQueueStatus() {
      return GET('/api/queue');
    },

    /** Get one queue job with persisted clip/failure state */
    getQueueJob(jobId) {
      return GET(`/api/queue/job/${encodeURIComponent(jobId)}`);
    },

    /** Get persisted event stream for a queue job */
    getQueueJobEvents(jobId, limit = 200) {
      return GET(`/api/queue/job/${encodeURIComponent(jobId)}/events?limit=${encodeURIComponent(limit)}`);
    },

    /** Enqueue a single render job */
    queueEnqueue(demoId, clipPlanId, renderMode, renderPreset, targetSettings) {
      return POST('/api/queue/enqueue', {
        demo_id: demoId,
        clip_plan_id: clipPlanId,
        render_mode: renderMode || 'cs2_ingame_capture',
        render_preset: renderPreset || '',
        target_settings: targetSettings || {},
      });
    },

    /** Enqueue a batch of render jobs */
    queueEnqueueBatch(demoId, opts = {}) {
      return POST('/api/queue/enqueue-batch', {
        demo_id: demoId,
        render_mode: opts.renderMode || 'cs2_ingame_capture',
        render_preset: opts.renderPreset || '',
        target_settings: opts.targetSettings || {},
        mode: opts.mode || 'selected',
        clip_plan_ids: opts.clipPlanIds || [],
        count: opts.count || 5,
      });
    },

    /** Cancel a queue job */
    queueCancel(jobId) {
      return POST(`/api/queue/cancel/${encodeURIComponent(jobId)}`);
    },

    /** Cancel all queued jobs */
    queueCancelAll() {
      return POST('/api/queue/cancel-all');
    },

    /** Retry a failed/cancelled job */
    queueRetry(jobId) {
      return POST(`/api/queue/retry/${encodeURIComponent(jobId)}`);
    },

    /** Retry all failed jobs */
    queueRetryAllFailed() {
      return POST('/api/queue/retry-all-failed');
    },

    /** Clear completed+cancelled jobs from queue */
    queueClearCompleted() {
      return POST('/api/queue/clear-completed');
    },

    /** Clear failed jobs from queue */
    queueClearFailed() {
      return POST('/api/queue/clear-failed');
    },
  };
})();
