"""
render_queue.py
Render job queue with persistent JSON storage.

Manages:
- Job creation from clip plans
- Job status lifecycle (queued → claimed → processing → completed/failed)
- Job persistence to disk
- Worker lease management
- Retry logic
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class QueueError(Exception):
    """Raised when a queue operation fails."""


@dataclass
class RenderJob:
    """A single render job in the queue."""
    job_id: str
    clip_id: str
    demo_id: str
    demo_path: str

    # Clip plan data
    clip_plan: dict = field(default_factory=dict)

    # Player identity (carried from clip plan)
    player_name: str = ""
    player_steamid64: str = ""

    # Timing
    round_number: int = 0
    start_tick: int = 0
    anchor_tick: int = 0
    end_tick: int = 0
    round_start_tick: int = 0
    round_end_tick: int = 0
    freeze_end_tick: int = 0
    clip_duration_s: float = 0.0

    # Camera
    pov_mode: str = "player_pov"
    camera_mode: str = "first_person"

    # Output
    output_dir: str = ""
    output_name: str = ""
    fps: int = 60
    width: int = 1920
    height: int = 1080
    video_container: str = "mp4"
    encode_preset: str = "veryfast"

    # Status
    status: str = "queued"  # queued, claimed, processing, completed, failed
    created_at: float = 0.0
    claimed_at: float = 0.0
    started_at: float = 0.0
    completed_at: float = 0.0
    worker_id: str = ""
    retry_count: int = 0
    max_retries: int = 1
    error: str = ""
    warnings: list[str] = field(default_factory=list)

    # Results
    render_recipe: dict | None = None
    render_result: dict | None = None
    capture_result: dict | None = None
    postprocess_result: dict | None = None
    artifact: dict | None = None
    error_code: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> RenderJob:
        known_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in d.items() if k in known_fields}
        return cls(**filtered)


@dataclass
class RenderQueue:
    """Persistent render job queue."""
    queue_dir: str = ""  # Base directory for queue storage

    def __post_init__(self):
        if not self.queue_dir:
            self.queue_dir = str(Path("outputs/generated/queue"))
        Path(self.queue_dir).mkdir(parents=True, exist_ok=True)
        self._jobs_dir.mkdir(parents=True, exist_ok=True)

    @property
    def _jobs_dir(self) -> Path:
        return Path(self.queue_dir) / "jobs"

    def _job_dir(self, job_id: str) -> Path:
        return self._jobs_dir / job_id

    def _job_file(self, job_id: str) -> Path:
        return self._job_dir(job_id) / "job.json"

    def _events_file(self, job_id: str) -> Path:
        return self._job_dir(job_id) / "events.jsonl"

    # ── Job creation ──────────────────────────────────────────────────────

    def create_job(
        self,
        clip_plan: dict,
        demo_id: str,
        demo_path: str,
        output_base_dir: str = "",
        max_retries: int = 1,
    ) -> RenderJob:
        """Create a new render job from a clip plan.

        Args:
            clip_plan: Clip plan dict from clip_planner
            demo_id: Demo session ID
            demo_path: Path to demo file
            output_base_dir: Base directory for clip outputs
            max_retries: Maximum retry count

        Returns:
            Created RenderJob
        """
        job_id = f"rj_{uuid.uuid4().hex[:12]}"
        clip_id = clip_plan.get("clip_plan_id", job_id)

        if not output_base_dir:
            output_base_dir = str(Path("outputs/generated/clips") / demo_id)
        output_dir = str(Path(output_base_dir) / f"{clip_id}_{job_id[:8]}")
        output_name = clip_plan.get("output_name") or clip_id

        job = RenderJob(
            job_id=job_id,
            clip_id=clip_id,
            demo_id=demo_id,
            demo_path=demo_path,
            clip_plan=clip_plan,
            player_name=clip_plan.get("player_name", ""),
            player_steamid64=clip_plan.get("player_steamid64", ""),
            round_number=int(clip_plan.get("round_number", 0)),
            start_tick=int(clip_plan.get("start_tick", 0)),
            anchor_tick=int(clip_plan.get("anchor_tick", 0)),
            end_tick=int(clip_plan.get("end_tick", 0)),
            round_start_tick=int(clip_plan.get("round_start_tick", 0)),
            round_end_tick=int(clip_plan.get("round_end_tick", 0)),
            freeze_end_tick=int(clip_plan.get("freeze_end_tick", 0)),
            clip_duration_s=float(clip_plan.get("duration_s", 0)),
            pov_mode=clip_plan.get("pov_mode", "player_pov"),
            camera_mode=clip_plan.get("camera_mode", "first_person"),
            output_dir=output_dir,
            output_name=output_name,
            fps=int(clip_plan.get("fps", 60) or 60),
            width=int(clip_plan.get("width", 1920) or 1920),
            height=int(clip_plan.get("height", 1080) or 1080),
            video_container=clip_plan.get("video_container", "mp4"),
            encode_preset=clip_plan.get("encode_preset", "veryfast"),
            status="queued",
            created_at=time.time(),
            max_retries=max_retries,
        )

        self._save_job(job)
        self._log_event(job.job_id, "job_created", {
            "clip_id": clip_id,
            "player": job.player_name,
            "steamid64": job.player_steamid64,
            "round": job.round_number,
            "ticks": f"{job.start_tick}-{job.anchor_tick}-{job.end_tick}",
        })

        log.info(f"Job created: {job_id} for clip {clip_id}")
        return job

    # ── Job lifecycle ─────────────────────────────────────────────────────

    def claim_job(self, worker_id: str) -> RenderJob | None:
        """Claim the next queued job for a worker.

        Returns:
            The claimed job, or None if no jobs available.
        """
        for job_id in self._list_job_ids():
            job = self._load_job(job_id)
            if job is None:
                continue
            if job.status != "queued":
                continue

            # Check lease timeout on claimed jobs
            if job.status == "claimed" and job.claimed_at > 0:
                age = time.time() - job.claimed_at
                if age > 120:  # lease expired
                    job.status = "queued"
                    job.worker_id = ""

            job.status = "claimed"
            job.claimed_at = time.time()
            job.worker_id = worker_id
            self._save_job(job)
            self._log_event(job_id, "job_claimed", {"worker_id": worker_id})
            log.info(f"Job {job_id} claimed by {worker_id}")
            return job
        return None

    def start_job(self, job_id: str) -> RenderJob | None:
        """Mark a job as processing."""
        job = self._load_job(job_id)
        if job is None:
            return None
        job.status = "processing"
        job.started_at = time.time()
        self._save_job(job)
        self._log_event(job_id, "job_started")
        return job

    def complete_job(
        self,
        job_id: str,
        render_recipe: dict | None = None,
        render_result: dict | None = None,
        capture_result: dict | None = None,
        postprocess_result: dict | None = None,
        artifact: dict | None = None,
    ) -> RenderJob | None:
        """Mark a job as completed."""
        job = self._load_job(job_id)
        if job is None:
            return None
        job.status = "completed"
        job.completed_at = time.time()
        job.render_recipe = render_recipe
        job.render_result = render_result
        job.capture_result = capture_result
        job.postprocess_result = postprocess_result
        job.artifact = artifact
        self._save_job(job)
        self._log_event(job_id, "job_completed", {
            "duration_ms": round((job.completed_at - job.started_at) * 1000, 1) if job.started_at else 0,
        })
        log.info(f"Job {job_id} completed")
        return job

    def fail_job(
        self,
        job_id: str,
        error: str,
        capture_result: dict | None = None,
        render_recipe: dict | None = None,
        render_result: dict | None = None,
        error_code: str = "",
    ) -> RenderJob | None:
        """Mark a job as failed. May retry if retries remain."""
        job = self._load_job(job_id)
        if job is None:
            return None

        job.error = error
        job.error_code = error_code
        job.render_recipe = render_recipe
        job.render_result = render_result
        job.capture_result = capture_result
        job.completed_at = time.time()

        if job.retry_count < job.max_retries:
            job.retry_count += 1
            job.status = "queued"
            job.worker_id = ""
            job.claimed_at = 0
            job.started_at = 0
            self._log_event(job_id, "job_retried", {
                "retry_count": job.retry_count,
                "error": error,
                "error_code": error_code,
            })
            log.info(f"Job {job_id} retried ({job.retry_count}/{job.max_retries})")
        else:
            job.status = "failed"
            self._log_event(job_id, "job_failed", {"error": error, "error_code": error_code})
            log.error(f"Job {job_id} failed permanently: {error}")

        self._save_job(job)
        return job

    # ── Query ─────────────────────────────────────────────────────────────

    def get_job(self, job_id: str) -> RenderJob | None:
        return self._load_job(job_id)

    def list_jobs(self, demo_id: str | None = None, status: str | None = None) -> list[RenderJob]:
        """List jobs, optionally filtered."""
        jobs = []
        for job_id in self._list_job_ids():
            job = self._load_job(job_id)
            if job is None:
                continue
            if demo_id and job.demo_id != demo_id:
                continue
            if status and job.status != status:
                continue
            jobs.append(job)
        jobs.sort(key=lambda j: j.created_at, reverse=True)
        return jobs

    def get_queue_status(self, demo_id: str | None = None) -> dict:
        """Get queue statistics."""
        jobs = self.list_jobs(demo_id=demo_id)
        counts: dict[str, int] = {}
        for j in jobs:
            counts[j.status] = counts.get(j.status, 0) + 1
        return {
            "total": len(jobs),
            "by_status": counts,
            "queued": counts.get("queued", 0),
            "processing": counts.get("processing", 0),
            "completed": counts.get("completed", 0),
            "failed": counts.get("failed", 0),
        }

    def clear_jobs(self, statuses: set[str] | None = None) -> int:
        """Delete persisted jobs, optionally filtered by status.

        Returns the number of removed jobs.
        """
        removed = 0
        for job_id in list(self._list_job_ids()):
            job = self._load_job(job_id)
            if job is None:
                continue
            if statuses is not None and job.status not in statuses:
                continue
            job_dir = self._job_dir(job_id)
            if job_dir.exists():
                shutil.rmtree(job_dir, ignore_errors=True)
                removed += 1
        if removed:
            log.info("Cleared %s render jobs from %s", removed, self.queue_dir)
        return removed

    def clear_all_jobs(self) -> int:
        """Delete every persisted job in the queue."""
        return self.clear_jobs(statuses=None)

    # ── Persistence ───────────────────────────────────────────────────────

    def _list_job_ids(self) -> list[str]:
        if not self._jobs_dir.exists():
            return []
        return sorted([d.name for d in self._jobs_dir.iterdir() if d.is_dir()])

    def _save_job(self, job: RenderJob) -> None:
        job_dir = self._job_dir(job.job_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        with open(self._job_file(job.job_id), "w", encoding="utf-8") as f:
            json.dump(job.to_dict(), f, indent=2, default=str)

    def _load_job(self, job_id: str) -> RenderJob | None:
        path = self._job_file(job_id)
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return RenderJob.from_dict(data)
        except (json.JSONDecodeError, TypeError, KeyError) as e:
            log.warning(f"Failed to load job {job_id}: {e}")
            return None

    def _log_event(self, job_id: str, event: str, extra: dict | None = None) -> None:
        events_file = self._events_file(job_id)
        events_file.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
            "job_id": job_id,
            "event": event,
        }
        if extra:
            entry.update(extra)
        with open(events_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
