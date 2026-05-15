"""
cs2_config.py
Centralized configuration for CS2 runtime control.

All CS2/OBS/render settings are loaded from environment variables with sensible defaults.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def _env_bool(key: str, default: bool = False) -> bool:
    v = _env(key).lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return default


def _env_int(key: str, default: int = 0) -> int:
    try:
        return int(_env(key))
    except (ValueError, TypeError):
        return default


def _env_float(key: str, default: float = 0.0) -> float:
    try:
        return float(_env(key))
    except (ValueError, TypeError):
        return default


@dataclass(frozen=True)
class CS2Config:
    # CS2 executable
    cs2_exe: str
    cs2_steam_path: str
    cs2_launch_options: str
    cs2_fullscreen: bool
    cs2_width: int
    cs2_height: int

    # Netcon (console remote control)
    netcon_port: int
    netcon_timeout_s: float

    # Playback timing
    skip_launch: bool
    demo_ready_settle_s: float
    post_seek_settle_s: float
    use_coarse_round_seek: bool

    # HLAE
    hlae_exe: str
    hlae_launch_template: str
    hlae_args: str
    hlae_config_dir: str
    hlae_hook_dll: str

    # OBS
    obs_ws_host: str
    obs_ws_port: int
    obs_ws_password: str
    obs_output_dir: str

    # Render
    render_job_max_retries: int
    render_job_lease_timeout_s: int
    render_worker_id: str
    render_backend: str
    render_output_root: str
    render_temp_root: str

    # HLAE render
    hlae_render_backend: str
    hlae_render_fps: int
    hlae_render_width: int
    hlae_render_height: int
    hlae_render_image_format: str
    hlae_render_pre_roll_ticks: int
    hlae_render_post_roll_ticks: int
    hlae_render_load_timeout_s: int
    hlae_render_seek_timeout_s: int
    hlae_render_pov_timeout_s: int
    hlae_render_output_timeout_s: int
    hlae_render_startup_settle_s: float
    hlae_incremental_probe_transport: str
    hlae_enable_gototick_fallback: bool

    # FFmpeg postprocess
    ffmpeg_exe: str
    ffprobe_exe: str
    postprocess_preset: str
    postprocess_crf: int
    postprocess_audio_bitrate: str
    postprocess_thumbnail_offset_s: float
    postprocess_thumbnail_width: int
    postprocess_transcode_timeout_s: int
    postprocess_thumbnail_timeout_s: int
    postprocess_probe_timeout_s: int
    postprocess_minimum_output_bytes: int
    postprocess_require_ffprobe: bool

    @property
    def cs2_exe_exists(self) -> bool:
        return bool(self.cs2_exe) and Path(self.cs2_exe).exists()

    @property
    def hlae_enabled(self) -> bool:
        return bool(self.hlae_exe) and Path(self.hlae_exe).exists()

    @property
    def obs_configured(self) -> bool:
        return bool(self.obs_ws_host) and self.obs_ws_port > 0


def load_config() -> CS2Config:
    """Load configuration from environment variables."""
    return CS2Config(
        cs2_exe=_env("CS2_EXE"),
        cs2_steam_path=_env("CS2_STEAM_PATH"),
        cs2_launch_options=_env("CS2_LAUNCH_OPTIONS"),
        cs2_fullscreen=_env_bool("CS2_FULLSCREEN", False),
        cs2_width=_env_int("CS2_WIDTH", 1920),
        cs2_height=_env_int("CS2_HEIGHT", 1080),
        netcon_port=_env_int("CS2_NETCON_PORT", 2121),
        netcon_timeout_s=_env_float("CS2_NETCON_TIMEOUT_S", 5.0),
        skip_launch=_env_bool("CS2_SKIP_LAUNCH", False),
        demo_ready_settle_s=_env_float("CS2_DEMO_READY_SETTLE_S", 8.0),
        post_seek_settle_s=_env_float("CS2_POST_SEEK_SETTLE_S", 4.0),
        use_coarse_round_seek=_env_bool("CS2_USE_COARSE_ROUND_SEEK", False),
        hlae_exe=_env("CS2_HLAE_EXE"),
        hlae_launch_template=_env("CS2_HLAE_LAUNCH_TEMPLATE"),
        hlae_args=_env("CS2_HLAE_ARGS"),
        hlae_config_dir=_env("CS2_HLAE_CONFIG_DIR"),
        hlae_hook_dll=_env("CS2_HLAE_HOOK_DLL"),
        obs_ws_host=_env("OBS_WS_HOST", "localhost"),
        obs_ws_port=_env_int("OBS_WS_PORT", 4455),
        obs_ws_password=_env("OBS_WS_PASSWORD"),
        obs_output_dir=_env("OBS_OUTPUT_DIR"),
        render_job_max_retries=_env_int("RENDER_JOB_MAX_RETRIES", 1),
        render_job_lease_timeout_s=_env_int("RENDER_JOB_LEASE_TIMEOUT_S", 120),
        render_worker_id=_env("RENDER_WORKER_ID"),
        render_backend=_env("RENDER_BACKEND", "hlae"),
        render_output_root=_env("RENDER_OUTPUT_ROOT", "outputs/generated"),
        render_temp_root=_env("RENDER_TEMP_ROOT", "outputs/generated/tmp"),
        hlae_render_backend=_env("HLAE_RENDER_BACKEND", "mirv_streams"),
        hlae_render_fps=_env_int("HLAE_RENDER_FPS", 60),
        hlae_render_width=_env_int("HLAE_RENDER_WIDTH", _env_int("CS2_WIDTH", 1920)),
        hlae_render_height=_env_int("HLAE_RENDER_HEIGHT", _env_int("CS2_HEIGHT", 1080)),
        hlae_render_image_format=_env("HLAE_RENDER_IMAGE_FORMAT", "png"),
        hlae_render_pre_roll_ticks=_env_int("HLAE_RENDER_PRE_ROLL_TICKS", 128),
        hlae_render_post_roll_ticks=_env_int("HLAE_RENDER_POST_ROLL_TICKS", 128),
        hlae_render_load_timeout_s=_env_int("HLAE_RENDER_LOAD_TIMEOUT_S", 20),
        hlae_render_seek_timeout_s=_env_int("HLAE_RENDER_SEEK_TIMEOUT_S", 10),
        hlae_render_pov_timeout_s=_env_int("HLAE_RENDER_POV_TIMEOUT_S", 8),
        hlae_render_output_timeout_s=_env_int("HLAE_RENDER_OUTPUT_TIMEOUT_S", 30),
        hlae_render_startup_settle_s=_env_float("HLAE_RENDER_STARTUP_SETTLE_S", 1.5),
        hlae_incremental_probe_transport=_env("HLAE_INCREMENTAL_PROBE_TRANSPORT", "mirv_skip_tick"),
        hlae_enable_gototick_fallback=_env_bool("HLAE_ENABLE_GOTOTICK_FALLBACK", False),
        ffmpeg_exe=_env("FFMPEG_EXE", "ffmpeg"),
        ffprobe_exe=_env("FFPROBE_EXE", "ffprobe"),
        postprocess_preset=_env("POSTPROCESS_PRESET", "veryfast"),
        postprocess_crf=_env_int("POSTPROCESS_CRF", 20),
        postprocess_audio_bitrate=_env("POSTPROCESS_AUDIO_BITRATE", "128k"),
        postprocess_thumbnail_offset_s=_env_float("POSTPROCESS_THUMBNAIL_OFFSET_S", 1.0),
        postprocess_thumbnail_width=_env_int("POSTPROCESS_THUMBNAIL_WIDTH", 1280),
        postprocess_transcode_timeout_s=_env_int("POSTPROCESS_TRANSCODE_TIMEOUT_S", 240),
        postprocess_thumbnail_timeout_s=_env_int("POSTPROCESS_THUMBNAIL_TIMEOUT_S", 45),
        postprocess_probe_timeout_s=_env_int("POSTPROCESS_PROBE_TIMEOUT_S", 30),
        postprocess_minimum_output_bytes=_env_int("POSTPROCESS_MINIMUM_OUTPUT_BYTES", 10240),
        postprocess_require_ffprobe=_env_bool("POSTPROCESS_REQUIRE_FFPROBE", False),
    )
