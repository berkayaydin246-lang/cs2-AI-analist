"""
render_service.py
New render service layer used by worker processes.

Responsibilities:
- Resolve clip plan + highlight from snapshot payload
- Execute rendering via render dispatcher
- Post-process media artifacts using ffmpeg
- Register final clip metadata and validate integrity
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from src.clip_renderer import render_clip_plan
from src.clip_store import register_clip
from src.render_postprocess import normalize_artifact_media
from src.utils import atomic_json_write

logger = logging.getLogger(__name__)


class RenderServiceError(RuntimeError):
    pass


def _persist_artifact_metadata(artifact: dict[str, Any]) -> None:
    meta_path_raw = str(artifact.get("metadata_path") or "").strip()
    if not meta_path_raw:
        return
    meta_path = Path(meta_path_raw)
    try:
        atomic_json_write(meta_path, artifact)
    except Exception as exc:
        raise RenderServiceError(f"Failed to persist artifact metadata: {exc}") from exc


def load_demo_snapshot(snapshot_path: str | Path) -> dict[str, Any]:
    path = Path(snapshot_path)
    if not path.is_file():
        raise RenderServiceError(f"Demo snapshot not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RenderServiceError(f"Snapshot read failed: {exc}") from exc
    if not isinstance(payload, dict):
        raise RenderServiceError("Snapshot payload is not a JSON object")
    return payload


def find_clip_plan(parsed_data: dict[str, Any], clip_plan_id: str) -> dict[str, Any] | None:
    for plan in parsed_data.get("clip_plans", []) or []:
        if str(plan.get("clip_plan_id") or "") == str(clip_plan_id):
            return plan
    return None


def find_highlight(parsed_data: dict[str, Any], source_highlight_id: str) -> dict[str, Any] | None:
    if not source_highlight_id:
        return None
    for item in parsed_data.get("highlights", []) or []:
        if str(item.get("highlight_id") or "") == str(source_highlight_id):
            return item
    return None


def render_job_artifact(
    *,
    snapshot_payload: dict[str, Any],
    demo_id: str,
    clip_plan_id: str,
    render_mode: str,
    target_settings: dict[str, Any],
    output_root: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    parsed = snapshot_payload.get("parsed_data")
    if not isinstance(parsed, dict):
        raise RenderServiceError("Snapshot missing parsed_data")

    clip_plan = find_clip_plan(parsed, clip_plan_id)
    if not clip_plan:
        raise RenderServiceError(f"Clip plan not found in snapshot: {clip_plan_id}")

    source_highlight_id = str(clip_plan.get("source_highlight_id") or "")
    source_highlight = find_highlight(parsed, source_highlight_id)

    demo_path = str(snapshot_payload.get("demo_path") or "").strip()
    if demo_path:
        parsed["demo_path"] = demo_path

    artifact = render_clip_plan(
        parsed_data=parsed,
        demo_id=demo_id,
        clip_plan=clip_plan,
        output_root=output_root,
        render_mode=render_mode,
        target_settings=target_settings,
    )
    if not isinstance(artifact, dict):
        raise RenderServiceError("Renderer did not return an artifact object")

    return artifact, clip_plan, source_highlight


def finalize_and_register_clip(
    *,
    output_root: Path,
    demo_id: str,
    artifact: dict[str, Any],
    clip_plan: dict[str, Any],
    source_highlight: dict[str, Any] | None,
) -> dict[str, Any]:
    if str(artifact.get("status") or "") != "completed":
        raise RenderServiceError(str(artifact.get("error") or "Render artifact status is not completed"))

    postprocess_settings = {}
    job_meta = artifact.get("job") if isinstance(artifact.get("job"), dict) else {}
    if isinstance(job_meta.get("postprocess_settings"), dict):
        postprocess_settings = dict(job_meta["postprocess_settings"])

    postprocess_result = normalize_artifact_media(artifact, overrides=postprocess_settings)
    if not bool(postprocess_result.get("ok")):
        artifact["status"] = "failed"
        artifact["error"] = "Post-process failed: " + str(postprocess_result.get("error") or "unknown error")
        _persist_artifact_metadata(artifact)
        raise RenderServiceError(
            "Post-process failed: "
            + str(postprocess_result.get("error") or "unknown post-process error")
        )
    _persist_artifact_metadata(artifact)

    record = register_clip(
        output_root=output_root,
        demo_id=demo_id,
        artifact=artifact,
        clip_plan=clip_plan,
        source_highlight=source_highlight,
    )

    status = str(record.get("status") or "")
    integrity = record.get("integrity") if isinstance(record.get("integrity"), dict) else {}
    has_video = bool(integrity.get("has_video"))
    has_thumbnail = bool(integrity.get("has_thumbnail"))
    if status != "completed" or not has_video or not has_thumbnail:
        raise RenderServiceError("Clip registration integrity failed")

    return record
