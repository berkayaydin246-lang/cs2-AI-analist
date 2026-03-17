"""
render_worker.py
Dedicated worker entrypoint for the async render queue.

Heavy rendering lives here, not in HTTP handlers.
Jobs are executed in an isolated subprocess so timeout and cancellation
can be enforced without leaving the worker thread blocked.
"""

from __future__ import annotations

import argparse
import logging
import multiprocessing as mp
import os
import queue
import sys
import time
import traceback
import uuid
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from src.render_pipeline import RenderPipelineService, RenderPipelineStageError
from src.render_queue import RenderQueueJob, RenderQueueManager

logger = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parent.parent


def _configure_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


def _pipeline_subprocess_main(
    result_queue: Any,
    *,
    job_payload: dict[str, Any],
    queue_dir: str,
    output_root: str,
    worker_id: str,
    max_retries: int,
    lease_timeout_s: int,
) -> None:
    try:
        load_dotenv(dotenv_path=BASE_DIR / ".env", override=False)
        queue_manager = RenderQueueManager(
            persist_dir=Path(queue_dir),
            max_retries=max_retries,
            lease_timeout_s=lease_timeout_s,
        )
        job = RenderQueueJob.from_dict(job_payload)
        pipeline = RenderPipelineService(
            queue=queue_manager,
            worker_id=worker_id,
            output_root=Path(output_root),
        )
        record = pipeline.run(job)
        result_queue.put({"ok": True, "record": record})
    except RenderPipelineStageError as exc:
        result_queue.put(
            {
                "ok": False,
                "stage": exc.stage,
                "error": str(exc),
                "error_type": exc.__class__.__name__,
            }
        )
    except Exception as exc:  # pragma: no cover - guarded by integration tests if hit
        result_queue.put(
            {
                "ok": False,
                "stage": "execution",
                "error": str(exc),
                "error_type": exc.__class__.__name__,
                "traceback": traceback.format_exc(),
            }
        )


class RenderWorker:
    def __init__(
        self,
        *,
        output_root: Path,
        queue_dir: Path,
        poll_interval_s: float = 1.0,
        max_retries: int = 1,
        lease_timeout_s: int = 120,
        worker_id: str | None = None,
    ) -> None:
        self.output_root = Path(output_root)
        self.queue_dir = Path(queue_dir)
        self.poll_interval_s = max(0.1, float(poll_interval_s))
        self.max_retries = max(0, int(max_retries))
        self.lease_timeout_s = max(30, int(lease_timeout_s))
        self.worker_id = worker_id or f"worker_{uuid.uuid4().hex[:8]}"
        self.queue = RenderQueueManager(
            persist_dir=self.queue_dir,
            max_retries=self.max_retries,
            lease_timeout_s=self.lease_timeout_s,
        )

    def run_forever(self, *, once: bool = False) -> int:
        logger.info(
            "render_worker starting worker_id=%s output_root=%s queue_dir=%s",
            self.worker_id,
            self.output_root,
            self.queue_dir,
        )
        processed = 0
        while True:
            job = self.queue.claim_next_job(self.worker_id, worker_pid=os.getpid())
            if job is None:
                if once:
                    logger.info("render_worker no pending jobs; exiting due to --once")
                    return 0
                time.sleep(self.poll_interval_s)
                continue

            processed += 1
            logger.info(
                "render_worker claimed job_id=%s clip_plan_id=%s mode=%s retry=%s",
                job.job_id,
                job.clip_plan_id,
                job.render_mode,
                job.retry_count,
            )
            self._execute_claimed_job(job)
            if once:
                logger.info("render_worker processed one job; exiting due to --once")
                return 0

    def _execute_claimed_job(self, job: RenderQueueJob) -> None:
        ctx = mp.get_context("spawn")
        result_queue: Any = ctx.Queue()
        process = ctx.Process(
            target=_pipeline_subprocess_main,
            kwargs={
                "result_queue": result_queue,
                "job_payload": job.to_dict(),
                "queue_dir": str(self.queue_dir),
                "output_root": str(self.output_root),
                "worker_id": self.worker_id,
                "max_retries": self.max_retries,
                "lease_timeout_s": self.lease_timeout_s,
            },
            daemon=False,
        )
        process.start()
        self.queue.append_event(
            job.job_id,
            event="worker_subprocess_started",
            worker_id=self.worker_id,
            worker_pid=process.pid,
        )

        deadline = time.monotonic() + float(job.timeout_s)
        while process.is_alive():
            current = self.queue.get_job(job.job_id)
            if current is None:
                logger.warning("render_worker job disappeared from queue while running: %s", job.job_id)
                self._terminate_process(process)
                return

            if current.cancellation_requested:
                logger.warning("render_worker cancelling job_id=%s", job.job_id)
                self.queue.append_event(job.job_id, level="warning", event="worker_cancellation_enforced")
                self._terminate_process(process)
                self.queue.fail_job(
                    job.job_id,
                    self.worker_id,
                    "Render job cancelled by operator",
                    failure_phase="cancelled",
                )
                return

            if time.monotonic() >= deadline:
                logger.error("render_worker timeout job_id=%s timeout_s=%s", job.job_id, job.timeout_s)
                self.queue.append_event(
                    job.job_id,
                    level="error",
                    event="worker_timeout_enforced",
                    timeout_s=job.timeout_s,
                )
                self._terminate_process(process)
                self.queue.fail_job(
                    job.job_id,
                    self.worker_id,
                    f"Render job exceeded timeout ({job.timeout_s}s)",
                    failure_phase="timeout",
                )
                return

            self.queue.touch_job(job.job_id, self.worker_id)
            time.sleep(self.poll_interval_s)

        process.join(timeout=1.0)
        self.queue.touch_job(job.job_id, self.worker_id)

        result_payload = self._drain_result_queue(result_queue)
        if process.exitcode not in (0, None) and not result_payload:
            self.queue.fail_job(
                job.job_id,
                self.worker_id,
                f"Render subprocess exited unexpectedly with code {process.exitcode}",
                failure_phase="worker_crash",
            )
            return

        if not result_payload:
            self.queue.fail_job(
                job.job_id,
                self.worker_id,
                "Render subprocess finished without returning a result",
                failure_phase="worker_protocol",
            )
            return

        if bool(result_payload.get("ok")):
            record = result_payload.get("record")
            if not isinstance(record, dict):
                self.queue.fail_job(
                    job.job_id,
                    self.worker_id,
                    "Render subprocess returned an invalid clip record",
                    failure_phase="worker_protocol",
                )
                return
            self.queue.complete_job(job.job_id, self.worker_id, record)
            return

        error = str(result_payload.get("error") or "render execution failed")
        stage = str(result_payload.get("stage") or "execution")
        self.queue.fail_job(job.job_id, self.worker_id, error, failure_phase=stage)

    @staticmethod
    def _terminate_process(process: mp.Process) -> None:
        if not process.is_alive():
            return
        process.terminate()
        process.join(timeout=5.0)
        if process.is_alive():
            process.kill()
            process.join(timeout=2.0)

    @staticmethod
    def _drain_result_queue(result_queue: Any) -> dict[str, Any] | None:
        latest: dict[str, Any] | None = None
        while True:
            try:
                item = result_queue.get_nowait()
            except queue.Empty:
                break
            if isinstance(item, dict):
                latest = item
        return latest


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the CS2 Coach render worker.")
    parser.add_argument("--output-root", default="outputs/generated", help="Generated artifact root directory")
    parser.add_argument("--queue-dir", default="outputs/generated/queue", help="Persistent render queue directory")
    parser.add_argument("--poll-interval", type=float, default=1.0, help="Polling interval in seconds")
    parser.add_argument("--max-retries", type=int, default=int(os.getenv("RENDER_JOB_MAX_RETRIES", "1")))
    parser.add_argument("--lease-timeout", type=int, default=int(os.getenv("RENDER_JOB_LEASE_TIMEOUT_S", "120")))
    parser.add_argument("--worker-id", default=os.getenv("RENDER_WORKER_ID") or "")
    parser.add_argument("--once", action="store_true", help="Process at most one job and exit")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    load_dotenv(dotenv_path=BASE_DIR / ".env", override=False)
    args = _parse_args(argv or sys.argv[1:])
    _configure_logging(args.verbose)
    worker = RenderWorker(
        output_root=Path(args.output_root),
        queue_dir=Path(args.queue_dir),
        poll_interval_s=args.poll_interval,
        max_retries=args.max_retries,
        lease_timeout_s=args.lease_timeout,
        worker_id=args.worker_id or None,
    )
    return worker.run_forever(once=args.once)


if __name__ == "__main__":
    raise SystemExit(main())
