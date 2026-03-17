"""
render_queue.py
Persistent render queue for worker-based clip rendering.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterator

from src.render_job_store import RenderJobStateStore, utc_now_iso
from src.render_modes import RENDER_MODE_TACTICAL_2D
from src.utils import atomic_json_write

logger = logging.getLogger(__name__)


class QueueJobStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in (QueueJobStatus.COMPLETED, QueueJobStatus.FAILED, QueueJobStatus.CANCELLED)

    @property
    def is_active(self) -> bool:
        return self == QueueJobStatus.PROCESSING


def _legacy_status(status: QueueJobStatus) -> str:
    mapping = {
        QueueJobStatus.PENDING: "queued",
        QueueJobStatus.PROCESSING: "running",
        QueueJobStatus.COMPLETED: "completed",
        QueueJobStatus.FAILED: "failed",
        QueueJobStatus.CANCELLED: "cancelled",
    }
    return mapping[status]


@dataclass
class RenderQueueJob:
    job_id: str = ""
    demo_id: str = ""
    clip_plan_id: str = ""
    render_mode: str = "cs2_ingame_capture"
    target_settings: dict[str, Any] = field(default_factory=dict)
    status: QueueJobStatus = QueueJobStatus.PENDING

    title: str = ""
    primary_player: str = ""
    round_number: int = 0
    clip_type: str = ""
    score: float = 0.0
    render_preset: str = ""
    source_highlight_id: str = ""

    queued_at: str = ""
    updated_at: str = ""
    started_at: str = ""
    finished_at: str = ""
    claimed_at: str = ""
    heartbeat_at: str = ""
    lease_expires_at: str = ""

    retry_count: int = 0
    max_retries: int = 1
    timeout_s: int = 900

    error: str | None = None
    warnings: list[str] = field(default_factory=list)
    progress_stage: str = ""
    failure_phase: str | None = None

    clip_id: str | None = None
    clip_record: dict[str, Any] | None = None
    artifact_dir: str | None = None
    artifact_metadata_path: str | None = None
    source_clip_plan_id: str | None = None

    cancellation_requested: bool = False
    worker_id: str | None = None
    worker_pid: int | None = None
    demo_snapshot_path: str | None = None
    state_path: str | None = None
    events_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "demo_id": self.demo_id,
            "clip_plan_id": self.clip_plan_id,
            "render_mode": self.render_mode,
            "target_settings": dict(self.target_settings),
            "status": self.status.value,
            "legacy_status": _legacy_status(self.status),
            "title": self.title,
            "primary_player": self.primary_player,
            "round_number": self.round_number,
            "clip_type": self.clip_type,
            "score": self.score,
            "render_preset": self.render_preset,
            "source_highlight_id": self.source_highlight_id,
            "queued_at": self.queued_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "claimed_at": self.claimed_at,
            "heartbeat_at": self.heartbeat_at,
            "lease_expires_at": self.lease_expires_at,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "timeout_s": self.timeout_s,
            "error": self.error,
            "warnings": list(self.warnings),
            "progress_stage": self.progress_stage,
            "failure_phase": self.failure_phase,
            "clip_id": self.clip_id,
            "clip_record": self.clip_record,
            "artifact_dir": self.artifact_dir,
            "artifact_metadata_path": self.artifact_metadata_path,
            "source_clip_plan_id": self.source_clip_plan_id,
            "cancellation_requested": self.cancellation_requested,
            "worker_id": self.worker_id,
            "worker_pid": self.worker_pid,
            "demo_snapshot_path": self.demo_snapshot_path,
            "state_path": self.state_path,
            "events_path": self.events_path,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RenderQueueJob":
        raw_status = str(payload.get("status") or "").strip().lower()
        if raw_status in ("queued", "pending"):
            status = QueueJobStatus.PENDING
        elif raw_status in ("running", "processing", "validating", "preparing", "recording", "finalizing"):
            status = QueueJobStatus.PROCESSING
        elif raw_status == "completed":
            status = QueueJobStatus.COMPLETED
        elif raw_status == "cancelled":
            status = QueueJobStatus.CANCELLED
        else:
            status = QueueJobStatus.FAILED

        timeout_default = int(payload.get("timeout_s") or 0)
        if timeout_default <= 0:
            target_settings = payload.get("target_settings") if isinstance(payload.get("target_settings"), dict) else {}
            timeout_default = int(target_settings.get("timeout_s") or 900)

        return cls(
            job_id=str(payload.get("job_id") or ""),
            demo_id=str(payload.get("demo_id") or ""),
            clip_plan_id=str(payload.get("clip_plan_id") or ""),
            render_mode=str(payload.get("render_mode") or "cs2_ingame_capture"),
            target_settings=dict(payload.get("target_settings") or {}),
            status=status,
            title=str(payload.get("title") or ""),
            primary_player=str(payload.get("primary_player") or ""),
            round_number=int(payload.get("round_number") or 0),
            clip_type=str(payload.get("clip_type") or ""),
            score=float(payload.get("score") or 0.0),
            render_preset=str(payload.get("render_preset") or ""),
            source_highlight_id=str(payload.get("source_highlight_id") or ""),
            queued_at=str(payload.get("queued_at") or ""),
            updated_at=str(payload.get("updated_at") or payload.get("queued_at") or ""),
            started_at=str(payload.get("started_at") or ""),
            finished_at=str(payload.get("finished_at") or ""),
            claimed_at=str(payload.get("claimed_at") or ""),
            heartbeat_at=str(payload.get("heartbeat_at") or ""),
            lease_expires_at=str(payload.get("lease_expires_at") or ""),
            retry_count=int(payload.get("retry_count") or 0),
            max_retries=max(0, int(payload.get("max_retries") if payload.get("max_retries") is not None else 1)),
            timeout_s=max(30, timeout_default),
            error=payload.get("error"),
            warnings=list(payload.get("warnings") or []),
            progress_stage=str(payload.get("progress_stage") or ""),
            failure_phase=payload.get("failure_phase"),
            clip_id=payload.get("clip_id"),
            clip_record=payload.get("clip_record") if isinstance(payload.get("clip_record"), dict) else None,
            artifact_dir=payload.get("artifact_dir"),
            artifact_metadata_path=payload.get("artifact_metadata_path"),
            source_clip_plan_id=payload.get("source_clip_plan_id"),
            cancellation_requested=bool(payload.get("cancellation_requested") or False),
            worker_id=payload.get("worker_id"),
            worker_pid=payload.get("worker_pid"),
            demo_snapshot_path=payload.get("demo_snapshot_path"),
            state_path=payload.get("state_path"),
            events_path=payload.get("events_path"),
        )


class RenderQueueManager:
    def __init__(self, persist_dir: Path | None = None, max_retries: int = 1, lease_timeout_s: int = 120):
        self._persist_dir = Path(persist_dir) if persist_dir else None
        self._max_retries = max(0, int(max_retries))
        self._lease_timeout_s = max(30, int(lease_timeout_s))
        self._state_store = RenderJobStateStore(self._persist_dir) if self._persist_dir else None
        if self._persist_dir:
            self._persist_dir.mkdir(parents=True, exist_ok=True)
            self._ensure_store_exists()

    @property
    def queue_path(self) -> Path | None:
        if not self._persist_dir:
            return None
        return self._persist_dir / "render_queue.json"

    @property
    def lock_path(self) -> Path | None:
        if not self._persist_dir:
            return None
        return self._persist_dir / "render_queue.lock"

    def enqueue(
        self,
        demo_id: str,
        clip_plan_id: str,
        render_mode: str = "cs2_ingame_capture",
        target_settings: dict[str, Any] | None = None,
        clip_plan: dict[str, Any] | None = None,
        demo_snapshot_path: str | None = None,
    ) -> RenderQueueJob:
        plan = clip_plan or {}
        settings = dict(target_settings or {})
        timeout_s = max(30, int(settings.get("timeout_s") or 900))

        with self._mutate_jobs() as jobs:
            existing = self._find_duplicate_job(
                jobs,
                demo_id=demo_id,
                clip_plan_id=clip_plan_id,
                render_mode=render_mode,
                target_settings=settings,
            )
            if existing:
                return existing

            now = utc_now_iso()
            job = RenderQueueJob(
                job_id=f"rq_{uuid.uuid4().hex[:12]}",
                demo_id=demo_id,
                clip_plan_id=clip_plan_id,
                render_mode=render_mode,
                target_settings=settings,
                status=QueueJobStatus.PENDING,
                title=str(plan.get("title") or ""),
                primary_player=str(plan.get("primary_player") or ""),
                round_number=int(plan.get("round_number") or 0),
                clip_type=str(plan.get("clip_type") or ""),
                score=float(plan.get("score") or 0.0),
                render_preset=str(settings.get("render_preset") or ""),
                source_highlight_id=str(plan.get("source_highlight_id") or ""),
                queued_at=now,
                updated_at=now,
                max_retries=self._max_retries,
                timeout_s=timeout_s,
                demo_snapshot_path=demo_snapshot_path,
            )
            jobs.append(job)
            self.append_event(
                job.job_id,
                event="job_enqueued",
                demo_id=demo_id,
                clip_plan_id=clip_plan_id,
                render_mode=render_mode,
                timeout_s=timeout_s,
            )
            return job

    def enqueue_batch(
        self,
        demo_id: str,
        clip_plans: list[dict[str, Any]],
        render_mode: str = "cs2_ingame_capture",
        target_settings: dict[str, Any] | None = None,
        demo_snapshot_path: str | None = None,
    ) -> list[RenderQueueJob]:
        created: list[RenderQueueJob] = []
        settings = dict(target_settings or {})
        timeout_s = max(30, int(settings.get("timeout_s") or 900))
        now = utc_now_iso()

        with self._mutate_jobs() as jobs:
            for plan in clip_plans:
                clip_plan_id = str(plan.get("clip_plan_id") or "")
                existing = self._find_duplicate_job(
                    jobs,
                    demo_id=demo_id,
                    clip_plan_id=clip_plan_id,
                    render_mode=render_mode,
                    target_settings=settings,
                )
                if existing:
                    created.append(existing)
                    continue

                job = RenderQueueJob(
                    job_id=f"rq_{uuid.uuid4().hex[:12]}",
                    demo_id=demo_id,
                    clip_plan_id=clip_plan_id,
                    render_mode=render_mode,
                    target_settings=settings,
                    status=QueueJobStatus.PENDING,
                    title=str(plan.get("title") or ""),
                    primary_player=str(plan.get("primary_player") or ""),
                    round_number=int(plan.get("round_number") or 0),
                    clip_type=str(plan.get("clip_type") or ""),
                    score=float(plan.get("score") or 0.0),
                    render_preset=str(settings.get("render_preset") or ""),
                    source_highlight_id=str(plan.get("source_highlight_id") or ""),
                    queued_at=now,
                    updated_at=now,
                    max_retries=self._max_retries,
                    timeout_s=timeout_s,
                    demo_snapshot_path=demo_snapshot_path,
                )
                jobs.append(job)
                created.append(job)
                self.append_event(
                    job.job_id,
                    event="job_enqueued",
                    demo_id=demo_id,
                    clip_plan_id=clip_plan_id,
                    render_mode=render_mode,
                    timeout_s=timeout_s,
                )
        return created

    def cancel(self, job_id: str) -> RenderQueueJob | None:
        with self._mutate_jobs() as jobs:
            job = self._find_job(jobs, job_id)
            if not job:
                return None
            if job.status == QueueJobStatus.PENDING:
                job.status = QueueJobStatus.CANCELLED
                job.finished_at = datetime.now(timezone.utc).isoformat()
                job.updated_at = job.finished_at
                self.append_event(job.job_id, level="warning", event="job_cancelled")
            elif job.status == QueueJobStatus.PROCESSING:
                job.cancellation_requested = True
                job.updated_at = utc_now_iso()
                self.append_event(job.job_id, level="warning", event="job_cancellation_requested")
            return job

    def cancel_all_queued(self) -> int:
        now = utc_now_iso()
        with self._mutate_jobs() as jobs:
            count = 0
            for job in jobs:
                if job.status == QueueJobStatus.PENDING:
                    job.status = QueueJobStatus.CANCELLED
                    job.finished_at = now
                    job.updated_at = now
                    count += 1
            return count

    def retry(self, job_id: str) -> RenderQueueJob | None:
        with self._mutate_jobs() as jobs:
            job = self._find_job(jobs, job_id)
            if not job:
                return None
            if job.status not in (QueueJobStatus.FAILED, QueueJobStatus.CANCELLED):
                return None
            if not self._is_retryable(job):
                return None
            self._reset_for_retry(job, increment_retry=False)
            self.append_event(job.job_id, event="job_retried", retry_count=job.retry_count)
            return job

    def retry_all_failed(self) -> int:
        with self._mutate_jobs() as jobs:
            count = 0
            for job in jobs:
                if job.status == QueueJobStatus.FAILED and self._is_retryable(job):
                    self._reset_for_retry(job, increment_retry=False)
                    count += 1
            return count

    def clear_completed(self) -> int:
        with self._mutate_jobs() as jobs:
            before = len(jobs)
            jobs[:] = [j for j in jobs if j.status not in (QueueJobStatus.COMPLETED, QueueJobStatus.CANCELLED)]
            return before - len(jobs)

    def clear_failed(self) -> int:
        with self._mutate_jobs() as jobs:
            before = len(jobs)
            jobs[:] = [j for j in jobs if j.status != QueueJobStatus.FAILED]
            return before - len(jobs)

    def get_job(self, job_id: str) -> RenderQueueJob | None:
        return self._find_job(self._read_jobs(), job_id)

    def get_jobs_list(self) -> list[dict[str, Any]]:
        return [job.to_dict() for job in self._read_jobs()]

    def get_status(self) -> dict[str, Any]:
        jobs = [job.to_dict() for job in self._read_jobs()]
        pending = [j for j in jobs if j.get("status") == QueueJobStatus.PENDING.value]
        active = [j for j in jobs if j.get("status") == QueueJobStatus.PROCESSING.value]
        completed = [j for j in jobs if j.get("status") == QueueJobStatus.COMPLETED.value]
        failed = [j for j in jobs if j.get("status") == QueueJobStatus.FAILED.value]
        cancelled = [j for j in jobs if j.get("status") == QueueJobStatus.CANCELLED.value]

        last_completed = max(completed, key=lambda x: x.get("finished_at") or "", default=None)
        last_failed = max(failed, key=lambda x: x.get("finished_at") or "", default=None)

        return {
            "queue_size": len(pending),
            "active_count": len(active),
            "completed_count": len(completed),
            "failed_count": len(failed),
            "cancelled_count": len(cancelled),
            "total_jobs": len(jobs),
            "active_job": active[0] if active else None,
            "last_completed_job": last_completed,
            "last_failed_job": last_failed,
            "jobs": jobs,
        }

    def claim_next_job(self, worker_id: str, worker_pid: int | None = None) -> RenderQueueJob | None:
        now = utc_now_iso()
        with self._mutate_jobs() as jobs:
            self._recover_stale_jobs(jobs)
            for job in jobs:
                if job.status != QueueJobStatus.PENDING:
                    continue
                job.status = QueueJobStatus.PROCESSING
                job.started_at = now
                job.claimed_at = now
                job.heartbeat_at = now
                job.updated_at = now
                job.lease_expires_at = self._lease_expiry_iso()
                job.worker_id = worker_id
                job.worker_pid = worker_pid
                job.progress_stage = "claimed"
                self.append_event(job.job_id, event="job_claimed", worker_id=worker_id, worker_pid=worker_pid)
                return job
        return None

    def touch_job(self, job_id: str, worker_id: str) -> None:
        with self._mutate_jobs() as jobs:
            job = self._find_job(jobs, job_id)
            if not job or job.worker_id != worker_id:
                return
            now = utc_now_iso()
            job.heartbeat_at = now
            job.updated_at = now
            job.lease_expires_at = self._lease_expiry_iso()

    def set_job_stage(self, job_id: str, worker_id: str, stage: str) -> None:
        with self._mutate_jobs() as jobs:
            job = self._find_job(jobs, job_id)
            if not job or job.worker_id != worker_id:
                return
            now = utc_now_iso()
            job.progress_stage = str(stage)
            job.heartbeat_at = now
            job.updated_at = now
            job.lease_expires_at = self._lease_expiry_iso()

    def complete_job(self, job_id: str, worker_id: str, clip_record: dict[str, Any]) -> None:
        with self._mutate_jobs() as jobs:
            job = self._find_job(jobs, job_id)
            if not job or job.worker_id != worker_id:
                return
            if job.cancellation_requested:
                job.status = QueueJobStatus.CANCELLED
            else:
                job.status = QueueJobStatus.COMPLETED
            now = utc_now_iso()
            job.finished_at = now
            job.heartbeat_at = now
            job.updated_at = now
            job.lease_expires_at = ""
            job.progress_stage = "done"
            job.failure_phase = None
            job.error = None
            job.clip_id = clip_record.get("clip_id")
            job.clip_record = clip_record
            job.artifact_dir = clip_record.get("asset_dir") or ((clip_record.get("artifacts") or {}).get("base_dir"))
            job.artifact_metadata_path = clip_record.get("artifact_metadata_path") or ((clip_record.get("artifacts") or {}).get("metadata") or {}).get("path")
            job.source_clip_plan_id = clip_record.get("clip_plan_id")
            self.append_event(job.job_id, event="job_completed", clip_id=job.clip_id, status=job.status.value)

    def fail_job(self, job_id: str, worker_id: str, error: str, failure_phase: str = "execution") -> None:
        with self._mutate_jobs() as jobs:
            job = self._find_job(jobs, job_id)
            if not job or job.worker_id != worker_id:
                return

            now = utc_now_iso()
            if job.cancellation_requested:
                job.status = QueueJobStatus.CANCELLED
                job.finished_at = now
                job.heartbeat_at = now
                job.updated_at = now
                job.lease_expires_at = ""
                job.error = "cancelled"
                job.failure_phase = "cancelled"
                job.progress_stage = "cancelled"
                self.append_event(job.job_id, level="warning", event="job_cancelled", failure_phase="cancelled")
                return

            can_retry = job.retry_count < job.max_retries
            if can_retry:
                job.error = str(error)
                job.failure_phase = failure_phase
                job.progress_stage = "retrying"
                job.updated_at = now
                job.lease_expires_at = ""
                self.append_event(job.job_id, level="warning", event="job_retry_scheduled", error=str(error), failure_phase=failure_phase)
                self._reset_for_retry(job, increment_retry=True)
                return

            job.status = QueueJobStatus.FAILED
            job.error = str(error)
            job.failure_phase = failure_phase
            job.progress_stage = "failed"
            job.finished_at = now
            job.heartbeat_at = now
            job.updated_at = now
            job.lease_expires_at = ""
            self.append_event(job.job_id, level="error", event="job_failed", error=str(error), failure_phase=failure_phase)

    def append_event(self, job_id: str, *, level: str = "info", event: str, **fields: Any) -> None:
        if not self._state_store:
            return
        self._state_store.append_event(job_id, level=level, event=event, **fields)

    def get_job_events(self, job_id: str, limit: int = 200) -> list[dict[str, Any]]:
        if not self._state_store:
            return []
        return self._state_store.read_events(job_id, limit=limit)

    def _ensure_store_exists(self) -> None:
        path = self.queue_path
        if path and not path.exists():
            atomic_json_write(path, [])

    def _read_jobs(self) -> list[RenderQueueJob]:
        path = self.queue_path
        if not path or not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []
        if not isinstance(data, list):
            return []
        return [RenderQueueJob.from_dict(x) for x in data if isinstance(x, dict)]

    def _write_jobs(self, jobs: list[RenderQueueJob]) -> None:
        path = self.queue_path
        if not path:
            return
        payloads = [j.to_dict() for j in jobs]
        if self._state_store:
            for payload in payloads:
                job_id = str(payload.get("job_id") or "")
                if not job_id:
                    continue
                payload["state_path"] = self._state_store.state_path(job_id).as_posix()
                payload["events_path"] = self._state_store.events_path(job_id).as_posix()
            self._state_store.sync_jobs(payloads)
        atomic_json_write(path, payloads)

    class _FileLock:
        def __init__(self, lock_path: Path, timeout_s: float = 5.0, poll_s: float = 0.05):
            self.lock_path = lock_path
            self.timeout_s = timeout_s
            self.poll_s = poll_s
            self.fd: int | None = None

        def __enter__(self) -> "RenderQueueManager._FileLock":
            deadline = time.monotonic() + self.timeout_s
            while True:
                try:
                    self.fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
                    try:
                        payload = json.dumps(
                            {
                                "pid": os.getpid(),
                                "created_at": utc_now_iso(),
                            }
                        ).encode("utf-8")
                        os.write(self.fd, payload)
                    except Exception:
                        pass
                    return self
                except FileExistsError:
                    self._reap_stale_lock_if_needed()
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"Queue lock timeout: {self.lock_path}")
                    time.sleep(self.poll_s)

        def _reap_stale_lock_if_needed(self) -> None:
            try:
                stat = self.lock_path.stat()
            except FileNotFoundError:
                return
            except Exception:
                return

            age_s = time.time() - float(stat.st_mtime)
            if age_s < max(2.0, float(self.timeout_s)):
                return

            try:
                self.lock_path.unlink(missing_ok=True)
            except Exception:
                return

        def __exit__(self, exc_type, exc, tb) -> None:
            try:
                if self.fd is not None:
                    os.close(self.fd)
            finally:
                try:
                    self.lock_path.unlink(missing_ok=True)
                except Exception:
                    pass

    @contextmanager
    def _mutate_jobs(self) -> Iterator[list[RenderQueueJob]]:
        lock = None
        if self.lock_path:
            lock = self._FileLock(self.lock_path)
            lock.__enter__()
        jobs = self._read_jobs()
        try:
            yield jobs
            self._write_jobs(jobs)
        finally:
            if lock:
                lock.__exit__(None, None, None)

    @staticmethod
    def _find_job(jobs: list[RenderQueueJob], job_id: str) -> RenderQueueJob | None:
        for job in jobs:
            if job.job_id == job_id:
                return job
        return None

    @staticmethod
    def _find_duplicate_job(
        jobs: list[RenderQueueJob],
        *,
        demo_id: str,
        clip_plan_id: str,
        render_mode: str,
        target_settings: dict[str, Any],
    ) -> RenderQueueJob | None:
        signature = json.dumps(target_settings or {}, sort_keys=True)
        for job in jobs:
            if job.status.is_terminal:
                continue
            if job.demo_id != demo_id:
                continue
            if job.clip_plan_id != clip_plan_id:
                continue
            if job.render_mode != render_mode:
                continue
            if json.dumps(job.target_settings or {}, sort_keys=True) != signature:
                continue
            return job
        return None

    @staticmethod
    def _reset_for_retry(job: RenderQueueJob, *, increment_retry: bool) -> None:
        if increment_retry:
            job.retry_count += 1
        job.status = QueueJobStatus.PENDING
        job.progress_stage = "queued_for_retry"
        job.updated_at = utc_now_iso()
        job.started_at = ""
        job.finished_at = ""
        job.claimed_at = ""
        job.heartbeat_at = ""
        job.lease_expires_at = ""
        job.worker_id = None
        job.worker_pid = None
        job.cancellation_requested = False
        job.clip_id = None
        job.clip_record = None
        job.artifact_dir = None
        job.artifact_metadata_path = None
        job.source_clip_plan_id = None
        job.queued_at = utc_now_iso()

    def _lease_expiry_iso(self) -> str:
        expiry = datetime.now(timezone.utc).timestamp() + float(self._lease_timeout_s)
        return datetime.fromtimestamp(expiry, tz=timezone.utc).isoformat()

    @staticmethod
    def _is_retryable(job: RenderQueueJob) -> bool:
        if not str(job.demo_snapshot_path or "").strip():
            return False
        if str(job.render_mode or "").strip() == RENDER_MODE_TACTICAL_2D:
            return False
        return True

    def _recover_stale_jobs(self, jobs: list[RenderQueueJob]) -> None:
        now_ts = datetime.now(timezone.utc).timestamp()
        for job in jobs:
            if job.status != QueueJobStatus.PROCESSING:
                continue
            hb = str(job.heartbeat_at or "")
            if not hb:
                continue
            try:
                heartbeat_ts = datetime.fromisoformat(hb).timestamp()
            except Exception:
                continue
            if now_ts - heartbeat_ts < self._lease_timeout_s:
                continue

            if job.retry_count < job.max_retries:
                job.warnings.append("stale_worker_recovered_for_retry")
                self.append_event(job.job_id, level="warning", event="stale_worker_recovered_for_retry")
                self._reset_for_retry(job, increment_retry=True)
                continue

            failure_time = utc_now_iso()
            job.status = QueueJobStatus.FAILED
            job.error = "Worker lease expired"
            job.failure_phase = "worker_timeout"
            job.progress_stage = "failed"
            job.finished_at = failure_time
            job.updated_at = failure_time
            job.lease_expires_at = ""
            self.append_event(job.job_id, level="error", event="worker_lease_expired")
