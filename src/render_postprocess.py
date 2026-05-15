"""
render_postprocess.py
Post-processing for captured clips (transcode, thumbnail, validation).

Uses FFmpeg for:
- Transcoding raw OBS output to production MP4
- Thumbnail extraction
- Duration/integrity validation via ffprobe
"""
from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from src.cs2_config import CS2Config

log = logging.getLogger(__name__)


class PostprocessError(Exception):
    """Raised when post-processing fails."""


@dataclass
class PostprocessResult:
    status: str = "pending"  # pending, completed, failed
    input_path: str = ""
    output_path: str = ""
    thumbnail_path: str = ""
    input_size_bytes: int = 0
    output_size_bytes: int = 0
    duration_s: float = 0.0
    frame_count: int = 0
    codec: str = ""
    resolution: str = ""
    error: str = ""
    warnings: list[str] | None = None

    def __post_init__(self):
        if self.warnings is None:
            self.warnings = []

    def to_dict(self) -> dict:
        return asdict(self)


def _run_ffmpeg(args: list[str], timeout_s: int = 240) -> subprocess.CompletedProcess:
    """Run an FFmpeg command with timeout."""
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        return result
    except subprocess.TimeoutExpired:
        raise PostprocessError(f"FFmpeg timed out after {timeout_s}s")
    except FileNotFoundError as e:
        raise PostprocessError(f"FFmpeg not found: {e}")


def probe_video(ffprobe_exe: str, video_path: str, timeout_s: int = 30) -> dict:
    """Probe a video file and return metadata."""
    args = [
        ffprobe_exe,
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(video_path),
    ]
    result = _run_ffmpeg(args, timeout_s=timeout_s)
    if result.returncode != 0:
        raise PostprocessError(f"ffprobe failed: {result.stderr[:500]}")

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise PostprocessError(f"ffprobe output parse failed: {e}")


def transcode(
    config: CS2Config,
    input_path: str,
    output_path: str,
) -> PostprocessResult:
    """Transcode raw recording to production MP4.

    Args:
        config: CS2 configuration with ffmpeg settings
        input_path: Path to raw recording file
        output_path: Path for output MP4

    Returns:
        PostprocessResult with transcode status and metadata
    """
    result = PostprocessResult(input_path=input_path, output_path=output_path)

    inp = Path(input_path)
    if not inp.exists():
        result.status = "failed"
        result.error = f"Input file not found: {input_path}"
        return result

    result.input_size_bytes = inp.stat().st_size
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    args = [
        config.ffmpeg_exe,
        "-y",
        "-i", str(input_path),
        "-c:v", "libx264",
        "-preset", config.postprocess_preset,
        "-crf", str(config.postprocess_crf),
        "-c:a", "aac",
        "-b:a", config.postprocess_audio_bitrate,
        "-movflags", "+faststart",
        str(output_path),
    ]

    log.info(f"Transcoding: {input_path} -> {output_path}")
    try:
        proc = _run_ffmpeg(args, timeout_s=config.postprocess_transcode_timeout_s)
    except PostprocessError as e:
        result.status = "failed"
        result.error = str(e)
        return result

    if proc.returncode != 0:
        result.status = "failed"
        result.error = f"FFmpeg transcode failed (exit {proc.returncode}): {proc.stderr[:500]}"
        return result

    out = Path(output_path)
    if not out.exists():
        result.status = "failed"
        result.error = "Output file not created"
        return result

    result.output_size_bytes = out.stat().st_size
    if result.output_size_bytes < config.postprocess_minimum_output_bytes:
        result.status = "failed"
        result.error = f"Output too small ({result.output_size_bytes} bytes)"
        return result

    # Probe output for metadata
    try:
        probe = probe_video(config.ffprobe_exe, output_path, config.postprocess_probe_timeout_s)
        fmt = probe.get("format", {})
        result.duration_s = float(fmt.get("duration", 0))

        for stream in probe.get("streams", []):
            if stream.get("codec_type") == "video":
                result.codec = stream.get("codec_name", "")
                w = stream.get("width", 0)
                h = stream.get("height", 0)
                result.resolution = f"{w}x{h}"
                # Approximate frame count
                nb_frames = stream.get("nb_frames")
                if nb_frames:
                    result.frame_count = int(nb_frames)
                break
    except PostprocessError as e:
        if config.postprocess_require_ffprobe:
            result.status = "failed"
            result.error = f"ffprobe validation failed: {e}"
            return result
        result.warnings.append(f"ffprobe failed (non-fatal): {e}")

    result.status = "completed"
    log.info(f"Transcode completed: {output_path} ({result.output_size_bytes} bytes, {result.duration_s:.1f}s)")
    return result


def extract_thumbnail(
    config: CS2Config,
    video_path: str,
    thumbnail_path: str,
) -> str | None:
    """Extract a thumbnail frame from the video.

    Returns:
        Path to thumbnail if successful, None otherwise.
    """
    Path(thumbnail_path).parent.mkdir(parents=True, exist_ok=True)

    args = [
        config.ffmpeg_exe,
        "-y",
        "-ss", str(config.postprocess_thumbnail_offset_s),
        "-i", str(video_path),
        "-vframes", "1",
        "-vf", f"scale={config.postprocess_thumbnail_width}:-1",
        "-q:v", "2",
        str(thumbnail_path),
    ]

    try:
        proc = _run_ffmpeg(args, timeout_s=config.postprocess_thumbnail_timeout_s)
        if proc.returncode == 0 and Path(thumbnail_path).exists():
            log.info(f"Thumbnail extracted: {thumbnail_path}")
            return thumbnail_path
    except PostprocessError as e:
        log.warning(f"Thumbnail extraction failed: {e}")

    return None


def postprocess_clip(
    config: CS2Config,
    raw_path: str,
    output_dir: str,
    clip_id: str,
) -> PostprocessResult:
    """Full post-processing pipeline for a single clip.

    1. Transcode raw → MP4
    2. Extract thumbnail
    3. Validate output

    Args:
        config: CS2 configuration
        raw_path: Path to raw recording file
        output_dir: Directory for output files
        clip_id: Clip identifier for naming

    Returns:
        PostprocessResult with full status
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    output_mp4 = str(out_dir / "clip.mp4")
    thumbnail_jpg = str(out_dir / "thumbnail.jpg")

    # Transcode
    result = transcode(config, raw_path, output_mp4)
    if result.status != "completed":
        return result

    # Thumbnail
    thumb = extract_thumbnail(config, output_mp4, thumbnail_jpg)
    if thumb:
        result.thumbnail_path = thumb

    return result
