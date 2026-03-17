from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.render_postprocess import MediaProbeInfo, normalize_artifact_media


class RenderPostprocessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_returns_clear_failure_when_ffmpeg_missing(self) -> None:
        raw_path = self.root / "clip.mp4"
        raw_path.write_bytes(b"x" * 2048)
        artifact = {"output_path": str(raw_path), "warnings": [], "job": {"postprocess_settings": {}}}

        with patch("src.render_postprocess._resolve_bin", return_value=None):
            result = normalize_artifact_media(artifact)

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "ffmpeg_not_available")
        self.assertEqual(artifact["postprocess"]["error"], "ffmpeg_not_available")

    def test_successful_postprocess_updates_artifact_metadata(self) -> None:
        raw_path = self.root / "clip.mp4"
        raw_path.write_bytes(b"x" * 40960)
        artifact = {
            "output_path": str(raw_path),
            "warnings": [],
            "job": {"postprocess_settings": {}},
            "artifacts": {
                "video": {"path": str(raw_path)},
                "thumbnail": {},
            },
        }

        def fake_run(cmd: list[str], timeout_s: int) -> subprocess.CompletedProcess:
            out_path = Path(cmd[-1])
            out_path.parent.mkdir(parents=True, exist_ok=True)
            if out_path.suffix.lower() == ".jpg":
                out_path.write_bytes(b"thumb")
            else:
                out_path.write_bytes(b"y" * 50000)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        media = MediaProbeInfo(
            duration_s=2.5,
            frame_count=150,
            width=1920,
            height=1080,
            resolution="1920x1080",
            bit_rate=1000000,
            video_codec="h264",
            audio_codec="aac",
            avg_frame_rate=60.0,
            file_size_bytes=50000,
        )

        with patch("src.render_postprocess._resolve_bin", side_effect=lambda value: value), patch(
            "src.render_postprocess._run", side_effect=fake_run
        ), patch("src.render_postprocess._probe_video", return_value=media):
            result = normalize_artifact_media(artifact)

        self.assertTrue(result["ok"])
        self.assertEqual(artifact["video_codec"], "h264")
        self.assertEqual(artifact["resolution"], "1920x1080")
        self.assertTrue(Path(artifact["thumbnail_path"]).is_file())


if __name__ == "__main__":
    unittest.main()
