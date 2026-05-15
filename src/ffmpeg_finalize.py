"""
ffmpeg_finalize.py
Finalize raw HLAE output to MP4.
"""
from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from src.cs2_config import CS2Config

log = logging.getLogger(__name__)

VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv", ".avi", ".webm")


class FinalizeError(Exception):
    """Raised when finalization fails."""


@dataclass
class FFmpegFinalizeResult:
    success: bool = False
    final_video_path: str = ""
    raw_output_path: str = ""
    thumbnail_path: str = ""
    output_size_bytes: int = 0
    duration_s: float = 0.0
    frame_count: int = 0
    codec: str = ""
    resolution: str = ""
    error: str = ""
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _run(args: list[str], timeout_s: int) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired as e:
        raise FinalizeError(f"FFmpeg timed out after {timeout_s}s: {e}")
    except FileNotFoundError as e:
        raise FinalizeError(f"FFmpeg executable not found: {e}")


def _probe(config: CS2Config, video_path: Path) -> dict[str, Any]:
    args = [
        config.ffprobe_exe,
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(video_path),
    ]
    proc = _run(args, timeout_s=config.postprocess_probe_timeout_s)
    if proc.returncode != 0:
        raise FinalizeError(f"ffprobe failed: {proc.stderr[:500]}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise FinalizeError(f"ffprobe output parse failed: {e}")


def _discover_frame_pattern(raw_path: Path, image_format: str) -> tuple[Path, int] | None:
    if raw_path.is_dir():
        files = sorted(raw_path.glob(f"*.{image_format.lstrip('.')}"))
        if not files:
            return None
        first = files[0]
        digits = "".join(ch for ch in first.stem if ch.isdigit())
        start_number = int(digits) if digits else 0
        pattern = raw_path / f"%06d.{image_format.lstrip('.')}"
        return pattern, start_number
    return None


def _find_video_input(raw_path: Path) -> Path | None:
    if raw_path.is_file() and raw_path.suffix.lower() in VIDEO_EXTENSIONS:
        return raw_path
    if raw_path.is_dir():
        for ext in VIDEO_EXTENSIONS:
            matches = sorted(raw_path.glob(f"*{ext}"))
            if matches:
                return matches[0]
    return None


def _extract_thumbnail(config: CS2Config, video_path: Path, thumbnail_path: Path) -> str | None:
    args = [
        config.ffmpeg_exe,
        "-y",
        "-ss", str(config.postprocess_thumbnail_offset_s),
        "-i", str(video_path),
        "-vframes", "1",
        "-vf", f"scale={config.postprocess_thumbnail_width}:-1",
        str(thumbnail_path),
    ]
    proc = _run(args, timeout_s=config.postprocess_thumbnail_timeout_s)
    if proc.returncode == 0 and thumbnail_path.exists():
        return str(thumbnail_path)
    return None


def finalize_render_output(
    config: CS2Config,
    *,
    raw_path: str,
    output_dir: str,
    output_name: str,
    fps: int,
    width: int,
    height: int,
    image_format: str,
    container: str,
    encode_preset: str,
) -> FFmpegFinalizeResult:
    result = FFmpegFinalizeResult(raw_output_path=raw_path)
    raw = Path(raw_path)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    final_video = out_dir / f"clip.{container}"

    pattern_info = _discover_frame_pattern(raw, image_format=image_format)
    if pattern_info:
        pattern, start_number = pattern_info
        args = [
            config.ffmpeg_exe,
            "-y",
            "-framerate", str(fps),
            "-start_number", str(start_number),
            "-i", str(pattern),
            "-vf", f"scale={width}:{height}",
            "-c:v", "libx264",
            "-preset", encode_preset,
            "-crf", str(config.postprocess_crf),
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            str(final_video),
        ]
        proc = _run(args, timeout_s=config.postprocess_transcode_timeout_s)
        if proc.returncode != 0:
            raise FinalizeError(f"FFmpeg image-sequence finalize failed: {proc.stderr[:1000]}")
    else:
        video_input = _find_video_input(raw)
        if not video_input:
            raise FinalizeError(f"No raw render output discovered under {raw_path}")
        args = [
            config.ffmpeg_exe,
            "-y",
            "-i", str(video_input),
            "-c:v", "libx264",
            "-preset", encode_preset,
            "-crf", str(config.postprocess_crf),
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            str(final_video),
        ]
        proc = _run(args, timeout_s=config.postprocess_transcode_timeout_s)
        if proc.returncode != 0:
            raise FinalizeError(f"FFmpeg video finalize failed: {proc.stderr[:1000]}")

    if not final_video.exists():
        raise FinalizeError("Final video was not created")
    result.final_video_path = str(final_video)
    result.output_size_bytes = final_video.stat().st_size
    if result.output_size_bytes < config.postprocess_minimum_output_bytes:
        raise FinalizeError(
            f"Final video too small ({result.output_size_bytes} bytes < {config.postprocess_minimum_output_bytes})"
        )

    probe = _probe(config, final_video)
    fmt = probe.get("format", {})
    result.duration_s = float(fmt.get("duration") or 0.0)
    for stream in probe.get("streams", []):
        if stream.get("codec_type") == "video":
            result.codec = stream.get("codec_name", "")
            result.frame_count = int(stream.get("nb_frames") or 0)
            result.resolution = f"{stream.get('width', 0)}x{stream.get('height', 0)}"
            break

    thumb = _extract_thumbnail(config, final_video, out_dir / "thumbnail.jpg")
    if thumb:
        result.thumbnail_path = thumb
    result.success = True
    return result
