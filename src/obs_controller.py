"""
obs_controller.py
OBS WebSocket controller for capture management.

Handles:
- OBS WebSocket connection
- Recording start/stop
- Output directory configuration
- Recording status monitoring
- Output file discovery
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Try to import obsws (obs-websocket-py or obsws-python)
_obsws_available = False
_obsws_lib = None

try:
    import obsws_python as obs
    _obsws_available = True
    _obsws_lib = "obsws_python"
except ImportError:
    pass

if not _obsws_available:
    try:
        import obswebsocket as obs  # type: ignore
        _obsws_available = True
        _obsws_lib = "obswebsocket"
    except ImportError:
        pass


class OBSError(Exception):
    """Raised when an OBS operation fails."""


@dataclass
class OBSRecordingState:
    """Tracks OBS recording state."""
    recording: bool = False
    output_dir: str = ""
    output_path: str = ""
    started_at: float = 0.0
    stopped_at: float = 0.0
    duration_s: float = 0.0
    file_size_bytes: int = 0

    def to_dict(self) -> dict:
        return {
            "recording": self.recording,
            "output_dir": self.output_dir,
            "output_path": self.output_path,
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "duration_s": round(self.duration_s, 2),
            "file_size_bytes": self.file_size_bytes,
        }


@dataclass
class OBSController:
    """Controls OBS recording via WebSocket."""
    host: str = "localhost"
    port: int = 4455
    password: str = ""
    _client: Any = field(default=None, repr=False)
    _connected: bool = False
    state: OBSRecordingState = field(default_factory=OBSRecordingState)

    @property
    def available(self) -> bool:
        return _obsws_available

    @property
    def connected(self) -> bool:
        return self._connected

    def connect(self) -> None:
        """Connect to OBS WebSocket."""
        if not _obsws_available:
            raise OBSError(
                "OBS WebSocket library not installed. "
                "Install with: pip install obsws-python"
            )

        if self._connected:
            return

        try:
            if _obsws_lib == "obsws_python":
                self._client = obs.ReqClient(
                    host=self.host,
                    port=self.port,
                    password=self.password or None,
                    timeout=10,
                )
            else:
                # obswebsocket fallback
                ws = obs.obsws(self.host, self.port, self.password or "")
                ws.connect()
                self._client = ws

            self._connected = True
            log.info(f"OBS connected ({_obsws_lib}) at {self.host}:{self.port}")

        except Exception as e:
            self._connected = False
            raise OBSError(f"OBS connection failed: {e}")

    def disconnect(self) -> None:
        """Disconnect from OBS."""
        if self._client:
            try:
                if _obsws_lib == "obswebsocket" and hasattr(self._client, "disconnect"):
                    self._client.disconnect()
            except Exception:
                pass
        self._client = None
        self._connected = False

    def ensure_connected(self) -> None:
        """Ensure we're connected, reconnecting if needed."""
        if not self._connected:
            self.connect()

    def set_output_directory(self, output_dir: str) -> None:
        """Configure OBS recording output directory."""
        self.ensure_connected()
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        self.state.output_dir = output_dir

        try:
            if _obsws_lib == "obsws_python":
                # Set the recording output path via profile settings
                self._client.set_profile_parameter(
                    "SimpleOutput", "FilePath", output_dir,
                )
            log.info(f"OBS output directory set to: {output_dir}")
        except Exception as e:
            log.warning(f"Could not set OBS output directory via API: {e}")
            # Not fatal — OBS may use its own configured directory

    def start_recording(self) -> None:
        """Start OBS recording."""
        self.ensure_connected()

        try:
            if _obsws_lib == "obsws_python":
                self._client.start_record()
            else:
                self._client.call(obs.requests.StartRecording())

            self.state.recording = True
            self.state.started_at = time.time()
            log.info("OBS recording started")

        except Exception as e:
            raise OBSError(f"Failed to start recording: {e}")

    def stop_recording(self) -> str:
        """Stop OBS recording and return the output file path.

        Returns:
            Path to the recorded file.
        """
        self.ensure_connected()

        try:
            if _obsws_lib == "obsws_python":
                result = self._client.stop_record()
                output_path = getattr(result, "output_path", "")
            else:
                result = self._client.call(obs.requests.StopRecording())
                output_path = ""

            self.state.recording = False
            self.state.stopped_at = time.time()
            self.state.duration_s = self.state.stopped_at - self.state.started_at

            if output_path:
                self.state.output_path = output_path
            log.info(f"OBS recording stopped. Output: {output_path}")
            return output_path

        except Exception as e:
            self.state.recording = False
            raise OBSError(f"Failed to stop recording: {e}")

    def get_recording_status(self) -> dict:
        """Get current recording status from OBS."""
        self.ensure_connected()
        try:
            if _obsws_lib == "obsws_python":
                status = self._client.get_record_status()
                return {
                    "active": getattr(status, "output_active", False),
                    "paused": getattr(status, "output_paused", False),
                    "timecode": getattr(status, "output_timecode", ""),
                    "duration": getattr(status, "output_duration", 0),
                    "bytes": getattr(status, "output_bytes", 0),
                }
            return {"active": self.state.recording}
        except Exception as e:
            log.warning(f"Could not get recording status: {e}")
            return {"active": self.state.recording, "error": str(e)}

    def get_obs_version(self) -> str:
        """Get OBS version string."""
        self.ensure_connected()
        try:
            if _obsws_lib == "obsws_python":
                ver = self._client.get_version()
                return getattr(ver, "obs_version", "unknown")
            return "unknown"
        except Exception:
            return "unknown"

    def find_output_files(self, output_dir: str, after_time: float = 0) -> list[Path]:
        """Find recording output files created after a given time."""
        d = Path(output_dir)
        if not d.exists():
            return []

        files = []
        for ext in ("*.mp4", "*.mkv", "*.flv", "*.mov", "*.ts"):
            for f in d.glob(ext):
                if f.stat().st_mtime >= after_time:
                    files.append(f)
        files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
        return files

    def wait_for_recording_file(
        self,
        output_dir: str,
        started_after: float,
        timeout_s: float = 30,
        min_size_bytes: int = 1024,
    ) -> Path | None:
        """Wait for a recording file to appear and be non-trivially sized."""
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            files = self.find_output_files(output_dir, after_time=started_after)
            for f in files:
                if f.stat().st_size >= min_size_bytes:
                    log.info(f"Found recording output: {f} ({f.stat().st_size} bytes)")
                    return f
            time.sleep(1.0)
        return None
