"""
render_worker.py
Render worker that processes jobs from the queue.

Run standalone:
    python -m src.render_worker

Or import and use RenderWorker programmatically.
"""
from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.cs2_config import CS2Config, load_config
from src.capture_pipeline import CapturePipeline, CaptureRequest, CaptureResult, CaptureError
from src.hlae_worker_runtime import HLAEWorkerRuntime
from src.render_postprocess import postprocess_clip, PostprocessResult
from src.render_queue import RenderQueue, RenderJob, QueueError

log = logging.getLogger(__name__)

DEFAULT_OUTPUT_ROOT = Path("outputs/generated")
DEFAULT_QUEUE_DIR = DEFAULT_OUTPUT_ROOT / "queue_v2"
LEGACY_QUEUE_DIR = DEFAULT_OUTPUT_ROOT / "queue"


def _resolve_queue_dir(requested: str) -> Path:
    """Prefer the active queue_v2 storage over the legacy queue path.

    Older startup instructions used `outputs/generated/queue`. The API now
    enqueues into `queue_v2`, so transparently migrate legacy worker commands.
    """
    requested_path = Path(requested)
    if requested_path == LEGACY_QUEUE_DIR and DEFAULT_QUEUE_DIR.exists():
        log.warning(
            "Legacy queue dir requested (%s); using active queue dir (%s) instead.",
            requested_path,
            DEFAULT_QUEUE_DIR,
        )
        return DEFAULT_QUEUE_DIR
    return requested_path


@dataclass
class RenderWorker:
    """Processes render jobs from the queue."""
    config: CS2Config | None = None
    queue: RenderQueue | None = None
    worker_id: str = ""
    poll_interval_s: float = 5.0
    _running: bool = False

    def __post_init__(self):
        if self.config is None:
            self.config = load_config()
        if self.queue is None:
            self.queue = RenderQueue(queue_dir=str(DEFAULT_QUEUE_DIR))
        if not self.worker_id:
            self.worker_id = self.config.render_worker_id or f"worker_{uuid.uuid4().hex[:8]}"

    def run_once(self) -> RenderJob | None:
        """Try to claim and process one job.

        Returns:
            The processed job, or None if no jobs available.
        """
        job = self.queue.claim_job(self.worker_id)
        if job is None:
            return None

        log.info(f"[{self.worker_id}] Processing job {job.job_id} (clip: {job.clip_id})")
        return self._process_job(job)

    def run_loop(self) -> None:
        """Run continuously, polling for jobs."""
        self._running = True
        log.info(f"[{self.worker_id}] Worker started, polling every {self.poll_interval_s}s")

        while self._running:
            try:
                job = self.run_once()
                if job is None:
                    time.sleep(self.poll_interval_s)
            except KeyboardInterrupt:
                log.info(f"[{self.worker_id}] Worker interrupted")
                break
            except Exception as e:
                log.exception(f"[{self.worker_id}] Unexpected error in worker loop: {e}")
                time.sleep(self.poll_interval_s)

        log.info(f"[{self.worker_id}] Worker stopped")

    def stop(self) -> None:
        """Signal the worker to stop."""
        self._running = False

    def _process_job(self, job: RenderJob) -> RenderJob:
        backend = (self.config.render_backend or "hlae").strip().lower()
        if backend == "hlae":
            return self._process_job_hlae(job)
        return self._process_job_legacy(job)

    def _process_job_hlae(self, job: RenderJob) -> RenderJob:
        """Primary render path: HLAE + mirv_streams + ffmpeg finalize."""
        self.queue.start_job(job.job_id)

        if not job.demo_path or not Path(job.demo_path).exists():
            return self._fail_job(job, f"Demo file not found: {job.demo_path}", error_code="demo_missing")
        if not job.player_name:
            return self._fail_job(job, "Missing player_name in job", error_code="missing_player")

        runtime = HLAEWorkerRuntime(config=self.config)
        try:
            recipe, render_result, artifact = runtime.render_job(job)
        except Exception as e:
            return self._fail_job(job, f"HLAE runtime error: {e}", error_code="runtime_failure")

        if not render_result.success:
            return self._fail_job(
                job,
                render_result.error_message or "HLAE render failed",
                render_recipe=recipe.to_dict(),
                render_result=render_result.to_dict(),
                error_code=render_result.error_code,
            )

        completed_job = self.queue.complete_job(
            job.job_id,
            render_recipe=recipe.to_dict(),
            render_result=render_result.to_dict(),
            artifact=artifact,
        )
        log.info(f"[{self.worker_id}] Job {job.job_id} completed successfully via HLAE")
        return completed_job or job

    def _process_job_legacy(self, job: RenderJob) -> RenderJob:
        """Process a single render job through the full pipeline.

        Steps:
        1. Mark job as processing
        2. Run capture pipeline
        3. Post-process if capture succeeded
        4. Build artifact metadata
        5. Mark job completed or failed
        """
        self.queue.start_job(job.job_id)

        # Validate required fields
        if not job.player_name:
            return self._fail_job(job, "Missing player_name in job")
        if not job.demo_path or not Path(job.demo_path).exists():
            return self._fail_job(job, f"Demo file not found: {job.demo_path}")

        # If player_steamid64 is missing, this is a warning but not fatal
        if not job.player_steamid64:
            job.warnings.append("player_steamid64 missing — POV targeting uses name only")

        # ── Step 1: Capture ───────────────────────────────────────────────
        pipeline = CapturePipeline(config=self.config)
        try:
            capture_request = CaptureRequest(
                clip_id=job.clip_id,
                demo_path=job.demo_path,
                round_number=job.round_number,
                start_tick=job.start_tick,
                anchor_tick=job.anchor_tick,
                end_tick=job.end_tick,
                round_start_tick=job.round_start_tick,
                round_end_tick=job.round_end_tick,
                freeze_end_tick=job.freeze_end_tick,
                player_name=job.player_name,
                player_steamid64=job.player_steamid64,
                pov_mode=job.pov_mode,
                camera_mode=job.camera_mode,
                output_dir=job.output_dir,
            )
            capture_result = pipeline.execute(capture_request)
        except Exception as e:
            return self._fail_job(job, f"Capture pipeline error: {e}")
        finally:
            pipeline.cleanup()

        if capture_result.status != "completed":
            return self._fail_job(
                job,
                f"Capture failed at {capture_result.failure_stage}: {capture_result.error}",
                capture_result=capture_result.to_dict(),
            )

        # ── Step 2: Post-process ──────────────────────────────────────────
        raw_path = capture_result.raw_output_path
        if not raw_path or not Path(raw_path).exists():
            return self._fail_job(
                job,
                "No raw output file after capture",
                capture_result=capture_result.to_dict(),
            )

        try:
            pp_result = postprocess_clip(
                config=self.config,
                raw_path=raw_path,
                output_dir=job.output_dir,
                clip_id=job.clip_id,
            )
        except Exception as e:
            return self._fail_job(
                job,
                f"Post-process error: {e}",
                capture_result=capture_result.to_dict(),
            )

        if pp_result.status != "completed":
            return self._fail_job(
                job,
                f"Post-process failed: {pp_result.error}",
                capture_result=capture_result.to_dict(),
            )

        # ── Step 3: Build artifact ────────────────────────────────────────
        artifact = self._build_artifact(job, capture_result, pp_result)

        # Save artifact.json
        artifact_path = Path(job.output_dir) / "artifact.json"
        with open(artifact_path, "w", encoding="utf-8") as f:
            json.dump(artifact, f, indent=2, default=str)

        # ── Step 4: Complete ──────────────────────────────────────────────
        completed_job = self.queue.complete_job(
            job.job_id,
            capture_result=capture_result.to_dict(),
            postprocess_result=pp_result.to_dict(),
            artifact=artifact,
        )

        log.info(f"[{self.worker_id}] Job {job.job_id} completed successfully")
        return completed_job or job

    def _fail_job(
        self,
        job: RenderJob,
        error: str,
        capture_result: dict | None = None,
        render_recipe: dict | None = None,
        render_result: dict | None = None,
        error_code: str = "",
    ) -> RenderJob:
        """Mark job as failed."""
        log.error(f"[{self.worker_id}] Job {job.job_id} failed: {error}")
        failed = self.queue.fail_job(
            job.job_id,
            error,
            capture_result=capture_result,
            render_recipe=render_recipe,
            render_result=render_result,
            error_code=error_code,
        )
        return failed or job

    def _build_artifact(
        self,
        job: RenderJob,
        capture: CaptureResult,
        postprocess: PostprocessResult,
    ) -> dict:
        """Build the artifact metadata dict for a completed clip."""
        output_mp4 = Path(job.output_dir) / "clip.mp4"
        thumb_jpg = Path(job.output_dir) / "thumbnail.jpg"

        # Compute URLs (relative to generated dir)
        def _rel_url(p: Path) -> str | None:
            if not p.exists():
                return None
            try:
                generated = Path("outputs/generated")
                rel = p.relative_to(generated).as_posix()
                return f"/generated/{rel}"
            except ValueError:
                return None

        return {
            "artifact_schema_version": 2,
            "clip_id": job.clip_id,
            "job_id": job.job_id,
            "demo_id": job.demo_id,
            "status": "completed",
            "player_name": job.player_name,
            "player_steamid64": job.player_steamid64,
            "event_type": job.clip_plan.get("event_type", ""),
            "round_number": job.round_number,
            "start_tick": job.start_tick,
            "anchor_tick": job.anchor_tick,
            "end_tick": job.end_tick,
            "duration_s": postprocess.duration_s,
            "frame_count": postprocess.frame_count,
            "resolution": postprocess.resolution,
            "pov_mode": job.pov_mode,
            "pov_player": job.player_name,
            "pov_steamid64": job.player_steamid64,
            "output_path": str(output_mp4) if output_mp4.exists() else None,
            "output_url": _rel_url(output_mp4),
            "thumbnail_path": str(thumb_jpg) if thumb_jpg.exists() else None,
            "thumbnail_url": _rel_url(thumb_jpg),
            "output_size_bytes": postprocess.output_size_bytes,
            "codec": postprocess.codec,
            "score": job.clip_plan.get("score", 0),
            "priority": job.clip_plan.get("priority", 0),
            "selection_reason": job.clip_plan.get("selection_reason", ""),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
            "capture_duration_ms": capture.total_duration_ms,
            "warnings": job.warnings + (capture.warnings or []),
        }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Process CS2 clip render jobs.")
    parser.add_argument(
        "--queue-dir",
        default=str(DEFAULT_QUEUE_DIR),
        help=f"Queue directory to poll (default: {DEFAULT_QUEUE_DIR})",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=5.0,
        help="Polling interval in seconds while idle.",
    )
    parser.add_argument(
        "--worker-id",
        default="",
        help="Optional explicit worker ID.",
    )
    parser.add_argument(
        "--output-root",
        default=str(DEFAULT_OUTPUT_ROOT),
        help="Accepted for compatibility with existing startup commands.",
    )
    return parser


def main(argv: list[str] | None = None):
    """Entry point for standalone worker."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    from dotenv import load_dotenv
    load_dotenv()

    args = _build_parser().parse_args(argv)
    config = load_config()
    resolved_queue_dir = _resolve_queue_dir(args.queue_dir)
    queue = RenderQueue(queue_dir=str(resolved_queue_dir))
    worker = RenderWorker(
        config=config,
        queue=queue,
        worker_id=args.worker_id,
        poll_interval_s=args.poll_interval,
    )

    # Handle graceful shutdown
    def _signal_handler(sig, frame):
        log.info("Shutdown signal received")
        worker.stop()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    log.info(
        "Render worker starting: %s (queue_dir=%s, output_root=%s, poll_interval=%.1fs)",
        worker.worker_id,
        resolved_queue_dir,
        args.output_root,
        args.poll_interval,
    )
    worker.run_loop()


if __name__ == "__main__":
    main()
