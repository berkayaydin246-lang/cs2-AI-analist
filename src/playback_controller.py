"""
playback_controller.py
HLAE-oriented wrapper over the existing CS2 playback preparation layer.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.cs2_config import CS2Config
from src.cs2_controller import CS2Controller
from src.cs2_playback import PlaybackController, PlaybackError, PlaybackRequest, PlaybackResult
from src.render_recipe import RenderRecipe


@dataclass
class HLAEPlaybackController:
    config: CS2Config
    cs2: CS2Controller

    def __post_init__(self):
        self._delegate = PlaybackController(config=self.config, cs2=self.cs2)

    def prepare_recipe(self, recipe: RenderRecipe) -> PlaybackResult:
        request = PlaybackRequest(
            demo_path=recipe.demo_path,
            round_number=recipe.round_number,
            start_tick=recipe.seek_tick,
            anchor_tick=recipe.anchor_tick or recipe.start_tick,
            end_tick=recipe.stop_tick,
            round_start_tick=recipe.round_start_tick,
            round_end_tick=recipe.round_end_tick,
            freeze_end_tick=recipe.freeze_end_tick,
        )
        return self._delegate.prepare(request)

    def resume(self, duration_s: float) -> None:
        self._delegate.resume_and_play(duration_s)

    def stop(self) -> None:
        self._delegate.stop_playback()

    def cleanup(self) -> None:
        self._delegate.cleanup()


__all__ = [
    "HLAEPlaybackController",
    "PlaybackController",
    "PlaybackRequest",
    "PlaybackResult",
    "PlaybackError",
]
