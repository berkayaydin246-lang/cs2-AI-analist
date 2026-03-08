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
      try { const j = await res.json(); msg = j.detail || msg; } catch {}
      throw new Error(msg);
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

    /** Fetch Steam profile metadata for selected player (avatar/profile url) */
    getPlayerSteamProfile(demoId, playerName, opts = {}) {
      const refresh = opts.refresh ? 1 : 0;
      const debug = opts.debug ? 1 : 0;
      return GET(`/api/demo/${demoId}/player/${encodeURIComponent(playerName)}/steam?refresh=${refresh}&debug=${debug}`);
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

    /** Generate AI coaching report for a player */
    getCoaching(demoId, playerName) {
      return POST(`/api/demo/${demoId}/coaching/${encodeURIComponent(playerName)}`);
    },

    /** Generate AI scouting report for a team ('team1' | 'team2') */
    getScouting(demoId, targetTeam) {
      return POST(`/api/demo/${demoId}/scouting/${targetTeam}`);
    },
  };
})();
