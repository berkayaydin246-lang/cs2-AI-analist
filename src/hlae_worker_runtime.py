"""
hlae_worker_runtime.py
Queue-facing wrapper for HLAE recipe execution.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.cs2_config import CS2Config
from src.hlae_renderer import HLAERenderer
from src.render_queue import RenderJob
from src.render_recipe import RenderRecipe
from src.render_result import RenderResult


@dataclass
class HLAEWorkerRuntime:
    config: CS2Config

    def build_recipe(self, job: RenderJob) -> RenderRecipe:
        return RenderRecipe.from_render_job(job, self.config)

    def render_job(self, job: RenderJob) -> tuple[RenderRecipe, RenderResult, dict[str, Any]]:
        recipe = self.build_recipe(job)
        renderer = HLAERenderer(config=self.config)
        result = renderer.render(recipe)
        artifact = self._build_artifact(job, recipe, result)
        artifact_path = Path(job.output_dir) / "artifact.json"
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        with open(artifact_path, "w", encoding="utf-8") as f:
            json.dump(artifact, f, indent=2)
        return recipe, result, artifact

    def _build_artifact(self, job: RenderJob, recipe: RenderRecipe, result: RenderResult) -> dict[str, Any]:
        diagnostics = result.diagnostics or {}
        finalize = diagnostics.get("finalize_result") or {}
        output_path = result.final_video_path if result.success else None
        thumbnail_path = finalize.get("thumbnail_path")
        generated_root = Path(self.config.render_output_root).resolve()

        def _generated_url(path_str: str | None) -> str | None:
            if not path_str:
                return None
            path = Path(path_str)
            if not path.exists():
                return None
            try:
                rel = path.resolve().relative_to(generated_root).as_posix()
            except ValueError:
                return None
            return f"/generated/{rel}"

        return {
            "artifact_schema_version": 3,
            "clip_id": job.clip_id,
            "job_id": job.job_id,
            "demo_id": job.demo_id,
            "status": "completed" if result.success else "failed",
            "render_backend": "hlae_mirv_streams",
            "player_name": recipe.player_name,
            "player_steamid64": recipe.player_steamid64,
            "event_type": recipe.metadata.get("event_type", ""),
            "round_number": recipe.round_number,
            "start_tick": recipe.start_tick,
            "anchor_tick": recipe.anchor_tick,
            "end_tick": recipe.end_tick,
            "score": recipe.metadata.get("score", 0),
            "priority": recipe.metadata.get("priority", 0),
            "pov_mode": recipe.camera_mode,
            "pov_player": recipe.player_name,
            "pov_steamid64": recipe.player_steamid64,
            "camera_mode": recipe.observer_mode,
            "final_video_path": output_path,
            "output_path": output_path,
            "output_url": _generated_url(output_path),
            "thumbnail_path": thumbnail_path,
            "thumbnail_url": _generated_url(thumbnail_path),
            "duration_s": finalize.get("duration_s", result.duration_seconds),
            "frame_count": finalize.get("frame_count", 0),
            "resolution": finalize.get("resolution", ""),
            "codec": finalize.get("codec", ""),
            "output_size_bytes": finalize.get("output_size_bytes", 0),
            "selection_reason": recipe.metadata.get("selection_reason", ""),
            "warnings": (diagnostics.get("warnings") or []) + job.warnings,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
            "log_path": result.log_path,
            "error_code": result.error_code,
            "error_message": result.error_message,
        }
