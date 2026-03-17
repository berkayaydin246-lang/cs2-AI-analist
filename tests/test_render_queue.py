from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.render_queue import QueueJobStatus, RenderQueueManager


class RenderQueueManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.queue_dir = Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _manager(self, *, max_retries: int = 1, lease_timeout_s: int = 30) -> RenderQueueManager:
        return RenderQueueManager(
            persist_dir=self.queue_dir,
            max_retries=max_retries,
            lease_timeout_s=lease_timeout_s,
        )

    def test_enqueue_claim_complete_persists_state(self) -> None:
        manager = self._manager()
        job = manager.enqueue("demo1", "plan1", target_settings={"timeout_s": 120})
        claimed = manager.claim_next_job("worker-a", worker_pid=1234)

        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.job_id, job.job_id)
        self.assertEqual(claimed.status, QueueJobStatus.PROCESSING)

        manager.complete_job(
            job.job_id,
            "worker-a",
            {
                "clip_id": "clip_1",
                "status": "completed",
                "asset_dir": str(self.queue_dir / "clip_1"),
                "artifacts": {
                    "base_dir": str(self.queue_dir / "clip_1"),
                    "metadata": {"path": str(self.queue_dir / "clip_1" / "artifact.json")},
                },
            },
        )

        stored = manager.get_job(job.job_id)
        self.assertIsNotNone(stored)
        self.assertEqual(stored.status, QueueJobStatus.COMPLETED)
        self.assertEqual(stored.clip_id, "clip_1")
        self.assertTrue(Path(stored.state_path).is_file())
        self.assertTrue(Path(stored.events_path).is_file())

    def test_fail_job_retries_then_terminal_failure(self) -> None:
        manager = self._manager(max_retries=1)
        job = manager.enqueue("demo1", "plan1")
        claimed = manager.claim_next_job("worker-a")
        self.assertIsNotNone(claimed)

        manager.fail_job(job.job_id, "worker-a", "first failure", failure_phase="rendering")
        retried = manager.get_job(job.job_id)
        self.assertIsNotNone(retried)
        self.assertEqual(retried.status, QueueJobStatus.PENDING)
        self.assertEqual(retried.retry_count, 1)

        claimed_again = manager.claim_next_job("worker-a")
        self.assertIsNotNone(claimed_again)
        manager.fail_job(job.job_id, "worker-a", "second failure", failure_phase="rendering")

        failed = manager.get_job(job.job_id)
        self.assertIsNotNone(failed)
        self.assertEqual(failed.status, QueueJobStatus.FAILED)
        self.assertEqual(failed.failure_phase, "rendering")
        self.assertEqual(failed.error, "second failure")

    def test_claim_next_job_recovers_stale_processing_job(self) -> None:
        manager = self._manager(max_retries=2, lease_timeout_s=30)
        job = manager.enqueue("demo1", "plan1")
        claimed = manager.claim_next_job("worker-a")
        self.assertIsNotNone(claimed)

        stale_heartbeat = (datetime.now(timezone.utc) - timedelta(seconds=90)).isoformat()
        with manager._mutate_jobs() as jobs:  # intentional private access for state injection
            jobs[0].heartbeat_at = stale_heartbeat
            jobs[0].updated_at = stale_heartbeat

        recovered = manager.claim_next_job("worker-b")
        self.assertIsNotNone(recovered)
        self.assertEqual(recovered.job_id, job.job_id)
        self.assertEqual(recovered.worker_id, "worker-b")
        self.assertEqual(recovered.retry_count, 1)
        self.assertEqual(recovered.status, QueueJobStatus.PROCESSING)


if __name__ == "__main__":
    unittest.main()
