"""
clip_store.py
Persistent clip index + record helpers.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.utils import safe_slug, atomic_json_write, generated_url

logger = logging.getLogger(__name__)

CLIP_INDEX_SCHEMA_VERSION = 3
CLIP_PACKAGE_VERSION = 1


def register_clip(
    output_root: str | Path,
    demo_id: str,
    artifact: dict,
    clip_plan: dict,
    source_highlight: dict | None = None,
) -> dict:
    index = _load_demo_index(output_root, demo_id)
    record = build_clip_record(demo_id, artifact, clip_plan, source_highlight=source_highlight)

    clips = [item for item in index.get("clips", []) if str(item.get("clip_id") or "") != record["clip_id"]]
    clips.append(record)
    clips.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)

    index["schema_version"] = CLIP_INDEX_SCHEMA_VERSION
    index["demo_id"] = demo_id
    index["updated_at"] = datetime.now(timezone.utc).isoformat()
    index["clip_count"] = len(clips)
    index["clips"] = clips
    _write_demo_index(output_root, demo_id, index)
    return record


def list_demo_clips(output_root: str | Path, demo_id: str) -> list[dict]:
    index = _load_demo_index(output_root, demo_id)
    clips = [validate_clip_record(dict(item), output_root=output_root) for item in index.get("clips", [])]
    clips.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return clips


def list_all_clips(output_root: str | Path) -> list[dict]:
    root = Path(output_root) / "clips"
    if not root.exists():
        return []

    clips: list[dict] = []
    for index_path in root.glob("*/index.json"):
        try:
            with open(index_path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except Exception:
            continue
        for item in payload.get("clips", []) or []:
            clips.append(validate_clip_record(dict(item), output_root=output_root))

    clips.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return clips


def get_clip_record(output_root: str | Path, clip_id: str, demo_id: str | None = None) -> dict | None:
    root = Path(output_root) / "clips"
    if demo_id:
        for item in list_demo_clips(output_root, demo_id):
            if str(item.get("clip_id") or "") == clip_id:
                return item
        return None

    if not root.exists():
        return None
    for index_path in root.glob("*/index.json"):
        try:
            with open(index_path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except Exception:
            continue
        for item in payload.get("clips", []) or []:
            if str(item.get("clip_id") or "") == clip_id:
                return validate_clip_record(dict(item), output_root=output_root)
    return None


def build_clip_record(
    demo_id: str,
    artifact: dict,
    clip_plan: dict,
    source_highlight: dict | None = None,
) -> dict:
    job = artifact.get("job") or {}
    clip_id = str(artifact.get("clip_id") or "")
    created_at = str(artifact.get("created_at") or datetime.now(timezone.utc).isoformat())

    source = {
        "demo_id": demo_id,
        "clip_plan_id": str(clip_plan.get("clip_plan_id") or artifact.get("clip_plan_id") or ""),
        "source_highlight_id": str(clip_plan.get("source_highlight_id") or artifact.get("source_highlight_id") or ""),
        "round_number": int(clip_plan.get("round_number") or 0),
        "primary_player": str(clip_plan.get("primary_player") or ""),
        "involved_players": list(clip_plan.get("involved_players") or []),
        "start_tick": int(clip_plan.get("start_tick") or 0),
        "anchor_tick": int(clip_plan.get("anchor_tick") or 0),
        "end_tick": int(clip_plan.get("end_tick") or 0),
        "clip_type": str(clip_plan.get("clip_type") or "highlight"),
        "tags": list(clip_plan.get("tags") or []),
        "score": float(clip_plan.get("score") or 0.0),
    }

    quality_profile = job.get("quality_profile") or {}
    render_profile: dict[str, Any] = {}
    for key in (
        "render_preset",
        "quality_tier",
        "resolution",
        "width",
        "height",
        "fps",
        "camera_mode",
        "observer_mode",
        "hud_mode",
        "capture_profile",
    ):
        val = job.get(key) or quality_profile.get(key) or artifact.get(key)
        if val is not None:
            render_profile[key] = val

    if "render_preset" not in render_profile:
        plan_preset = (clip_plan.get("metadata") or {}).get("render_preset") or {}
        render_profile["render_preset"] = plan_preset.get("name", "standard_highlight")
    if "capture_profile" not in render_profile:
        render_profile["capture_profile"] = quality_profile.get("capture_profile", "default")
    if "quality_tier" not in render_profile:
        render_profile["quality_tier"] = quality_profile.get("quality_tier", "medium")

    artifacts = _build_artifact_bundle(artifact)

    title = str(clip_plan.get("title") or "Clip")
    primary_player = source["primary_player"]
    round_number = source["round_number"]
    canonical_base = _canonical_basename(title, primary_player, round_number, clip_id)

    metadata: dict[str, Any] = {
        "source_highlight_type": (source_highlight or {}).get("type") or clip_plan.get("metadata", {}).get("source_highlight_type"),
        "source_highlight_title": (source_highlight or {}).get("title"),
        "render_warnings": list(artifact.get("warnings") or []),
        "pov_strategy": job.get("pov_strategy"),
        "artifact_metadata_path": artifact.get("metadata_path") or (artifacts.get("metadata") or {}).get("path"),
        "postprocess": artifact.get("postprocess"),
        "media_info": artifact.get("media_info"),
        "capture_execution": artifact.get("capture_execution"),
    }
    metadata = {k: v for k, v in metadata.items() if v is not None}

    lineage = {
        "queue_job_id": str(artifact.get("queue_job_id") or ""),
        "render_job_id": str(((artifact.get("render_job") or {}).get("job_id") or "")),
        "artifact_schema_version": int(artifact.get("artifact_schema_version") or 0),
        "queue_job_state_path": str(((artifact.get("queue_job") or {}).get("state_path") or "")),
        "queue_job_events_path": str(((artifact.get("queue_job") or {}).get("events_path") or "")),
    }

    export_meta = {
        "package_version": CLIP_PACKAGE_VERSION,
        "canonical_title": title,
        "canonical_filename_slug": safe_slug(canonical_base),
        "canonical_basename": canonical_base,
        "created_at": created_at,
        "summary": str(clip_plan.get("description") or ""),
    }

    record = {
        "schema_version": CLIP_INDEX_SCHEMA_VERSION,
        "clip_id": clip_id,
        "status": str(artifact.get("status") or "unknown"),
        "created_at": created_at,
        "duration_s": float(artifact.get("duration_s") or 0.0),
        "frame_count": int(artifact.get("frame_count") or 0),
        "resolution": str(artifact.get("resolution") or render_profile.get("resolution") or ""),
        "width": int(artifact.get("width") or render_profile.get("width") or 0),
        "height": int(artifact.get("height") or render_profile.get("height") or 0),
        "title": title,
        "description": str(clip_plan.get("description") or ""),
        "source": source,
        "render": {
            "mode": str(artifact.get("render_mode") or ""),
            "pov_mode": str(artifact.get("pov_mode") or clip_plan.get("pov_mode") or "auto"),
            "pov_player": artifact.get("pov_player") or clip_plan.get("pov_player"),
            "profile": render_profile,
        },
        "artifacts": artifacts,
        "lineage": lineage,
        "export": export_meta,
        "integrity": {
            "validation_warnings": [],
            "has_video": False,
            "has_thumbnail": False,
        },
        "metadata": metadata,
        "tags": source["tags"],
        "score": source["score"],
        # Legacy compatibility fields consumed by existing UI
        "demo_id": demo_id,
        "clip_plan_id": source["clip_plan_id"],
        "source_highlight_id": source["source_highlight_id"],
        "round_number": source["round_number"],
        "primary_player": source["primary_player"],
        "involved_players": source["involved_players"],
        "clip_type": source["clip_type"],
        "render_mode": str(artifact.get("render_mode") or ""),
        "pov_mode": str(artifact.get("pov_mode") or clip_plan.get("pov_mode") or "auto"),
        "pov_player": artifact.get("pov_player") or clip_plan.get("pov_player"),
        "camera_mode": render_profile.get("camera_mode"),
        "observer_mode": render_profile.get("observer_mode"),
        "hud_mode": render_profile.get("hud_mode"),
        "start_tick": source["start_tick"],
        "anchor_tick": source["anchor_tick"],
        "end_tick": source["end_tick"],
        "file_path": (artifacts.get("video") or {}).get("path"),
        "file_url": (artifacts.get("video") or {}).get("url"),
        "thumbnail_path": (artifacts.get("thumbnail") or {}).get("path"),
        "thumbnail_url": (artifacts.get("thumbnail") or {}).get("url"),
        "filename_slug": safe_slug(canonical_base),
        "render_profile": render_profile,
        "asset_dir": artifacts.get("base_dir"),
        "artifact_metadata_path": (artifacts.get("metadata") or {}).get("path"),
        "video_codec": artifact.get("video_codec"),
        "audio_codec": artifact.get("audio_codec"),
        "bit_rate": artifact.get("bit_rate"),
        "avg_frame_rate": artifact.get("avg_frame_rate"),
    }
    return validate_clip_record(record)


def validate_clip_record(record: dict, output_root: str | Path | None = None) -> dict:
    # Normalize legacy records into the structured sections expected by the UI/API.
    _normalize_record_sections(record)

    video = (record.get("artifacts") or {}).get("video") or {}
    thumb = (record.get("artifacts") or {}).get("thumbnail") or {}
    meta = (record.get("artifacts") or {}).get("metadata") or {}

    warnings = list(((record.get("integrity") or {}).get("validation_warnings")) or [])

    video_path = str(video.get("path") or "")
    thumb_path = str(thumb.get("path") or "")
    meta_path = str(meta.get("path") or "")

    has_video = bool(video_path and Path(video_path).exists())
    has_thumb = bool(thumb_path and Path(thumb_path).exists())
    has_meta = bool(meta_path and Path(meta_path).exists())

    if video_path and not has_video:
        warnings.append("missing_video_file")
    if thumb_path and not has_thumb:
        warnings.append("missing_thumbnail_file")
    if record.get("status") == "completed" and not thumb_path:
        warnings.append("missing_thumbnail_reference")
    if meta_path and not has_meta:
        warnings.append("missing_artifact_metadata")

    source = record.get("source") or {}
    if not str(record.get("clip_id") or ""):
        warnings.append("empty_clip_id")
    if not str(source.get("clip_plan_id") or ""):
        warnings.append("missing_clip_plan_id")
    if int(source.get("round_number") or 0) <= 0:
        warnings.append("invalid_round_number")

    start_tick = int(source.get("start_tick") or 0)
    anchor_tick = int(source.get("anchor_tick") or 0)
    end_tick = int(source.get("end_tick") or 0)
    if not (start_tick <= anchor_tick <= end_tick):
        warnings.append("invalid_tick_window")

    if output_root:
        queue_map = _load_queue_jobs(output_root)
        queue_job_id = str(((record.get("lineage") or {}).get("queue_job_id") or ""))
        if queue_job_id:
            qj = queue_map.get(queue_job_id)
            if not qj:
                warnings.append("stale_queue_job_reference")
            elif str(qj.get("clip_id") or "") and str(qj.get("clip_id") or "") != str(record.get("clip_id") or ""):
                warnings.append("queue_job_clip_mismatch")

    warnings = sorted(set(warnings))

    record.setdefault("schema_version", CLIP_INDEX_SCHEMA_VERSION)
    record["validated_at"] = datetime.now(timezone.utc).isoformat()

    integrity = dict(record.get("integrity") or {})
    integrity["validation_warnings"] = warnings
    integrity["has_video"] = has_video
    integrity["has_thumbnail"] = has_thumb
    integrity["has_artifact_metadata"] = has_meta
    integrity["video_size_bytes"] = _file_size(video_path)
    integrity["thumbnail_size_bytes"] = _file_size(thumb_path)
    record["integrity"] = integrity

    metadata = dict(record.get("metadata") or {})
    metadata["validation_warnings"] = warnings
    record["metadata"] = metadata

    if record.get("status") == "completed" and "missing_video_file" in warnings:
        record["status"] = "missing"

    # Keep legacy fields up to date for existing frontend behavior.
    source = record.get("source") or {}
    artifacts = record.get("artifacts") or {}
    video = artifacts.get("video") or {}
    thumb = artifacts.get("thumbnail") or {}
    meta = artifacts.get("metadata") or {}
    render = record.get("render") or {}
    profile = (render.get("profile") or {})

    record["demo_id"] = source.get("demo_id") or record.get("demo_id") or ""
    record["clip_plan_id"] = source.get("clip_plan_id") or record.get("clip_plan_id") or ""
    record["source_highlight_id"] = source.get("source_highlight_id") or record.get("source_highlight_id") or ""
    record["round_number"] = int(source.get("round_number") or record.get("round_number") or 0)
    record["primary_player"] = source.get("primary_player") or record.get("primary_player") or ""
    record["involved_players"] = list(source.get("involved_players") or record.get("involved_players") or [])
    record["clip_type"] = source.get("clip_type") or record.get("clip_type") or "highlight"
    record["start_tick"] = int(source.get("start_tick") or record.get("start_tick") or 0)
    record["anchor_tick"] = int(source.get("anchor_tick") or record.get("anchor_tick") or 0)
    record["end_tick"] = int(source.get("end_tick") or record.get("end_tick") or 0)
    record["score"] = float(source.get("score") or record.get("score") or 0.0)
    record["tags"] = list(source.get("tags") or record.get("tags") or [])

    record["render_mode"] = render.get("mode") or record.get("render_mode") or ""
    record["pov_mode"] = render.get("pov_mode") or record.get("pov_mode") or "auto"
    record["pov_player"] = render.get("pov_player") or record.get("pov_player")
    record["render_profile"] = profile
    record["camera_mode"] = profile.get("camera_mode")
    record["observer_mode"] = profile.get("observer_mode")
    record["hud_mode"] = profile.get("hud_mode")

    record["file_path"] = video.get("path")
    record["file_url"] = video.get("url")
    record["thumbnail_path"] = thumb.get("path")
    record["thumbnail_url"] = thumb.get("url")
    record["asset_dir"] = artifacts.get("base_dir")
    record["artifact_metadata_path"] = meta.get("path")

    if not record.get("filename_slug"):
        export_meta = record.get("export") or {}
        record["filename_slug"] = safe_slug(export_meta.get("canonical_basename") or record.get("clip_id") or "")

    return record


def scan_clip_integrity(output_root: str | Path) -> dict:
    """Scan all clip indexes and return an integrity report."""
    root = Path(output_root) / "clips"
    report: dict[str, Any] = {
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": CLIP_INDEX_SCHEMA_VERSION,
        "total_clips": 0,
        "ok": 0,
        "missing_video": 0,
        "missing_thumbnail": 0,
        "missing_artifact_metadata": 0,
        "stale_queue_refs": 0,
        "stale_indexes": [],
        "issues": [],
    }
    if not root.exists():
        return report

    queue_map = _load_queue_jobs(output_root)

    for index_path in root.glob("*/index.json"):
        try:
            with open(index_path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except Exception:
            report["stale_indexes"].append(str(index_path))
            continue

        for item in payload.get("clips", []) or []:
            report["total_clips"] += 1
            clip = validate_clip_record(dict(item), output_root=output_root)
            problems = list(((clip.get("integrity") or {}).get("validation_warnings")) or [])
            if "missing_video_file" in problems:
                report["missing_video"] += 1
            if "missing_thumbnail_file" in problems:
                report["missing_thumbnail"] += 1
            if "missing_artifact_metadata" in problems:
                report["missing_artifact_metadata"] += 1

            queue_job_id = str(((clip.get("lineage") or {}).get("queue_job_id") or ""))
            if queue_job_id and queue_job_id not in queue_map:
                if "stale_queue_job_reference" not in problems:
                    problems.append("stale_queue_job_reference")
                report["stale_queue_refs"] += 1

            if problems:
                report["issues"].append({
                    "clip_id": clip.get("clip_id", ""),
                    "demo_id": clip.get("demo_id", ""),
                    "index_path": str(index_path),
                    "problems": sorted(set(problems)),
                })
            else:
                report["ok"] += 1

    return report


def _build_artifact_bundle(artifact: dict) -> dict:
    structured = dict(artifact.get("artifacts") or {})
    base_dir = structured.get("base_dir") or artifact.get("asset_dir")

    video_path = _first_non_empty(
        (structured.get("video") or {}).get("path"),
        artifact.get("output_path"),
    )
    thumb_path = _first_non_empty(
        (structured.get("thumbnail") or {}).get("path"),
        artifact.get("thumbnail_path"),
    )
    meta_path = _first_non_empty(
        (structured.get("metadata") or {}).get("path"),
        artifact.get("metadata_path"),
    )

    if not base_dir:
        # Backfill base_dir from known artifact paths for older records.
        if video_path:
            base_dir = str(Path(video_path).parent)
        elif meta_path:
            base_dir = str(Path(meta_path).parent)

    video = {
        "path": video_path,
        "url": (structured.get("video") or {}).get("url") or generated_url(video_path),
        "kind": (structured.get("video") or {}).get("kind") or "video/mp4",
    }
    thumbnail = {
        "path": thumb_path,
        "url": (structured.get("thumbnail") or {}).get("url") or generated_url(thumb_path),
        "kind": (structured.get("thumbnail") or {}).get("kind") or "image/jpeg",
    }
    metadata = {
        "path": meta_path,
        "url": (structured.get("metadata") or {}).get("url") or generated_url(meta_path),
        "kind": (structured.get("metadata") or {}).get("kind") or "application/json",
    }

    return {
        "base_dir": str(base_dir) if base_dir else "",
        "video": video,
        "thumbnail": thumbnail,
        "metadata": metadata,
    }


def _normalize_record_sections(record: dict) -> None:
    if "source" not in record:
        record["source"] = {
            "demo_id": record.get("demo_id", ""),
            "clip_plan_id": record.get("clip_plan_id", ""),
            "source_highlight_id": record.get("source_highlight_id", ""),
            "round_number": int(record.get("round_number") or 0),
            "primary_player": record.get("primary_player", ""),
            "involved_players": list(record.get("involved_players") or []),
            "start_tick": int(record.get("start_tick") or 0),
            "anchor_tick": int(record.get("anchor_tick") or 0),
            "end_tick": int(record.get("end_tick") or 0),
            "clip_type": record.get("clip_type", "highlight"),
            "tags": list(record.get("tags") or []),
            "score": float(record.get("score") or 0.0),
        }

    if "render" not in record:
        record["render"] = {
            "mode": record.get("render_mode", ""),
            "pov_mode": record.get("pov_mode", "auto"),
            "pov_player": record.get("pov_player"),
            "profile": dict(record.get("render_profile") or {}),
        }

    if "artifacts" not in record:
        record["artifacts"] = {
            "base_dir": record.get("asset_dir") or str(Path(record.get("file_path") or "").parent) if record.get("file_path") else "",
            "video": {
                "path": record.get("file_path"),
                "url": record.get("file_url") or generated_url(record.get("file_path")),
                "kind": "video/mp4",
            },
            "thumbnail": {
                "path": record.get("thumbnail_path"),
                "url": record.get("thumbnail_url") or generated_url(record.get("thumbnail_path")),
                "kind": "image/jpeg",
            },
            "metadata": {
                "path": record.get("artifact_metadata_path") or (record.get("metadata") or {}).get("artifact_metadata_path"),
                "url": generated_url(record.get("artifact_metadata_path") or (record.get("metadata") or {}).get("artifact_metadata_path")),
                "kind": "application/json",
            },
        }

    if "lineage" not in record:
        record["lineage"] = {
            "queue_job_id": "",
            "render_job_id": "",
            "artifact_schema_version": 0,
        }

    if "export" not in record:
        source = record.get("source") or {}
        canonical_base = _canonical_basename(
            str(record.get("title") or "Clip"),
            str(source.get("primary_player") or ""),
            int(source.get("round_number") or 0),
            str(record.get("clip_id") or ""),
        )
        record["export"] = {
            "package_version": CLIP_PACKAGE_VERSION,
            "canonical_title": str(record.get("title") or "Clip"),
            "canonical_filename_slug": safe_slug(canonical_base),
            "canonical_basename": canonical_base,
            "created_at": str(record.get("created_at") or datetime.now(timezone.utc).isoformat()),
            "summary": str(record.get("description") or ""),
        }

    if "integrity" not in record:
        record["integrity"] = {
            "validation_warnings": list(((record.get("metadata") or {}).get("validation_warnings") or [])),
            "has_video": False,
            "has_thumbnail": False,
        }


def _canonical_basename(title: str, player: str, round_number: int, clip_id: str) -> str:
    title_part = safe_slug(title)[:48]
    player_part = safe_slug(player)[:24] if player else "player"
    suffix = safe_slug(clip_id)[-8:] if clip_id else "clip"
    return f"{title_part}_r{max(0, int(round_number))}_{player_part}_{suffix}"


def _load_queue_jobs(output_root: str | Path) -> dict[str, dict]:
    queue_path = Path(output_root) / "queue" / "render_queue.json"
    if not queue_path.exists():
        return {}
    try:
        data = json.loads(queue_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, list):
        return {}
    result: dict[str, dict] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        job_id = str(item.get("job_id") or "")
        if job_id:
            result[job_id] = item
    return result


def _file_size(path_str: str | None) -> int | None:
    path = Path(path_str) if path_str else None
    if not path or not path.exists() or not path.is_file():
        return None
    try:
        return int(path.stat().st_size)
    except OSError:
        return None


def _first_non_empty(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _load_demo_index(output_root: str | Path, demo_id: str) -> dict:
    path = _demo_index_path(output_root, demo_id)
    if not path.exists():
        return {
            "schema_version": CLIP_INDEX_SCHEMA_VERSION,
            "demo_id": demo_id,
            "updated_at": None,
            "clip_count": 0,
            "clips": [],
        }
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except Exception:
        return {
            "schema_version": CLIP_INDEX_SCHEMA_VERSION,
            "demo_id": demo_id,
            "updated_at": None,
            "clip_count": 0,
            "clips": [],
        }
    payload.setdefault("clips", [])
    payload.setdefault("clip_count", len(payload["clips"]))
    payload.setdefault("demo_id", demo_id)
    payload.setdefault("schema_version", CLIP_INDEX_SCHEMA_VERSION)
    return payload


def _write_demo_index(output_root: str | Path, demo_id: str, payload: dict) -> None:
    path = _demo_index_path(output_root, demo_id)
    atomic_json_write(path, payload)


def _demo_index_path(output_root: str | Path, demo_id: str) -> Path:
    return Path(output_root) / "clips" / safe_slug(demo_id) / "index.json"
