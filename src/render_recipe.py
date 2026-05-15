"""
render_recipe.py
Stable contract between clip planning and HLAE render execution.
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from src.cs2_config import CS2Config
from src.render_queue import RenderJob

DEFAULT_TICK_RATE = 64


class RenderRecipeError(Exception):
    """Raised when a render recipe cannot be built or validated."""


@dataclass
class RenderRecipe:
    job_id: str
    demo_path: str
    output_dir: str
    output_name: str
    player_name: str
    player_steamid64: str = ""
    start_tick: int = 0
    end_tick: int = 0
    anchor_tick: int = 0
    pre_roll_ticks: int = 0
    post_roll_ticks: int = 0
    tick_rate: int = DEFAULT_TICK_RATE
    camera_mode: str = "player_pov"
    observer_mode: str = "first_person"
    fps: int = 60
    width: int = 1920
    height: int = 1080
    encode_preset: str = "veryfast"
    video_container: str = "mp4"
    round_number: int = 0
    round_start_tick: int = 0
    round_end_tick: int = 0
    freeze_end_tick: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def seek_tick(self) -> int:
        return max(0, self.start_tick - self.pre_roll_ticks)

    @property
    def stop_tick(self) -> int:
        return max(self.seek_tick, self.end_tick + self.post_roll_ticks)

    @property
    def render_duration_seconds(self) -> float:
        return max(0.0, (self.stop_tick - self.seek_tick) / max(self.tick_rate, 1))

    @property
    def raw_name_prefix(self) -> str:
        safe = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in self.output_name).strip("_")
        return safe or self.job_id

    def validate(self) -> None:
        if not self.job_id:
            raise RenderRecipeError("job_id is required")
        if not self.demo_path or not Path(self.demo_path).exists():
            raise RenderRecipeError(f"demo_path not found: {self.demo_path}")
        if not self.output_dir:
            raise RenderRecipeError("output_dir is required")
        if not self.output_name:
            raise RenderRecipeError("output_name is required")
        if not self.player_name:
            raise RenderRecipeError("player_name is required for MVP player_pov rendering")
        if self.start_tick <= 0:
            raise RenderRecipeError(f"start_tick must be > 0, got {self.start_tick}")
        if self.end_tick <= self.start_tick:
            raise RenderRecipeError(
                f"end_tick must be > start_tick, got start={self.start_tick} end={self.end_tick}"
            )
        if self.fps <= 0:
            raise RenderRecipeError(f"fps must be > 0, got {self.fps}")
        if self.width <= 0 or self.height <= 0:
            raise RenderRecipeError(f"invalid output size: {self.width}x{self.height}")
        if self.tick_rate <= 0:
            raise RenderRecipeError(f"tick_rate must be > 0, got {self.tick_rate}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_clip_plan(
        cls,
        clip_plan: dict[str, Any],
        *,
        job_id: str,
        demo_path: str,
        output_dir: str,
        config: CS2Config,
    ) -> "RenderRecipe":
        output_name = str(clip_plan.get("output_name") or clip_plan.get("clip_plan_id") or job_id)
        recipe = cls(
            job_id=job_id,
            demo_path=demo_path,
            output_dir=output_dir,
            output_name=output_name,
            player_name=str(clip_plan.get("player_name") or ""),
            player_steamid64=str(clip_plan.get("player_steamid64") or ""),
            start_tick=int(clip_plan.get("start_tick") or 0),
            end_tick=int(clip_plan.get("end_tick") or 0),
            anchor_tick=int(clip_plan.get("anchor_tick") or 0),
            pre_roll_ticks=int(clip_plan.get("lead_in_ticks") or config.hlae_render_pre_roll_ticks),
            post_roll_ticks=int(clip_plan.get("tail_ticks") or config.hlae_render_post_roll_ticks),
            tick_rate=int(clip_plan.get("tick_rate") or DEFAULT_TICK_RATE),
            camera_mode=str(clip_plan.get("pov_mode") or "player_pov"),
            observer_mode=str(clip_plan.get("camera_mode") or "first_person"),
            fps=int(clip_plan.get("fps") or config.hlae_render_fps),
            width=int(clip_plan.get("width") or config.hlae_render_width),
            height=int(clip_plan.get("height") or config.hlae_render_height),
            encode_preset=str(clip_plan.get("encode_preset") or config.postprocess_preset),
            video_container=str(clip_plan.get("video_container") or "mp4"),
            round_number=int(clip_plan.get("round_number") or 0),
            round_start_tick=int(clip_plan.get("round_start_tick") or 0),
            round_end_tick=int(clip_plan.get("round_end_tick") or 0),
            freeze_end_tick=int(clip_plan.get("freeze_end_tick") or 0),
            metadata={
                "clip_plan_id": clip_plan.get("clip_plan_id"),
                "highlight_id": clip_plan.get("highlight_id"),
                "event_type": clip_plan.get("event_type"),
                "score": clip_plan.get("score"),
                "priority": clip_plan.get("priority"),
                "confidence": clip_plan.get("confidence"),
                "selection_reason": clip_plan.get("selection_reason"),
            },
        )
        recipe.validate()
        return recipe

    @classmethod
    def from_render_job(cls, job: RenderJob, config: CS2Config) -> "RenderRecipe":
        recipe = cls.from_clip_plan(
            job.clip_plan,
            job_id=job.job_id,
            demo_path=job.demo_path,
            output_dir=job.output_dir,
            config=config,
        )
        if job.output_name:
            recipe.output_name = job.output_name
        if job.fps > 0:
            recipe.fps = job.fps
        if job.width > 0:
            recipe.width = job.width
        if job.height > 0:
            recipe.height = job.height
        if job.video_container:
            recipe.video_container = job.video_container
        if job.encode_preset:
            recipe.encode_preset = job.encode_preset
        recipe.validate()
        return recipe
