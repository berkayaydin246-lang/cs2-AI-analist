from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.render_service import RenderServiceError, finalize_and_register_clip


class RenderServiceFinalizeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.output_root = Path(self.tempdir.name)
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.meta_path = self.output_root / "artifact.json"
        self.artifact = {
            "status": "completed",
            "metadata_path": str(self.meta_path),
            "job": {"postprocess_settings": {}},
            "warnings": [],
        }
        self.clip_plan = {
            "clip_plan_id": "plan1",
            "round_number": 1,
            "start_tick": 10,
            "anchor_tick": 20,
            "end_tick": 30,
            "title": "Test Clip",
            "primary_player": "Player",
        }

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_finalize_aborts_when_postprocess_fails(self) -> None:
        with patch("src.render_service.normalize_artifact_media", return_value={"ok": False, "error": "bad media"}), patch(
            "src.render_service.register_clip"
        ) as mock_register:
            with self.assertRaises(RenderServiceError):
                finalize_and_register_clip(
                    output_root=self.output_root,
                    demo_id="demo1",
                    artifact=dict(self.artifact),
                    clip_plan=self.clip_plan,
                    source_highlight=None,
                )

        mock_register.assert_not_called()
        self.assertTrue(self.meta_path.is_file())

    def test_finalize_registers_only_after_successful_postprocess(self) -> None:
        clip_record = {
            "clip_id": "clip_1",
            "status": "completed",
            "clip_plan_id": "plan1",
            "integrity": {"has_video": True, "has_thumbnail": True},
        }
        artifact = dict(self.artifact)
        artifact["clip_id"] = "clip_1"

        with patch("src.render_service.normalize_artifact_media", return_value={"ok": True}), patch(
            "src.render_service.register_clip", return_value=clip_record
        ) as mock_register:
            record = finalize_and_register_clip(
                output_root=self.output_root,
                demo_id="demo1",
                artifact=artifact,
                clip_plan=self.clip_plan,
                source_highlight=None,
            )

        self.assertEqual(record["clip_id"], "clip_1")
        mock_register.assert_called_once()
        self.assertTrue(self.meta_path.is_file())


if __name__ == "__main__":
    unittest.main()
