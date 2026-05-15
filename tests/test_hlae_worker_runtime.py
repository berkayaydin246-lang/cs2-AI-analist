"""Tests for hlae_worker_runtime.py."""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.cs2_config import load_config
from src.hlae_worker_runtime import HLAEWorkerRuntime
from src.render_queue import RenderJob
from src.render_result import RenderResult


def _make_job(tmp_path):
    demo = tmp_path / "test.dem"
    demo.write_text("x")
    out_dir = tmp_path / "out"
    return RenderJob(
        job_id="rj_test",
        clip_id="cp_test",
        demo_id="demo1",
        demo_path=str(demo),
        clip_plan={
            "clip_plan_id": "cp_test",
            "highlight_id": "hl_test",
            "player_name": "alice",
            "player_steamid64": "76561198356633543",
            "round_number": 5,
            "start_tick": 9000,
            "anchor_tick": 10000,
            "end_tick": 11000,
            "round_start_tick": 8000,
            "round_end_tick": 15000,
            "freeze_end_tick": 8500,
            "lead_in_ticks": 128,
            "tail_ticks": 96,
            "pov_mode": "player_pov",
            "camera_mode": "first_person",
            "event_type": "opening_kill",
            "selection_reason": "test clip",
        },
        player_name="alice",
        player_steamid64="76561198356633543",
        round_number=5,
        start_tick=9000,
        anchor_tick=10000,
        end_tick=11000,
        round_start_tick=8000,
        round_end_tick=15000,
        freeze_end_tick=8500,
        output_dir=str(out_dir),
    )


def test_hlae_worker_runtime_builds_artifact_from_successful_render(tmp_path):
    config = load_config()
    runtime = HLAEWorkerRuntime(config=config)
    job = _make_job(tmp_path)

    fake_result = RenderResult.start(job.job_id).mark_completed(
        final_video_path=str(Path(job.output_dir) / "clip.mp4"),
        raw_output_path=str(Path(job.output_dir) / "raw" / "frames"),
        thumbnail_path=str(Path(job.output_dir) / "thumbnail.jpg"),
        diagnostics={
            "finalize_result": {
                "duration_s": 12.5,
                "frame_count": 750,
                "resolution": "1920x1080",
                "codec": "h264",
                "output_size_bytes": 1000000,
                "thumbnail_path": str(Path(job.output_dir) / "thumbnail.jpg"),
            }
        },
    )

    with patch("src.hlae_worker_runtime.HLAERenderer") as mock_renderer_cls:
        mock_renderer = mock_renderer_cls.return_value
        mock_renderer.render.return_value = fake_result
        recipe, result, artifact = runtime.render_job(job)

    assert result.success is True
    assert artifact["render_backend"] == "hlae_mirv_streams"
    assert artifact["final_video_path"].endswith("clip.mp4")
    assert Path(job.output_dir, "artifact.json").exists()
