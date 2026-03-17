from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.game_control import CS2GameControlService, build_game_control_service
from src.hlae_adapter import HLAEConfigurationError, HLAEGameControlService


class DummyController:
    pass


class GameControlFactoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.cs2_exe = self.root / "cs2.exe"
        self.cs2_exe.write_text("", encoding="utf-8")
        self.hlae_exe = self.root / "hlae.exe"
        self.hlae_exe.write_text("", encoding="utf-8")
        self.hlae_cfg = self.root / "hlae_cfg"
        self.hlae_cfg.mkdir()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_builds_plain_backend_by_default(self) -> None:
        service = build_game_control_service(
            config={"cs2_exe": str(self.cs2_exe)},
            controller=DummyController(),
        )
        self.assertIsInstance(service, CS2GameControlService)

    def test_builds_hlae_backend_when_configured(self) -> None:
        service = build_game_control_service(
            config={
                "cs2_exe": str(self.cs2_exe),
                "control_backend": "hlae",
                "hlae_exe": str(self.hlae_exe),
                "hlae_launch_template": "{hlae_exe} --cs2 {cs2_exe}",
                "hlae_config_dir": str(self.hlae_cfg),
            },
            controller=DummyController(),
        )
        self.assertIsInstance(service, HLAEGameControlService)

    def test_hlae_backend_requires_config(self) -> None:
        with self.assertRaises(HLAEConfigurationError):
            build_game_control_service(
                config={
                    "cs2_exe": str(self.cs2_exe),
                    "control_backend": "hlae",
                },
                controller=DummyController(),
            )


if __name__ == "__main__":
    unittest.main()
