"""
render_postprocess.py
FFmpeg/ffprobe post-processing for rendered clips.

This module turns raw captured video into final user-facing assets:
  - normalized H.264 MP4
  - +faststart for web playback
  - thumbnail image
  - media metadata extraction
  - integrity validation
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import cv2

from src.utils import generated_url

logger = logging.getLogger(__name__)


class PostProcessError(RuntimeError):
    pass


@dataclass(frozen=True)
class PostProcessSettings:
    ffmpeg_exe: str = "ffmpeg"
    ffprobe_exe: str = "ffprobe"
    video_codec: str = "libx264"
    audio_codec: str = "aac"
    preset: str = "veryfast"
    crf: int = 20
    pixel_format: str = "yuv420p"
    movflags: str = "+faststart"
    audio_bitrate: str = "128k"
    audio_channels: int = 2
    thumbnail_offset_s: float = 1.0
    thumbnail_width: int = 1280
    transcode_timeout_s: int = 240
    thumbnail_timeout_s: int = 45
    probe_timeout_s: int = 30
    minimum_output_bytes: int = 10_240
    require_ffprobe: bool = False

    @classmethod
    def from_artifact(
        cls,
        artifact: dict[str, Any],
        *,
        overrides: dict[str, Any] | None = None,
    ) -> "PostProcessSettings":
        env = os.environ
        artifact_job = artifact.get("job") if isinstance(artifact.get("job"), dict) else {}
        artifact_cfg = artifact.get("postprocess_settings") if isinstance(artifact.get("postprocess_settings"), dict) else {}
        job_cfg = artifact_job.get("postprocess_settings") if isinstance(artifact_job.get("postprocess_settings"), dict) else {}
        merged: dict[str, Any] = {
            **artifact_cfg,
            **job_cfg,
            **(overrides or {}),
        }

        def _env(name: str) -> str | None:
            return env.get(f"POSTPROCESS_{name.upper()}")

        def _bool(name: str, default: bool) -> bool:
            if name in merged:
                return bool(merged[name])
            raw = _env(name)
            if raw is None:
                return default
            return raw.strip().lower() in {"1", "true", "yes", "on"}

        def _int(name: str, default: int) -> int:
            if name in merged and merged[name] is not None:
                return int(merged[name])
            raw = _env(name)
            return int(raw) if raw else default

        def _float(name: str, default: float) -> float:
            if name in merged and merged[name] is not None:
                return float(merged[name])
            raw = _env(name)
            return float(raw) if raw else default

        def _str(name: str, default: str) -> str:
            if name in merged and merged[name] is not None:
                return str(merged[name])
            return str(_env(name) or default)

        return cls(
            ffmpeg_exe=_str("ffmpeg_exe", env.get("FFMPEG_EXE", "ffmpeg")),
            ffprobe_exe=_str("ffprobe_exe", env.get("FFPROBE_EXE", "ffprobe")),
            video_codec=_str("video_codec", "libx264"),
            audio_codec=_str("audio_codec", "aac"),
            preset=_str("preset", "veryfast"),
            crf=_int("crf", 20),
            pixel_format=_str("pixel_format", "yuv420p"),
            movflags=_str("movflags", "+faststart"),
            audio_bitrate=_str("audio_bitrate", "128k"),
            audio_channels=_int("audio_channels", 2),
            thumbnail_offset_s=_float("thumbnail_offset_s", 1.0),
            thumbnail_width=_int("thumbnail_width", 1280),
            transcode_timeout_s=_int("transcode_timeout_s", 240),
            thumbnail_timeout_s=_int("thumbnail_timeout_s", 45),
            probe_timeout_s=_int("probe_timeout_s", 30),
            minimum_output_bytes=_int("minimum_output_bytes", 10_240),
            require_ffprobe=_bool("require_ffprobe", False),
        )


@dataclass
class MediaProbeInfo:
    duration_s: float = 0.0
    frame_count: int = 0
    width: int = 0
    height: int = 0
    resolution: str = ""
    bit_rate: int = 0
    format_name: str = ""
    video_codec: str = ""
    audio_codec: str = ""
    avg_frame_rate: float = 0.0
    file_size_bytes: int = 0
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return payload


@dataclass
class PostProcessResult:
    ok: bool = False
    output_path: str = ""
    thumbnail_path: str = ""
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    reencoded: bool = False
    ffmpeg_available: bool = False
    ffprobe_available: bool = False
    settings: dict[str, Any] = field(default_factory=dict)
    media_info: dict[str, Any] = field(default_factory=dict)
    steps: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "output_path": self.output_path,
            "thumbnail_path": self.thumbnail_path,
            "warnings": list(self.warnings),
            "error": self.error,
            "reencoded": self.reencoded,
            "ffmpeg_available": self.ffmpeg_available,
            "ffprobe_available": self.ffprobe_available,
            "settings": dict(self.settings),
            "media_info": dict(self.media_info),
            "steps": list(self.steps),
        }


def _resolve_bin(path_or_name: str) -> str | None:
    if not path_or_name:
        return None
    p = Path(path_or_name)
    if p.is_file():
        return str(p)
    found = shutil.which(path_or_name)
    return found


def _run(cmd: list[str], timeout_s: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=max(1, int(timeout_s)),
        check=False,
    )


def _probe_video(path: Path, settings: PostProcessSettings) -> MediaProbeInfo | None:
    ffprobe = _resolve_bin(settings.ffprobe_exe)
    if not ffprobe:
        return None

    cmd = [
        ffprobe,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    proc = _run(cmd, timeout_s=settings.probe_timeout_s)
    if proc.returncode != 0:
        logger.warning("ffprobe failed for %s: %s", path, (proc.stderr or "").strip()[:500])
        return None

    try:
        payload = json.loads(proc.stdout or "{}")
    except Exception as exc:
        logger.warning("ffprobe returned invalid JSON for %s: %s", path, exc)
        return None

    info = MediaProbeInfo(raw=payload)
    fmt = payload.get("format") if isinstance(payload.get("format"), dict) else {}
    streams = payload.get("streams") if isinstance(payload.get("streams"), list) else []

    info.file_size_bytes = int(path.stat().st_size) if path.exists() else 0
    info.format_name = str(fmt.get("format_name") or "")
    try:
        info.duration_s = float(fmt.get("duration") or 0.0)
    except Exception:
        info.duration_s = 0.0
    try:
        info.bit_rate = int(float(fmt.get("bit_rate") or 0))
    except Exception:
        info.bit_rate = 0

    for stream in streams:
        if not isinstance(stream, dict):
            continue
        codec_type = str(stream.get("codec_type") or "")
        if codec_type == "video":
            info.video_codec = str(stream.get("codec_name") or "")
            info.width = int(stream.get("width") or 0)
            info.height = int(stream.get("height") or 0)
            info.resolution = f"{info.width}x{info.height}" if info.width and info.height else ""
            try:
                info.frame_count = int(stream.get("nb_frames") or 0)
            except Exception:
                info.frame_count = 0
            raw_rate = str(stream.get("avg_frame_rate") or "0/0")
            if "/" in raw_rate:
                num, den = raw_rate.split("/", 1)
                try:
                    den_v = float(den)
                    if den_v:
                        info.avg_frame_rate = round(float(num) / den_v, 3)
                except Exception:
                    info.avg_frame_rate = 0.0
        elif codec_type == "audio" and not info.audio_codec:
            info.audio_codec = str(stream.get("codec_name") or "")

    return info


def _probe_video_cv2(path: Path) -> MediaProbeInfo | None:
    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            return None
        info = MediaProbeInfo()
        info.file_size_bytes = int(path.stat().st_size) if path.exists() else 0
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        info.width = width
        info.height = height
        info.resolution = f"{width}x{height}" if width and height else ""
        info.frame_count = frame_count
        info.avg_frame_rate = round(fps, 3) if fps > 0 else 0.0
        if fps > 0 and frame_count > 0:
            info.duration_s = round(frame_count / fps, 3)
        return info
    finally:
        cap.release()


def _validate_output_file(path: Path, settings: PostProcessSettings, media: MediaProbeInfo | None) -> None:
    if not path.is_file():
        raise PostProcessError(f"Post-processed output file not found: {path}")
    size_bytes = path.stat().st_size
    if size_bytes <= 0:
        raise PostProcessError(f"Post-processed output file is empty: {path}")
    if size_bytes < settings.minimum_output_bytes:
        raise PostProcessError(
            f"Post-processed output is too small ({size_bytes} bytes, minimum {settings.minimum_output_bytes})"
        )
    if media:
        if media.duration_s <= 0:
            raise PostProcessError("ffprobe reported invalid output duration")
        if media.width <= 0 or media.height <= 0:
            raise PostProcessError("ffprobe reported invalid output resolution")
        if media.video_codec and media.video_codec.lower() != "h264":
            raise PostProcessError(f"Expected H.264 output, got {media.video_codec}")


def normalize_artifact_media(
    artifact: dict[str, Any],
    *,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize raw output to final H.264 MP4 assets.

    Returns a structured result dict and updates the artifact in place.
    Raises no exception directly; failures are surfaced in the returned payload.
    """
    result = PostProcessResult()
    settings = PostProcessSettings.from_artifact(artifact, overrides=overrides)
    result.settings = asdict(settings)

    output_path_raw = str(artifact.get("output_path") or "").strip()
    if not output_path_raw:
        result.error = "postprocess_missing_output_path"
        result.warnings.append(result.error)
        artifact["postprocess"] = result.to_dict()
        return result.to_dict()

    output_path = Path(output_path_raw)
    if not output_path.is_file():
        result.error = f"postprocess_output_file_not_found: {output_path}"
        result.warnings.append("postprocess_output_file_not_found")
        artifact["postprocess"] = result.to_dict()
        return result.to_dict()

    ffmpeg = _resolve_bin(settings.ffmpeg_exe)
    ffprobe = _resolve_bin(settings.ffprobe_exe)
    result.ffmpeg_available = bool(ffmpeg)
    result.ffprobe_available = bool(ffprobe)

    if not ffmpeg:
        result.error = "ffmpeg_not_available"
        result.warnings.append("ffmpeg_not_available")
        artifact["postprocess"] = result.to_dict()
        return result.to_dict()

    encoded_path = output_path.with_name(f"{output_path.stem}.normalized.mp4")
    thumb_path = output_path.with_name("thumbnail.jpg")

    try:
        transcode_cmd = [
            ffmpeg,
            "-y",
            "-i",
            str(output_path),
            "-c:v",
            settings.video_codec,
            "-preset",
            settings.preset,
            "-crf",
            str(settings.crf),
            "-pix_fmt",
            settings.pixel_format,
            "-movflags",
            settings.movflags,
            "-c:a",
            settings.audio_codec,
            "-b:a",
            settings.audio_bitrate,
            "-ac",
            str(settings.audio_channels),
            str(encoded_path),
        ]
        result.steps.append({"step": "transcode", "command": transcode_cmd})
        transcode = _run(transcode_cmd, timeout_s=settings.transcode_timeout_s)
        if transcode.returncode != 0 or not encoded_path.is_file():
            raise PostProcessError(
                "ffmpeg_transcode_failed: "
                + ((transcode.stderr or transcode.stdout or "").strip()[:500] or "unknown error")
            )
        result.reencoded = True

        try:
            output_path.unlink(missing_ok=True)
        except Exception:
            pass
        encoded_path.replace(output_path)
        result.output_path = str(output_path)

        thumb_cmd = [
            ffmpeg,
            "-y",
            "-ss",
            f"{max(0.0, settings.thumbnail_offset_s):.3f}",
            "-i",
            str(output_path),
            "-frames:v",
            "1",
            "-vf",
            f"scale='min({settings.thumbnail_width},iw)':-2",
            str(thumb_path),
        ]
        result.steps.append({"step": "thumbnail", "command": thumb_cmd})
        thumb_proc = _run(thumb_cmd, timeout_s=settings.thumbnail_timeout_s)
        if thumb_proc.returncode != 0 or not thumb_path.is_file() or thumb_path.stat().st_size <= 0:
            raise PostProcessError(
                "ffmpeg_thumbnail_failed: "
                + ((thumb_proc.stderr or thumb_proc.stdout or "").strip()[:500] or "unknown error")
            )
        result.thumbnail_path = str(thumb_path)

        media = _probe_video(output_path, settings)
        if not media:
            media = _probe_video_cv2(output_path)
        if settings.require_ffprobe and not media:
            raise PostProcessError("ffprobe_required_but_unavailable_or_failed")
        if media:
            _validate_output_file(output_path, settings, media)
            result.media_info = media.to_dict()
        else:
            _validate_output_file(output_path, settings, None)
            result.warnings.append("ffprobe_and_cv2_probe_unavailable")

        artifact["output_path"] = str(output_path)
        artifact["output_url"] = generated_url(output_path)
        artifact["thumbnail_path"] = str(thumb_path)
        artifact["thumbnail_url"] = generated_url(thumb_path)

        artifacts = artifact.setdefault("artifacts", {})
        video_art = artifacts.setdefault("video", {})
        video_art["path"] = str(output_path)
        video_art["url"] = generated_url(output_path)
        video_art["kind"] = "video/mp4"
        thumb_art = artifacts.setdefault("thumbnail", {})
        thumb_art["path"] = str(thumb_path)
        thumb_art["url"] = generated_url(thumb_path)
        thumb_art["kind"] = "image/jpeg"

        if result.media_info:
            media_info = result.media_info
            artifact["duration_s"] = float(media_info.get("duration_s") or artifact.get("duration_s") or 0.0)
            artifact["frame_count"] = int(media_info.get("frame_count") or artifact.get("frame_count") or 0)
            artifact["resolution"] = media_info.get("resolution") or artifact.get("resolution")
            artifact["width"] = int(media_info.get("width") or artifact.get("width") or 0)
            artifact["height"] = int(media_info.get("height") or artifact.get("height") or 0)
            artifact["video_codec"] = media_info.get("video_codec") or artifact.get("video_codec")
            artifact["audio_codec"] = media_info.get("audio_codec") or artifact.get("audio_codec")
            artifact["bit_rate"] = int(media_info.get("bit_rate") or artifact.get("bit_rate") or 0)
            artifact["avg_frame_rate"] = float(media_info.get("avg_frame_rate") or artifact.get("avg_frame_rate") or 0.0)
            artifact["media_info"] = media_info

        result.ok = True
        artifact["postprocess"] = result.to_dict()
        artifact["warnings"] = sorted(set(list(artifact.get("warnings") or []) + result.warnings))
        return result.to_dict()
    except Exception as exc:
        result.error = str(exc)
        artifact["postprocess"] = result.to_dict()
        artifact["warnings"] = sorted(set(list(artifact.get("warnings") or []) + result.warnings))
        logger.error("Post-process failed for %s: %s", output_path, exc, exc_info=True)
        return result.to_dict()
