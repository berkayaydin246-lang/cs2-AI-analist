"""Tests for render_queue.py — job management."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from src.render_queue import RenderQueue, RenderJob


def _make_clip_plan(clip_id="cp_test_001", player="alice", steamid="76561198356633543"):
    return {
        "clip_plan_id": clip_id,
        "player_name": player,
        "player_steamid64": steamid,
        "round_number": 5,
        "start_tick": 9000,
        "anchor_tick": 10000,
        "end_tick": 11000,
        "round_start_tick": 8000,
        "round_end_tick": 15000,
        "freeze_end_tick": 8500,
        "duration_s": 10.5,
        "pov_mode": "player_pov",
        "camera_mode": "first_person",
        "event_type": "opening_kill",
        "score": 25.0,
        "priority": 1,
        "confidence": 0.9,
        "selection_reason": "test clip",
    }


@pytest.fixture
def queue(tmp_path):
    return RenderQueue(queue_dir=str(tmp_path / "queue"))


class TestRenderJobCreation:
    def test_create_job(self, queue):
        plan = _make_clip_plan()
        job = queue.create_job(plan, demo_id="demo1", demo_path="/tmp/test.dem")
        assert job.job_id.startswith("rj_")
        assert job.status == "queued"
        assert job.demo_id == "demo1"
        assert job.player_name == "alice"
        assert job.player_steamid64 == "76561198356633543"
        assert job.start_tick == 9000
        assert job.anchor_tick == 10000
        assert job.end_tick == 11000

    def test_job_persisted_to_disk(self, queue):
        plan = _make_clip_plan()
        job = queue.create_job(plan, demo_id="demo1", demo_path="/tmp/test.dem")
        # Load from disk
        loaded = queue.get_job(job.job_id)
        assert loaded is not None
        assert loaded.job_id == job.job_id
        assert loaded.player_name == "alice"
        assert loaded.player_steamid64 == "76561198356633543"

    def test_events_logged(self, queue):
        plan = _make_clip_plan()
        job = queue.create_job(plan, demo_id="demo1", demo_path="/tmp/test.dem")
        events_file = queue._events_file(job.job_id)
        assert events_file.exists()
        content = events_file.read_text()
        assert "job_created" in content


class TestRenderJobLifecycle:
    def test_claim_job(self, queue):
        plan = _make_clip_plan()
        job = queue.create_job(plan, demo_id="demo1", demo_path="/tmp/test.dem")

        claimed = queue.claim_job("worker_1")
        assert claimed is not None
        assert claimed.job_id == job.job_id
        assert claimed.status == "claimed"
        assert claimed.worker_id == "worker_1"

    def test_no_jobs_to_claim(self, queue):
        claimed = queue.claim_job("worker_1")
        assert claimed is None

    def test_start_job(self, queue):
        plan = _make_clip_plan()
        job = queue.create_job(plan, demo_id="demo1", demo_path="/tmp/test.dem")
        queue.claim_job("worker_1")
        started = queue.start_job(job.job_id)
        assert started is not None
        assert started.status == "processing"

    def test_complete_job(self, queue):
        plan = _make_clip_plan()
        job = queue.create_job(plan, demo_id="demo1", demo_path="/tmp/test.dem")
        queue.claim_job("worker_1")
        queue.start_job(job.job_id)

        completed = queue.complete_job(
            job.job_id,
            capture_result={"status": "completed"},
            postprocess_result={"status": "completed"},
            artifact={"clip_id": "test"},
        )
        assert completed is not None
        assert completed.status == "completed"
        assert completed.artifact == {"clip_id": "test"}

    def test_fail_job_with_retry(self, queue):
        plan = _make_clip_plan()
        job = queue.create_job(plan, demo_id="demo1", demo_path="/tmp/test.dem", max_retries=2)
        queue.claim_job("worker_1")
        queue.start_job(job.job_id)

        failed = queue.fail_job(job.job_id, "test error")
        assert failed is not None
        assert failed.status == "queued"  # back in queue for retry
        assert failed.retry_count == 1

    def test_fail_job_permanently(self, queue):
        plan = _make_clip_plan()
        job = queue.create_job(plan, demo_id="demo1", demo_path="/tmp/test.dem", max_retries=0)
        queue.claim_job("worker_1")
        queue.start_job(job.job_id)

        failed = queue.fail_job(job.job_id, "test error")
        assert failed is not None
        assert failed.status == "failed"
        assert failed.error == "test error"


class TestRenderJobIdentity:
    def test_steamid64_required_in_job(self, queue):
        plan = _make_clip_plan(steamid="76561198388732174")
        job = queue.create_job(plan, demo_id="demo1", demo_path="/tmp/test.dem")
        assert job.player_steamid64 == "76561198388732174"

    def test_identity_preserved_through_lifecycle(self, queue):
        plan = _make_clip_plan(player="VixToix037", steamid="76561198388732174")
        job = queue.create_job(plan, demo_id="demo1", demo_path="/tmp/test.dem")
        queue.claim_job("worker_1")
        queue.start_job(job.job_id)
        completed = queue.complete_job(job.job_id)
        assert completed.player_name == "VixToix037"
        assert completed.player_steamid64 == "76561198388732174"


class TestRenderQueueQuery:
    def test_list_jobs_by_demo(self, queue):
        plan1 = _make_clip_plan(clip_id="cp1")
        plan2 = _make_clip_plan(clip_id="cp2")
        queue.create_job(plan1, demo_id="demo1", demo_path="/tmp/1.dem")
        queue.create_job(plan2, demo_id="demo2", demo_path="/tmp/2.dem")

        demo1_jobs = queue.list_jobs(demo_id="demo1")
        assert len(demo1_jobs) == 1
        assert demo1_jobs[0].demo_id == "demo1"

    def test_list_jobs_by_status(self, queue):
        plan1 = _make_clip_plan(clip_id="cp1")
        plan2 = _make_clip_plan(clip_id="cp2")
        queue.create_job(plan1, demo_id="demo1", demo_path="/tmp/1.dem")
        job2 = queue.create_job(plan2, demo_id="demo1", demo_path="/tmp/1.dem")
        queue.claim_job("worker_1")

        queued = queue.list_jobs(status="queued")
        assert len(queued) == 1

    def test_queue_status(self, queue):
        plan1 = _make_clip_plan(clip_id="cp1")
        plan2 = _make_clip_plan(clip_id="cp2")
        queue.create_job(plan1, demo_id="demo1", demo_path="/tmp/1.dem")
        queue.create_job(plan2, demo_id="demo1", demo_path="/tmp/1.dem")

        status = queue.get_queue_status(demo_id="demo1")
        assert status["total"] == 2
        assert status["queued"] == 2

    def test_clear_all_jobs(self, queue):
        plan1 = _make_clip_plan(clip_id="cp1")
        plan2 = _make_clip_plan(clip_id="cp2")
        queue.create_job(plan1, demo_id="demo1", demo_path="/tmp/1.dem")
        queue.create_job(plan2, demo_id="demo1", demo_path="/tmp/1.dem")

        removed = queue.clear_all_jobs()
        assert removed == 2
        assert queue.list_jobs() == []


class TestRenderJobSerialization:
    def test_to_dict(self):
        job = RenderJob(
            job_id="rj_test",
            clip_id="cp_test",
            demo_id="demo1",
            demo_path="/tmp/test.dem",
            player_name="alice",
            player_steamid64="76561198356633543",
        )
        d = job.to_dict()
        assert d["job_id"] == "rj_test"
        assert d["player_steamid64"] == "76561198356633543"

    def test_from_dict_roundtrip(self):
        job = RenderJob(
            job_id="rj_test",
            clip_id="cp_test",
            demo_id="demo1",
            demo_path="/tmp/test.dem",
            player_name="alice",
            player_steamid64="76561198356633543",
            start_tick=9000,
            anchor_tick=10000,
            end_tick=11000,
        )
        d = job.to_dict()
        restored = RenderJob.from_dict(d)
        assert restored.job_id == job.job_id
        assert restored.player_steamid64 == job.player_steamid64
        assert restored.start_tick == job.start_tick
