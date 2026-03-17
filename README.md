# CS2 AI Coach

Local CS2 demo analysis product with replay, highlights, clip planning, clip rendering, render queue, and AI coaching/scouting.

This repository now targets a production-ready local workflow, not cloud deployment.

## Current Product Scope

Implemented and working in current scope:
- Demo upload and parsing
- Structured match analytics (player + team)
- Highlight detection and clip planning
- Interactive 2D replay
- Clip storage and gallery
- In-game renderer: `cs2_ingame_capture` (CS2 + OBS)
- Sequential render queue with batch actions
- Cross-workflow context between replay, clips, analysis, and coaching

Deprecated and disabled by default:
- Legacy tactical renderer: `tactical_2d_mp4`

Not in current scope:
- Cloud deployment
- Authentication/subscriptions
- Public clip sharing infrastructure
- Automatic match downloading

## Stability Notes

- The active render foundation now targets worker-driven in-game capture, not the old tactical renderer.
- In-game capture is environment-sensitive (CS2 process control + OBS connectivity) and requires machine-specific setup.
- The legacy tactical renderer remains in the repository only as an explicit migration/debug opt-in via `ENABLE_LEGACY_TACTICAL_RENDERER=1`.

## Platform + Tooling Requirements

## Required
- Windows for in-game capture mode
- Python 3.10+
- CS2 demo file (`.dem`)
- Dependencies from `requirements.txt`

## Optional by feature
- `ANTHROPIC_API_KEY`: required only for coaching/scouting endpoints
- `STEAM_API_KEY`: optional, enables avatars/profile links
- CS2 + OBS setup: required only for `cs2_ingame_capture`

## Setup

1. Create and activate virtual environment:

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

2. Install dependencies:

```powershell
pip install -r requirements.txt
```

3. Download map assets used by awpy:

```powershell
awpy get maps
```

4. Configure environment:
- Copy `.env.example` -> `.env`
- Fill values relevant to your workflow

5. Start backend (serves frontend as well):

```powershell
uvicorn api.main:app --reload --port 8000
```

6. Open:
- `http://localhost:8000`

No separate frontend dev server is required for normal local operation.

7. Start the render worker in a second terminal:

```powershell
.\venv\Scripts\Activate.ps1
python -m src.render_worker --output-root outputs/generated --queue-dir outputs/generated/queue
```

8. Run repository-only tests for the queue, post-process, and render-service wiring:

```powershell
python -m unittest discover -s tests -v
```

Note on legacy script:
- app.py is a legacy Streamlit interface kept for compatibility experiments.
- The primary supported product path is FastAPI plus frontend at http://localhost:8000.

## Environment Variables

See `.env.example` for full details.

Core:
- `ANTHROPIC_API_KEY` (optional unless using AI reports)
- `STEAM_API_KEY` (optional)

In-game capture:
- `CS2_EXE` (recommended explicit path)
- `CS2_STEAM_PATH` (optional hint)
- `CS2_CONTROL_BACKEND` (`plain` or `hlae`, default: `plain`)
- `CS2_NETCON_PORT` (must match CS2 launch options)
- `CS2_FULLSCREEN`, `CS2_WIDTH`, `CS2_HEIGHT` (optional)
- `CS2_HLAE_EXE` (required only when `CS2_CONTROL_BACKEND=hlae`)
- `CS2_HLAE_LAUNCH_TEMPLATE` (required only when `CS2_CONTROL_BACKEND=hlae`)
- `CS2_HLAE_ARGS`, `CS2_HLAE_CONFIG_DIR`, `CS2_HLAE_HOOK_DLL` (optional, machine-specific)

OBS:
- `OBS_WS_HOST`, `OBS_WS_PORT`, `OBS_WS_PASSWORD`
- `OBS_OUTPUT_DIR` (optional override)

Worker / queue:
- `RENDER_JOB_MAX_RETRIES` (default: `1`)
- `RENDER_JOB_LEASE_TIMEOUT_S` (default: `120`)

Post-process:
- `FFMPEG_EXE`
- `FFPROBE_EXE`
- `POSTPROCESS_PRESET`
- `POSTPROCESS_CRF`
- `POSTPROCESS_AUDIO_BITRATE`
- `POSTPROCESS_THUMBNAIL_OFFSET_S`
- `POSTPROCESS_THUMBNAIL_WIDTH`
- `POSTPROCESS_TRANSCODE_TIMEOUT_S`
- `POSTPROCESS_THUMBNAIL_TIMEOUT_S`
- `POSTPROCESS_PROBE_TIMEOUT_S`
- `POSTPROCESS_MINIMUM_OUTPUT_BYTES`
- `POSTPROCESS_REQUIRE_FFPROBE`

## CS2 / OBS Prerequisites for In-Game Capture

1. CS2 launch options should include:

```text
-usercon -netconport <CS2_NETCON_PORT>
```

2. OBS Studio 28+:
- Open `Tools -> obs-websocket Settings`
- Enable WebSocket server
- Match host/port/password with `.env`

3. Validate readiness before capture:
- `GET /api/ingame/health`
- `GET /api/local/doctor`

## Local Operator Workflow

1. Start backend and open UI.
2. Upload a `.dem` file from Upload view.
3. Parse demo.
4. Review highlights and replay.
5. Render clips:
- In-game: run readiness check first
- Legacy tactical mode: disabled by default and not part of the active workflow
 - Render requests are queued; worker execution happens out-of-process
6. Use Clips gallery for playback and queue results.
7. Run integrity/doctor checks if outputs look inconsistent.

## Validation Endpoints (Production-Readiness)

- `GET /api/health`
: app/runtime/dependency/config summary with next-step hints

- `GET /api/ingame/health?demo_id=<id>`
: CS2/OBS/environment readiness with blockers/warnings/actions

- `GET /api/local/doctor?demo_id=<id>`
: end-to-end local validation summary (app health + in-game readiness + queue + clip integrity)

- `GET /api/clips/integrity`
: detects missing video/thumbnail/metadata and stale references

PowerShell examples:

  Invoke-RestMethod http://localhost:8000/api/health
  Invoke-RestMethod http://localhost:8000/api/ingame/health
  Invoke-RestMethod http://localhost:8000/api/local/doctor

## Render Modes

- `tactical_2d_mp4`
: deprecated legacy parsed-data tactical rendering to MP4, disabled by default

- `cs2_ingame_capture`
: CS2 playback + OBS recording pipeline

Optional control backend:
- `plain`
: standard CS2 control path
- `hlae`
: HLAE-assisted launch/control path, configured through environment variables

Example HLAE setup notes:
- install HLAE separately outside this repository
- point `CS2_HLAE_EXE` to the HLAE launcher executable
- provide a machine-specific `CS2_HLAE_LAUNCH_TEMPLATE`
- ensure that template expands to a valid command that launches CS2 with your preferred HLAE workflow

The repository does not hardcode HLAE paths or a single universal launch command because HLAE setups vary by machine and moviemaking workflow.

## Output Layout

Generated artifacts are under `outputs/generated`.

```text
outputs/generated/
  clips/
    <demo_slug>/
      index.json
      <clip_id>/
        clip.mp4
        thumbnail.jpg
        artifact.json
  queue/
    render_queue.json
```

## Common Failure Cases and What to Check

- Parse fails:
: verify `.dem` validity, awpy installation, and map assets (`awpy get maps`)

- In-game capture blocked:
: check `CS2_EXE`, CS2 launch options, and OBS WebSocket config

- Queue jobs fail repeatedly:
: inspect `GET /api/queue` and retry failed jobs after fixing readiness blockers

- Clip exists but media missing:
: run `GET /api/clips/integrity`, then re-render affected clips

## API Surface (Key Routes)

Demo lifecycle:
- `POST /api/demo/upload`
- `POST /api/demo/{id}/parse`
- `GET /api/demo/{id}/info`

Highlights + plans:
- `GET /api/demo/{id}/highlights`
- `GET /api/demo/{id}/clip-plans`

Replay:
- `GET /api/demo/{id}/replay/rounds`
- `GET /api/demo/{id}/replay/{round}`

Clips:
- `POST /api/demo/{id}/clips/render/{plan_id}`
- `GET /api/demo/{id}/clips`
- `GET /api/clips`
- `GET /api/clips/{clip_id}`
- `GET /api/clips/integrity`

Queue:
- `GET /api/queue`
- `GET /api/queue/job/{job_id}`
- `GET /api/queue/job/{job_id}/events`
- `POST /api/queue/enqueue`
- `POST /api/queue/enqueue-batch`
- `POST /api/queue/retry/{job_id}`
- `POST /api/queue/retry-all-failed`
- `POST /api/queue/cancel/{job_id}`
- `POST /api/queue/cancel-all`
- `POST /api/queue/clear-completed`

Diagnostics:
- `GET /api/health`
- `GET /api/ingame/health`
- `GET /api/local/doctor`

## End-to-End Render Flow

1. Frontend or API calls `POST /api/demo/{id}/clips/render/{plan_id}` or `POST /api/queue/enqueue`
2. Backend validates the clip plan and persists a demo snapshot
3. Backend enqueues a render job in `outputs/generated/queue`
4. Worker process claims the job and updates heartbeat / progress state
5. Worker runs:
   - game-control preparation
   - capture execution
   - FFmpeg post-process
   - clip registration
6. Only after final media passes integrity checks is the job marked `completed`
7. Final clip metadata becomes available through:
   - `GET /api/demo/{id}/clips`
   - `GET /api/clips`
   - `GET /api/queue`
