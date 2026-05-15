"""
cs2_controller.py
Low-level CS2 game control via netcon (TCP console).

Handles:
- CS2 process detection and launch
- Netcon TCP connection
- Console command execution
- Demo loading
- Tick seeking
"""
from __future__ import annotations

import logging
import os
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from src.cs2_config import CS2Config

log = logging.getLogger(__name__)

NETCON_RECV_SIZE = 4096
NETCON_LINE_TERMINATOR = b"\n"


class CS2ControlError(Exception):
    """Raised when a CS2 control operation fails irrecoverably."""


@dataclass
class NetconConnection:
    host: str = "127.0.0.1"
    port: int = 2121
    timeout_s: float = 5.0
    _sock: socket.socket | None = field(default=None, repr=False)

    def _drain_pending_output(self) -> None:
        """Clear any stale buffered output before issuing a new command.

        Netcon can leave trailing lines from earlier commands in the socket
        buffer. If we don't drain them, subsequent probes may read stale text
        and incorrectly verify POV / playback state.
        """
        if not self._sock:
            return
        try:
            self._sock.settimeout(0.01)
            while True:
                data = self._sock.recv(NETCON_RECV_SIZE)
                if not data:
                    break
        except socket.timeout:
            pass
        finally:
            self._sock.settimeout(self.timeout_s)

    def connect(self) -> None:
        if self._sock is not None:
            return
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.timeout_s)
            sock.connect((self.host, self.port))
            self._sock = sock
            log.info(f"Netcon connected to {self.host}:{self.port}")
        except (socket.error, OSError) as e:
            self._sock = None
            raise CS2ControlError(f"Netcon connection failed: {e}")

    def close(self) -> None:
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    @property
    def connected(self) -> bool:
        return self._sock is not None

    def send_command(self, cmd: str) -> str:
        """Send a console command and return response text."""
        if not self._sock:
            raise CS2ControlError("Netcon not connected")

        try:
            self._drain_pending_output()
            payload = (cmd.strip() + "\n").encode("utf-8")
            self._sock.sendall(payload)

            # Read until the socket goes quiet. This is more reliable than a
            # single fixed sleep and helps us avoid mixing adjacent command
            # responses together.
            deadline = time.monotonic() + min(max(self.timeout_s, 0.3), 1.5)
            idle_deadline = time.monotonic() + 0.15
            chunks = []
            self._sock.settimeout(0.15)
            while time.monotonic() < deadline:
                try:
                    data = self._sock.recv(NETCON_RECV_SIZE)
                except socket.timeout:
                    if chunks or time.monotonic() >= idle_deadline:
                        break
                    continue
                if not data:
                    break
                chunks.append(data)
                idle_deadline = time.monotonic() + 0.12
            self._sock.settimeout(self.timeout_s)
            return b"".join(chunks).decode("utf-8", errors="replace")
        except (socket.error, OSError) as e:
            self.close()
            raise CS2ControlError(f"Netcon send failed: {e}")

    def send_commands(self, commands: list[str], delay_between: float = 0.05) -> list[str]:
        """Send multiple commands sequentially."""
        responses = []
        for cmd in commands:
            resp = self.send_command(cmd)
            responses.append(resp)
            if delay_between > 0:
                time.sleep(delay_between)
        return responses


@dataclass
class CS2Controller:
    """High-level CS2 game controller."""
    config: CS2Config
    netcon: NetconConnection | None = field(default=None)
    _cs2_pid: int | None = field(default=None)
    command_observer: Callable[[str, str], None] | None = field(default=None, repr=False)

    def __post_init__(self):
        if self.netcon is None:
            self.netcon = NetconConnection(
                port=self.config.netcon_port,
                timeout_s=self.config.netcon_timeout_s,
            )

    # ── Process management ────────────────────────────────────────────────

    def is_cs2_running(self) -> bool:
        """Check if CS2 process is running."""
        try:
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq cs2.exe", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=5,
            )
            return "cs2.exe" in result.stdout.lower()
        except (subprocess.SubprocessError, OSError):
            return False

    def get_cs2_pid(self) -> int | None:
        """Get CS2 process ID if running."""
        try:
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq cs2.exe", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.strip().split("\n"):
                parts = line.strip().strip('"').split('","')
                if len(parts) >= 2 and "cs2" in parts[0].lower():
                    try:
                        return int(parts[1])
                    except ValueError:
                        pass
        except (subprocess.SubprocessError, OSError):
            pass
        return None

    def launch_cs2(self) -> int | None:
        """Launch CS2 with required launch options."""
        if self.config.skip_launch:
            log.info("CS2 launch skipped (CS2_SKIP_LAUNCH=true)")
            if self.is_cs2_running():
                self._cs2_pid = self.get_cs2_pid()
                return self._cs2_pid
            raise CS2ControlError("CS2 not running and launch is disabled")

        if not self.config.cs2_exe_exists:
            raise CS2ControlError(f"CS2 executable not found: {self.config.cs2_exe}")

        if self.is_cs2_running():
            self._cs2_pid = self.get_cs2_pid()
            log.info(f"CS2 already running (PID {self._cs2_pid})")
            return self._cs2_pid

        launch_args = [
            self.config.cs2_exe,
            "-usercon",
            f"-netconport", str(self.config.netcon_port),
            "-condebug",
        ]

        if not self.config.cs2_fullscreen:
            launch_args.extend(["-windowed", "-noborder"])
            launch_args.extend(["-w", str(self.config.cs2_width)])
            launch_args.extend(["-h", str(self.config.cs2_height)])

        if self.config.cs2_launch_options:
            launch_args.extend(self.config.cs2_launch_options.split())

        log.info(f"Launching CS2: {' '.join(launch_args)}")
        try:
            proc = subprocess.Popen(
                launch_args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._cs2_pid = proc.pid
        except OSError as e:
            raise CS2ControlError(f"Failed to launch CS2: {e}")

        # Wait for CS2 to be detectable
        for attempt in range(30):
            time.sleep(2)
            if self.is_cs2_running():
                self._cs2_pid = self.get_cs2_pid()
                log.info(f"CS2 launched successfully (PID {self._cs2_pid})")
                return self._cs2_pid

        raise CS2ControlError("CS2 launched but not detected within 60s")

    # ── Netcon ────────────────────────────────────────────────────────────

    def ensure_netcon(self) -> NetconConnection:
        """Ensure netcon is connected, attempting connection if needed."""
        if self.netcon and self.netcon.connected:
            return self.netcon
        if self.netcon is None:
            self.netcon = NetconConnection(
                port=self.config.netcon_port,
                timeout_s=self.config.netcon_timeout_s,
            )
        # Retry connection a few times
        last_err = None
        for attempt in range(5):
            try:
                self.netcon.connect()
                return self.netcon
            except CS2ControlError as e:
                last_err = e
                time.sleep(2)
        raise CS2ControlError(f"Netcon connection failed after 5 attempts: {last_err}")

    def exec_command(self, cmd: str) -> str:
        """Execute a console command via netcon."""
        nc = self.ensure_netcon()
        response = nc.send_command(cmd)
        if self.command_observer is not None:
            try:
                self.command_observer(cmd, response)
            except Exception:
                pass
        return response

    def exec_commands(self, commands: list[str], delay: float = 0.05) -> list[str]:
        """Execute multiple console commands."""
        responses = []
        for idx, cmd in enumerate(commands):
            responses.append(self.exec_command(cmd))
            if idx != len(commands) - 1 and delay > 0:
                time.sleep(delay)
        return responses

    # ── Demo control ──────────────────────────────────────────────────────

    def stage_demo(self, demo_path: str) -> str:
        """Copy demo to CS2's expected directory if needed. Returns the playdemo-compatible path."""
        demo_p = Path(demo_path)
        if not demo_p.exists():
            raise CS2ControlError(f"Demo file not found: {demo_path}")

        # CS2 expects demos relative to csgo directory or absolute
        # Best approach: copy to CS2's replays directory
        cs2_dir = Path(self.config.cs2_exe).parent.parent.parent if self.config.cs2_exe else None
        if cs2_dir and cs2_dir.exists():
            replays_dir = cs2_dir / "csgo" / "replays"
            replays_dir.mkdir(parents=True, exist_ok=True)
            target = replays_dir / demo_p.name
            if not target.exists() or target.stat().st_size != demo_p.stat().st_size:
                shutil.copy2(str(demo_p), str(target))
                log.info(f"Demo staged to {target}")
            return f"replays/{demo_p.name}"
        return str(demo_p)

    def load_demo(self, playdemo_path: str) -> None:
        """Load a demo via playdemo command."""
        log.info(f"Loading demo: {playdemo_path}")
        self.exec_command(f"playdemo {playdemo_path}")

    def seek_to_tick(self, tick: int) -> None:
        """Seek to a specific tick in the currently loaded demo."""
        log.info(f"Seeking to tick {tick}")
        self.exec_command(f"demo_gototick {tick}")

    def seek_to_round(self, round_num: int) -> None:
        """Seek to a round using demo_goto with round targeting."""
        log.info(f"Seeking to round {round_num}")
        self.exec_command(f"demo_gototick {round_num} 0 1")

    def pause_demo(self) -> None:
        """Pause demo playback."""
        self.exec_command("demo_pause")

    def resume_demo(self) -> None:
        """Resume demo playback."""
        self.exec_command("demo_resume")

    def set_timescale(self, scale: float = 1.0) -> None:
        """Set demo playback speed."""
        self.exec_command(f"demo_timescale {scale}")

    def disconnect(self) -> None:
        """Disconnect from current demo/server."""
        self.exec_command("disconnect")

    # ── Environment check ─────────────────────────────────────────────────

    def check_environment(self) -> dict[str, Any]:
        """Run environment readiness checks."""
        checks = []
        blockers = []
        warnings = []

        # Platform
        import platform
        checks.append({"check": "platform", "status": "ok", "detail": f"Windows ({platform.version()})"})

        # CS2 exe
        if self.config.cs2_exe_exists:
            checks.append({"check": "cs2_exe", "status": "ok", "detail": self.config.cs2_exe})
        else:
            checks.append({"check": "cs2_exe", "status": "error", "detail": f"Not found: {self.config.cs2_exe}"})
            blockers.append("CS2 executable not found")

        # CS2 process
        running = self.is_cs2_running()
        pid = self.get_cs2_pid() if running else None
        if running:
            checks.append({"check": "cs2_process", "status": "ok", "detail": f"Running (PID {pid})"})
        else:
            checks.append({"check": "cs2_process", "status": "info", "detail": "Not running (will launch on demand)"})
            if not self.config.cs2_exe_exists:
                warnings.append("CS2 not running and executable not found")

        # Netcon port
        checks.append({"check": "netcon_port", "status": "ok", "detail": f"Port {self.config.netcon_port}"})

        return {
            "checks": checks,
            "blockers": blockers,
            "warnings": warnings,
            "cs2_running": running,
            "cs2_pid": pid,
            "cs2_exe_found": self.config.cs2_exe_exists,
            "ready": len(blockers) == 0,
        }

    def cleanup(self) -> None:
        """Close connections."""
        if self.netcon:
            self.netcon.close()
