# CS2 AI Coach

Counter-Strike 2 demo analysis tool powered by `awpy` and Anthropic Claude.

## Project Overview (EN)

CS2 AI Coach analyzes a **single .dem file** and provides a full professional-grade web dashboard:

- Individual player analytics with pro metrics
- Team-level analytics (all 10 players, split scoreboard)
- Opponent scouting report (AI)
- Interactive 2D round replay with Canvas renderer

## Proje Ozeti (TR)

CS2 AI Coach, **tek bir .dem dosyasi** uzerinden profesyonel seviyede web arayuzu sunar:

- Oyuncu bazli analiz ve pro metrikler
- Takim bazli analiz (10 oyuncu, ayri scoreboard)
- Rakip scouting raporu (AI)
- Interaktif 2D round replay (Canvas)

## Current Features (v1.1)

### Demo Parsing
- Parse `.dem` with `awpy 2.x` (Polars DataFrames, named-column aliasing)
- Extract kills, damages (hp_damage multi-alias), grenades, shots, player positions, rounds, bomb events

### Individual Analysis
- K/D, ADR, HS%, accuracy, opening duel stats
- KAST, side split (T/CT), clutches, trades, multi-kills, economy, flash, death clusters
- Pro metrics: HLTV Rating 2.0 (approx), Impact, Entry Success %, Duel Win Rate, Utility Effectiveness

### Team Analysis
- 10-player scoreboard split by team (Team 1 vs Team 2)
- HLTV-style color-coded rating column (green/yellow/red)
- Match score (rounds won per team)
- Team aggregate stats: ADR, KAST, Avg Rating, Coordination Score
- CT setup detection (A/B anchor distribution per round)
- T execute pattern detection (site preference, kill density)
- Round auto-tags: pistol, eco, force, full_buy, anti_eco, ace

### 2D Replay
- Canvas-based minimap replay at 600×600px
- Correct awpy coordinate mapping (world → 1024px radar → 600px canvas)
- T players (orange) / CT players (blue) with HP bars and yaw arrows
- Kill markers (X), headshot ring, bomb plant flash, grenade icons
- Smoke / flash / molotov / HE / decoy event visualization
- Trail system, player labels, frame scrubber, speed control (0.5×–4×)
- Kill feed and alive panel in sidebar

### AI Reports
- Individual coaching report (`get_coaching`) — Turkish output, Claude Sonnet 4.6
- Opponent scouting report (`get_scouting_report`) — tendencies, weak spots, counter-strategies

## Architecture

This project uses a **FastAPI** backend + **vanilla JS SPA** frontend (no frameworks):

```
cs2-coach/
├── api/
│   ├── __init__.py
│   └── main.py          # FastAPI endpoints, in-memory session store
├── frontend/
│   ├── index.html       # Single-page app shell
│   ├── css/style.css
│   └── js/
│       ├── api.js       # Thin fetch wrapper
│       ├── replay.js    # ReplayEngine (Canvas 2D)
│       └── app.js       # SPA navigation, view rendering
├── src/
│   ├── analyzer.py      # Player analysis, pro metrics
│   ├── coach.py         # Claude AI coaching & scouting
│   ├── parser.py        # awpy 2.x demo parsing
│   ├── team_analyzer.py # Team scoreboard, setups, executes, tags
│   ├── replay.py        # (legacy Plotly replay helper)
│   └── utils.py         # Map info, coordinate helpers
├── app.py               # (legacy Streamlit app)
├── requirements.txt
└── README.md
```

## Setup

```bash
python -m venv venv
```

Windows:
```bash
venv\Scripts\activate
```

macOS/Linux:
```bash
source venv/bin/activate
```

Install dependencies:
```bash
pip install -r requirements.txt
```

Download map radar images:
```bash
awpy get maps
```

Create `.env` (copy from `.env.example`):
```env
# Required — AI coaching and scouting reports
ANTHROPIC_API_KEY=sk-ant-...

# Optional — Steam avatar images and profile links in Player view
# Get your key at: https://steamcommunity.com/dev/apikey
STEAM_API_KEY=
```

Run the web server:
```bash
uvicorn api.main:app --reload --port 8000
```

Then open [http://localhost:8000](http://localhost:8000).

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/demo/upload` | Upload `.dem` file |
| POST | `/api/demo/{id}/parse` | Parse demo, extract all data |
| GET  | `/api/demo/{id}/info` | Demo metadata + player list |
| GET  | `/api/radar/{map_name}` | Radar image (PNG/WebP) |
| POST | `/api/demo/{id}/analyze/{player}` | Individual player analysis |
| GET  | `/api/demo/{id}/team` | Full team analysis |
| GET  | `/api/demo/{id}/replay/rounds` | Available round numbers |
| GET  | `/api/demo/{id}/replay/{round}` | Replay frames + kills + bombs + grenades |
| POST | `/api/demo/{id}/coaching/{player}` | AI coaching report |
| POST | `/api/demo/{id}/scouting/{team}` | AI scouting report |
| GET  | `/api/demo/{id}/player/{player}/steam` | Steam avatar + profile URL (requires `STEAM_API_KEY`) |
| GET  | `/api/demo/{id}/player/{player}/visuals` | Generated heatmaps, utility map, route GIF |

## Changelog

### v1.2
- **New**: Steam profile integration — player avatar image and "Player Profile" link in the player header
  - Backend: `GET /api/demo/{id}/player/{player}/steam` resolves SteamID64 from parsed demo data, calls `ISteamUser/GetPlayerSummaries`, returns `avatar_url` + `profile_url`
  - Parser: `_build_player_identities()` extracts SteamID64 from tick data and stores as `player_identities` in parsed output
  - Frontend: real avatar replaces letter placeholder when available; disabled button shown on fallback
  - Caching per session; failure results cached for 30 s to avoid redundant calls
  - Debug mode: add `?steamDebug=1` to URL to log Steam API responses to browser console
  - Requires `STEAM_API_KEY` in `.env` (see Setup)
- **New**: `.env.example` for quick project setup

### v1.1
- **Improve**: Player summary metric cards are now grouped by performance level:
  - Strong Performance (green)
  - Average Performance (yellow)
  - Weak Performance (red)
- **Improve**: Metric status labels now use benchmark-aware evaluation (value vs Avg marker), not raw text parsing.
- **Improve**: Direction-aware status logic:
  - Higher is better for ADR, K/D, KPR, HS%, Accuracy, A/R, Opening success, Total Damage
  - Lower is better for DPR
- **Fix**: Incorrect status labels on cards such as `A / Round` and `Opening W/L` when values were above Avg.
- **Improve**: Detailed statistics area rendered as cleaner grouped block cards for better readability.
- **Improve**: Side-based player metric consistency strengthened via scoped `pro_metrics.sides` data flow in analyzer/frontend integration.

### v1.0
- **New**: FastAPI backend + vanilla JS SPA replaces Streamlit
- **New**: `api/main.py` — REST API with in-memory session store, side normalization, zero-coord filtering, grenade events in replay response
- **New**: `frontend/` — dark-themed SPA with upload flow, overview, player, team, replay and coaching views
- **New**: Canvas 2D replay engine (`replay.js`) — correct world→radar→canvas coordinate mapping, grenade/bomb/kill events
- **New**: `src/team_analyzer.py` — scoreboard, CT setups, T executes, coordination score, round tags, match score
- **New**: HLTV Rating 2.0 approx, Impact, Duel Win Rate, Entry Success, Utility Score (`src/analyzer.py`)
- **New**: `get_scouting_report()` in `src/coach.py`
- **Fix**: `_process_damages` column aliasing — handles `hp_damage`, `hp_dmg`, `damage_health`, `damage_health_real`, etc. (ADR = 0.0 bug)
- **Fix**: Rating color CSS specificity — `.data-table td.rating-*` overrides table cell default color
- **Fix**: Match score computed from `winner_side` + team side mapping

### v0.3
- Fixed `victim_X` / `victim_Y` normalization bug
- Fixed grenade `X/Y/Z` mapping to `nade_x/y/z`
- Added separate death map and utility map
- Schema version bumped to 5

### v0.2.1
- Added round route GIF animation

### v0.2
- Added advanced analytics (KAST, clutch, trade, economy, flash, multi-kill, clusters)
- Added T/CT heatmap split

### v0.1
- Initial release: parsing, basic stats, rule-based findings, AI coaching
