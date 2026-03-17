"""
render_pipeline.py
Production-oriented orchestration layer for render job execution.

Separates:
  1. job orchestration
  2. renderer invocation
  3. post-processing
  4. clip registration
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from src.render_queue import RenderQueueManager, RenderQueueJob
from src.render_service import (
    RenderServiceError,
    finalize_and_register_clip,
    find_clip_plan,
    find_highlight,
    load_demo_snapshot,
    render_job_artifact,
)

logger = logging.getLogger(__name__)


class RenderPipelineStageError(RenderServiceError):
    def __init__(self, stage: str, message: str):
        super().__init__(message)
        self.stage = stage


class RenderPipelineService:
    def __init__(
        self,
        *,
        queue: RenderQueueManager,
        worker_id: str,
        output_root: Path,
        logger_: logging.Logger | None = None,
    ):
        self.queue = queue
        self.worker_id = worker_id
        self.output_root = output_root
        self.logger = logger_ or logger

    def _event(self, job_id: str, event: str, *, level: str = "info", **fields: Any) -> None:
        self.queue.append_event(job_id, level=level, event=event, worker_id=self.worker_id, **fields)
        log_fn = getattr(self.logger, level, self.logger.info)
        log_fn("render_job event=%s job_id=%s %s", event, job_id, " ".join(f"{k}={fields[k]}" for k in sorted(fields)))

    def run(self, job: RenderQueueJob) -> dict[str, Any]:
        job_id = job.job_id
        self._event(job_id, "job_started", status=job.status.value, render_mode=job.render_mode, retry_count=job.retry_count)
        self.queue.touch_job(job_id, self.worker_id)

        snapshot = self._load_snapshot(job)
        self.queue.touch_job(job_id, self.worker_id)

        artifact, clip_plan, source_highlight = self._render_artifact(job, snapshot)
        self.queue.touch_job(job_id, self.worker_id)

        record = self._postprocess_and_register(job, artifact, clip_plan, source_highlight)
        self.queue.touch_job(job_id, self.worker_id)
        self._event(job_id, "job_completed", clip_id=record.get("clip_id"), status=record.get("status"))
        return record

    def _load_snapshot(self, job: RenderQueueJob) -> dict[str, Any]:
        self.queue.set_job_stage(job.job_id, self.worker_id, "loading_snapshot")
        snapshot_path = str(job.demo_snapshot_path or "").strip()
        self._event(job.job_id, "loading_snapshot", snapshot_path=snapshot_path)
        if not snapshot_path:
            raise RenderPipelineStageError("loading_snapshot", "Missing demo_snapshot_path on queue job")
        try:
            snapshot = load_demo_snapshot(snapshot_path)
        except RenderServiceError as exc:
            raise RenderPipelineStageError("loading_snapshot", str(exc)) from exc
        return snapshot

    def _render_artifact(
        self,
        job: RenderQueueJob,
        snapshot: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
        self.queue.set_job_stage(job.job_id, self.worker_id, "rendering")
        self._event(
            job.job_id,
            "rendering_started",
            demo_id=job.demo_id,
            clip_plan_id=job.clip_plan_id,
            render_mode=job.render_mode,
        )
        try:
            artifact, clip_plan, source_highlight = render_job_artifact(
                snapshot_payload=snapshot,
                demo_id=job.demo_id,
                clip_plan_id=job.clip_plan_id,
                render_mode=job.render_mode,
                target_settings=job.target_settings,
                output_root=self.output_root,
            )
        except RenderServiceError as exc:
            raise RenderPipelineStageError("rendering", str(exc)) from exc

        parsed = snapshot.get("parsed_data") if isinstance(snapshot.get("parsed_data"), dict) else {}
        if not clip_plan and isinstance(parsed, dict):
            clip_plan = find_clip_plan(parsed, job.clip_plan_id) or {}
        if not source_highlight and isinstance(parsed, dict):
            source_highlight = find_highlight(parsed, str((clip_plan or {}).get("source_highlight_id") or ""))

        artifact["queue_job_id"] = job.job_id
        artifact["queue_job"] = {
            "job_id": job.job_id,
            "queued_at": job.queued_at,
            "started_at": job.started_at,
            "render_mode": job.render_mode,
            "clip_plan_id": job.clip_plan_id,
            "worker_id": self.worker_id,
            "retry_count": job.retry_count,
            "state_path": job.state_path,
            "events_path": job.events_path,
        }
        self._event(
            job.job_id,
            "rendering_finished",
            artifact_status=artifact.get("status"),
            clip_id=artifact.get("clip_id"),
        )
        return artifact, clip_plan, source_highlight

    def _postprocess_and_register(
        self,
        job: RenderQueueJob,
        artifact: dict[str, Any],
        clip_plan: dict[str, Any],
        source_highlight: dict[str, Any] | None,
    ) -> dict[str, Any]:
        self.queue.set_job_stage(job.job_id, self.worker_id, "postprocess_register")
        self._event(job.job_id, "postprocess_register_started", artifact_status=artifact.get("status"))
        try:
            record = finalize_and_register_clip(
                output_root=self.output_root,
                demo_id=job.demo_id,
                artifact=artifact,
                clip_plan=clip_plan,
                source_highlight=source_highlight,
            )
        except RenderServiceError as exc:
            raise RenderPipelineStageError("postprocess_register", str(exc)) from exc
        self._event(
            job.job_id,
            "postprocess_register_finished",
            clip_id=record.get("clip_id"),
            clip_status=record.get("status"),
        )
        return record
