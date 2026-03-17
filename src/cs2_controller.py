"""
cs2_controller.py
Low-level CS2 process and console control layer (Windows-only).

Provides:
  - CS2 process detection and launch
  - Netcon (TCP console) command interface
  - Demo loading and playback commands
  - Camera / observer mode commands
  - Structured status reporting

This module does NOT handle recording — it only manages CS2 game state
for demo playback. Recording (OBS / game capture) will be layered on top.
"""

from __future__ import annotations

import ctypes
import hashlib
import logging
import re
import socket
import subprocess
import sys
import time
import os
import shutil
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from src.cs2_config import build_cs2_config, get_cs2_launch_args, validate_cs2_config

logger = logging.getLogger(__name__)

# ── Win32 keyboard input helpers (fallback when netcon TCP is unavailable) ────
# These are only available on Windows; on other platforms they are set to None
# so that the module can be imported for testing and non-Windows usage.

_user32 = None
_kernel32 = None

if sys.platform == "win32":
    import ctypes.wintypes
    _user32 = ctypes.windll.user32
    _kernel32 = ctypes.windll.kernel32

    # Configure ctypes return/arg types for 64-bit compatibility
    _kernel32.GlobalAlloc.restype = ctypes.c_void_p
    _kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    _kernel32.GlobalLock.restype = ctypes.c_void_p
    _kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    _kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    _kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
    _user32.SetClipboardData.restype = ctypes.c_void_p
    _user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]

# Virtual key codes
_VK_RETURN = 0x0D
_VK_BACK = 0x08
_VK_CONTROL = 0x11
_VK_ESCAPE = 0x1B
_VK_OEM_3 = 0xC0  # ` / ~ (backtick/tilde — CS2 console toggle)
_VK_A = 0x41
_VK_V = 0x56

# Keyboard event flags
_KEYEVENTF_KEYUP = 0x0002

# Clipboard
_CF_UNICODETEXT = 13
_GMEM_MOVEABLE = 0x0002
_GMEM_ZEROINIT = 0x0040

# CS2 window title
_CS2_WINDOW_TITLE = "Counter-Strike 2"


def _kbd_press(vk: int) -> None:
    """Simulate a single key press (down + up)."""
    _user32.keybd_event(vk, 0, 0, 0)
    time.sleep(0.02)
    _user32.keybd_event(vk, 0, _KEYEVENTF_KEYUP, 0)
    time.sleep(0.04)


def _kbd_combo(vk_modifier: int, vk_key: int) -> None:
    """Simulate a key combo like Ctrl+V."""
    _user32.keybd_event(vk_modifier, 0, 0, 0)
    time.sleep(0.02)
    _user32.keybd_event(vk_key, 0, 0, 0)
    time.sleep(0.02)
    _user32.keybd_event(vk_key, 0, _KEYEVENTF_KEYUP, 0)
    time.sleep(0.02)
    _user32.keybd_event(vk_modifier, 0, _KEYEVENTF_KEYUP, 0)
    time.sleep(0.04)


def _clipboard_set(text: str) -> bool:
    """Copy *text* to the Windows clipboard. Returns True on success."""
    encoded = (text + "\0").encode("utf-16-le")
    h_mem = _kernel32.GlobalAlloc(_GMEM_MOVEABLE | _GMEM_ZEROINIT, len(encoded))
    if not h_mem:
        return False
    ptr = _kernel32.GlobalLock(h_mem)
    if not ptr:
        _kernel32.GlobalFree(h_mem)
        return False
    ctypes.memmove(ptr, encoded, len(encoded))
    _kernel32.GlobalUnlock(h_mem)
    if not _user32.OpenClipboard(0):
        _kernel32.GlobalFree(h_mem)
        return False
    _user32.EmptyClipboard()
    _user32.SetClipboardData(_CF_UNICODETEXT, h_mem)
    _user32.CloseClipboard()
    return True


def _find_cs2_window() -> int:
    """Return the HWND of the CS2 window, or 0 if not found."""
    hwnd = _user32.FindWindowW(None, _CS2_WINDOW_TITLE)
    return hwnd or 0


def _activate_cs2_window(hwnd: int) -> bool:
    """Bring CS2 to the foreground. Returns True if successful."""
    if not hwnd:
        return False
    # ShowWindow SW_RESTORE (9) in case it's minimized
    _user32.ShowWindow(hwnd, 9)
    time.sleep(0.1)
    result = _user32.SetForegroundWindow(hwnd)
    time.sleep(0.3)
    return bool(result)


# ── Status types ──────────────────────────────────────────────────────────────

class CS2Status(str, Enum):
    NOT_FOUND = "not_found"
    NOT_RUNNING = "not_running"
    RUNNING = "running"
    LAUNCHING = "launching"
    LAUNCH_FAILED = "launch_failed"
    READY = "ready"


class NetconStatus(str, Enum):
    DISABLED = "disabled"
    NOT_CONNECTED = "not_connected"
    CONNECTED = "connected"
    CONNECT_FAILED = "connect_failed"


@dataclass
class CS2State:
    """Snapshot of current CS2 process and connection state."""
    cs2_status: CS2Status = CS2Status.NOT_RUNNING
    netcon_status: NetconStatus = NetconStatus.NOT_CONNECTED
    pid: int | None = None
    cs2_exe_found: bool = False
    cs2_exe_path: str | None = None
    netcon_port: int = 2121
    warnings: list[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "cs2_status": self.cs2_status.value,
            "netcon_status": self.netcon_status.value,
            "pid": self.pid,
            "cs2_exe_found": self.cs2_exe_found,
            "cs2_exe_path": self.cs2_exe_path,
            "netcon_port": self.netcon_port,
            "warnings": list(self.warnings),
            "error": self.error,
        }


# ── Controller ────────────────────────────────────────────────────────────────

class CS2Controller:
    """Low-level CS2 game process and console controller.

    Supports two command transport mechanisms:
      1. Netcon TCP — fast and bidirectional (preferred)
      2. Win32 keyboard console — fallback when netcon is unavailable.
         Simulates keystrokes into CS2's developer console.

    Usage:
        ctrl = CS2Controller()         # uses auto-detected config
        state = ctrl.check_status()     # is CS2 running?
        ctrl.launch()                   # start CS2 if not running
        ctrl.connect_netcon()           # connect to console
        ctrl.send_command("echo hi")    # send a console command
    """

    def __init__(self, config: dict | None = None):
        self._cfg = config or build_cs2_config()
        self._process: subprocess.Popen | None = None
        self._netcon_sock: socket.socket | None = None
        self._netcon_connected = False
        self._allow_ui_fallback = bool(self._cfg.get("allow_ui_fallback", False))
        # Keyboard fallback — activated when netcon TCP fails
        self._kbd_fallback = False

    @property
    def config(self) -> dict:
        return dict(self._cfg)

    @property
    def can_send_commands(self) -> bool:
        """True only when strict command channel (netcon) is available."""
        return bool(self._netcon_connected and self._netcon_sock)

    @property
    def can_send_commands_with_ui_fallback(self) -> bool:
        """True when netcon is connected OR explicit debug UI fallback is usable."""
        if self.can_send_commands:
            return True
        if not self._allow_ui_fallback:
            return False
        return bool(_find_cs2_window())

    # ── Process detection ─────────────────────────────────────────────────

    def check_status(self) -> CS2State:
        """Check current CS2 state without changing anything."""
        state = CS2State()
        state.netcon_port = self._cfg.get("netcon_port", 2121)

        # Check executable
        exe = self._cfg.get("cs2_exe")
        if exe and Path(exe).is_file():
            state.cs2_exe_found = True
            state.cs2_exe_path = exe
        else:
            state.cs2_exe_found = False
            state.cs2_exe_path = exe
            state.warnings.append("CS2 executable not found on this machine")

        # Check if process is running
        pid = self._find_cs2_process()
        if pid:
            state.cs2_status = CS2Status.RUNNING
            state.pid = pid
        else:
            state.cs2_status = CS2Status.NOT_RUNNING

        # Check netcon
        if not self._cfg.get("use_netcon", True):
            state.netcon_status = NetconStatus.DISABLED
        elif self._netcon_connected:
            state.netcon_status = NetconStatus.CONNECTED
        else:
            state.netcon_status = NetconStatus.NOT_CONNECTED

        return state

    def get_diagnostics(self) -> dict:
        """Return full diagnostic info for debugging."""
        state = self.check_status()
        config_diag = validate_cs2_config(self._cfg)
        return {
            "state": state.to_dict(),
            "config_validation": config_diag,
            "config": {k: v for k, v in self._cfg.items()},
        }

    # ── Process management ────────────────────────────────────────────────

    def ensure_running(self) -> CS2State:
        """Ensure CS2 is running. Launch it if not.

        Returns updated state. Does NOT raise on failure — check state.cs2_status.
        """
        state = self.check_status()
        if state.cs2_status == CS2Status.RUNNING:
            logger.info("CS2 already running (PID %s)", state.pid)
            return state

        if not state.cs2_exe_found:
            state.cs2_status = CS2Status.NOT_FOUND
            state.error = "Cannot launch CS2: executable not found"
            return state

        return self._launch_cs2(state)

    def _launch_cs2(self, state: CS2State) -> CS2State:
        """Attempt to launch CS2 process."""
        state.cs2_status = CS2Status.LAUNCHING
        try:
            args = get_cs2_launch_args(self._cfg)
            logger.info("Launching CS2: %s", " ".join(args))

            self._process = subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.DETACHED_PROCESS,
            )
            state.pid = self._process.pid

            # Wait for process to start
            timeout = self._cfg.get("launch_timeout", 45)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                pid = self._find_cs2_process()
                if pid:
                    state.cs2_status = CS2Status.RUNNING
                    state.pid = pid
                    logger.info("CS2 launched (PID %d)", pid)
                    return state
                time.sleep(1.0)

            state.cs2_status = CS2Status.LAUNCH_FAILED
            state.error = f"CS2 process not detected within {timeout}s after launch"
            state.warnings.append("launch_timeout")
            return state

        except Exception as exc:
            state.cs2_status = CS2Status.LAUNCH_FAILED
            state.error = f"Failed to launch CS2: {exc}"
            logger.error("CS2 launch failed: %s", exc)
            return state

    def stop_running_instance(self) -> dict:
        """Terminate the currently running CS2 process, if any."""
        pid = self._find_cs2_process()
        if not pid:
            return {"success": True, "pid": None, "error": None}

        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )

            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if not self._find_cs2_process():
                    self.disconnect_netcon()
                    logger.info("Stopped existing CS2 instance (PID %s)", pid)
                    return {"success": True, "pid": pid, "error": None}
                time.sleep(1.0)

            return {
                "success": False,
                "pid": pid,
                "error": "Timed out waiting for CS2 process to exit",
            }
        except Exception as exc:
            return {"success": False, "pid": pid, "error": str(exc)}

    def restart_with_netcon(self) -> CS2State:
        """Restart CS2 so it is launched with the configured netcon args."""
        stop_result = self.stop_running_instance()
        if not stop_result.get("success"):
            state = self.check_status()
            state.cs2_status = CS2Status.LAUNCH_FAILED
            state.error = f"Failed to stop existing CS2 instance: {stop_result.get('error')}"
            return state

        state = self.check_status()
        if not state.cs2_exe_found:
            state.cs2_status = CS2Status.NOT_FOUND
            state.error = "Cannot relaunch CS2: executable not found"
            return state

        # Small settle so Steam/CS2 can release handles before relaunch.
        time.sleep(2.0)
        return self._launch_cs2(state)

    def _find_cs2_process(self) -> int | None:
        """Find running CS2 process ID (Windows-only)."""
        process_name = self._cfg.get("process_name", "cs2.exe")
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {process_name}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            for line in result.stdout.strip().splitlines():
                parts = line.strip('"').split('","')
                if len(parts) >= 2 and parts[0].lower() == process_name.lower():
                    try:
                        return int(parts[1])
                    except ValueError:
                        continue
        except Exception as exc:
            logger.debug("Process check failed: %s", exc)
        return None

    # ── Netcon (TCP console) ──────────────────────────────────────────────

    def connect_netcon(self) -> NetconStatus:
        """Connect to CS2's TCP console (netcon).

        CS2 must be launched with -netconport <port>.
        """
        if not self._cfg.get("use_netcon", True):
            return NetconStatus.DISABLED

        port = self._cfg.get("netcon_port", 2121)
        try:
            if self._netcon_sock:
                try:
                    self._netcon_sock.close()
                except Exception:
                    pass

            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5.0)
            sock.connect(("127.0.0.1", port))
            self._netcon_sock = sock
            self._netcon_connected = True
            logger.info("Connected to CS2 netcon on port %d", port)

            # Drain any welcome banner
            try:
                sock.settimeout(0.5)
                sock.recv(4096)
            except socket.timeout:
                pass
            sock.settimeout(5.0)

            return NetconStatus.CONNECTED

        except Exception as exc:
            self._netcon_connected = False
            self._netcon_sock = None
            logger.warning("Netcon connection failed on port %d: %s", port, exc)
            # Enable keyboard fallback only when explicitly allowed (manual debug).
            if self._allow_ui_fallback and _find_cs2_window():
                self._kbd_fallback = True
                logger.info(
                    "Keyboard console fallback activated (debug mode) — "
                    "commands will be sent via CS2's developer console"
                )
            return NetconStatus.CONNECT_FAILED

    # ── Keyboard console fallback ─────────────────────────────────────────

    def _send_via_keyboard(self, command: str) -> dict:
        """Send a console command by typing it into CS2's developer console.

        Flow: find window → activate → open console → paste command → Enter → close console.
        Returns the same structure as send_command().
        """
        result: dict[str, Any] = {
            "success": False,
            "command": command,
            "response": None,
            "error": None,
            "transport": "keyboard",
        }

        hwnd = _find_cs2_window()
        if not hwnd:
            result["error"] = "CS2 window not found for keyboard input"
            return result

        try:
            if not _activate_cs2_window(hwnd):
                result["error"] = "Failed to bring CS2 window to foreground"
                return result

            # Open CS2 console (backtick key toggles it)
            _kbd_press(_VK_OEM_3)
            time.sleep(0.25)

            # Select all existing text in the console input and delete it
            _kbd_combo(_VK_CONTROL, _VK_A)
            time.sleep(0.05)
            _kbd_press(_VK_BACK)
            time.sleep(0.05)

            # Copy command to clipboard and paste
            if not _clipboard_set(command):
                result["error"] = "Failed to set clipboard for keyboard command"
                return result

            _kbd_combo(_VK_CONTROL, _VK_V)
            time.sleep(0.1)

            # Execute (Enter)
            _kbd_press(_VK_RETURN)
            time.sleep(0.1)

            # Close the console
            _kbd_press(_VK_OEM_3)
            time.sleep(0.1)

            result["success"] = True
            logger.debug("[kbd] Sent command: %s", command)
            return result

        except Exception as exc:
            result["error"] = f"Keyboard send failed: {exc}"
            logger.error("[kbd] Failed to send command '%s': %s", command, exc)
            return result

    def send_command(self, command: str, read_response: bool = True,
                     _retry: bool = True) -> dict:
        """Send a console command to CS2.

        Tries netcon TCP first. If unavailable, falls back to keyboard
        console input (Win32 keystroke simulation).

        Returns {"success": bool, "command": str, "response": str|None, "error": str|None}
        """
        result: dict[str, Any] = {
            "success": False,
            "command": command,
            "response": None,
            "error": None,
        }

        # ── Try netcon TCP first ──────────────────────────────────────────
        if not self._netcon_connected or not self._netcon_sock:
            if _retry and not self._kbd_fallback:
                logger.info("Netcon not connected, attempting reconnect before command: %s", command)
                reconn = self.connect_netcon()
                if reconn == NetconStatus.CONNECTED:
                    return self.send_command(command, read_response=read_response, _retry=False)
            # Netcon unavailable — UI fallback is manual debug only.
            if self._allow_ui_fallback and (self._kbd_fallback or _find_cs2_window()):
                self._kbd_fallback = True
                return self._send_via_keyboard(command)
            result["error"] = "Netcon command channel unavailable"
            return result

        try:
            payload = (command + "\n").encode("utf-8")
            self._netcon_sock.sendall(payload)
            result["success"] = True

            if read_response:
                time.sleep(self._cfg.get("command_delay", 0.3))
                try:
                    self._netcon_sock.settimeout(1.0)
                    data = self._netcon_sock.recv(8192)
                    result["response"] = data.decode("utf-8", errors="replace").strip()
                except socket.timeout:
                    result["response"] = ""
                finally:
                    self._netcon_sock.settimeout(5.0)

            return result

        except Exception as exc:
            self._netcon_connected = False
            if _retry:
                logger.info("Netcon send failed, attempting reconnect: %s", exc)
                reconn = self.connect_netcon()
                if reconn == NetconStatus.CONNECTED:
                    return self.send_command(command, read_response=read_response, _retry=False)
            # Fall back to keyboard only if debug fallback is explicitly enabled.
            if self._allow_ui_fallback and _find_cs2_window():
                self._kbd_fallback = True
                logger.info("Netcon broken, switching to keyboard fallback (debug mode) for: %s", command)
                return self._send_via_keyboard(command)
            result["success"] = False
            result["error"] = f"Netcon send failed: {exc}"
            logger.error("Netcon command failed (%s): %s", command, exc)
            return result

    def send_commands(self, commands: list[str], delay: float | None = None) -> list[dict]:
        """Send multiple console commands sequentially."""
        delay = delay if delay is not None else self._cfg.get("command_delay", 0.3)
        results = []
        for cmd in commands:
            r = self.send_command(cmd, read_response=True)
            results.append(r)
            if not r["success"]:
                break
            if delay > 0:
                time.sleep(delay)
        return results

    def disconnect_netcon(self):
        """Close netcon connection."""
        if self._netcon_sock:
            try:
                self._netcon_sock.close()
            except Exception:
                pass
        self._netcon_sock = None
        self._netcon_connected = False

    # ── Demo commands ─────────────────────────────────────────────────────

    def _get_cs2_replays_dir(self) -> Path | None:
        """Resolve the CS2 game/csgo/replays directory for safe demo staging."""
        exe = self._cfg.get("cs2_exe")
        if not exe:
            return None
        # cs2.exe lives at <steam>/steamapps/common/Counter-Strike Global Offensive/game/bin/win64/cs2.exe
        # csgo content root is <steam>/steamapps/common/Counter-Strike Global Offensive/game/csgo/
        exe_path = Path(os.path.expandvars(str(exe))).resolve()
        csgo_dir = exe_path.parent.parent.parent / "csgo"
        if csgo_dir.is_dir():
            replays = csgo_dir / "replays"
            replays.mkdir(exist_ok=True)
            return replays
        return None

    def _deterministic_replay_name(self, source: Path) -> str:
        """Build a stable replay filename from demo metadata."""
        slug = re.sub(r"[^a-zA-Z0-9_-]", "_", source.stem).strip("_") or "demo"
        slug = slug[:40]
        stat = source.stat()
        fingerprint_source = (
            f"{source.resolve().as_posix().lower()}|{stat.st_size}|{int(stat.st_mtime)}"
        )
        fingerprint = hashlib.sha1(fingerprint_source.encode("utf-8")).hexdigest()[:12]
        return f"{slug}_{fingerprint}"

    def stage_demo_for_playback(self, demo_path: str, *, worker_tag: str | None = None) -> dict:
        """Stage a demo into a deterministic CS2-accessible location."""
        source = Path(demo_path)
        if not source.is_file():
            return {
                "success": False,
                "error": "demo_not_accessible",
                "message": f"Demo file not found: {demo_path}",
            }

        replays_dir = self._get_cs2_replays_dir()
        if not replays_dir:
            return {
                "success": False,
                "error": "replays_dir_not_found",
                "message": "Unable to resolve CS2 replays directory from cs2_exe",
            }

        configured_subdir = str(self._cfg.get("demo_stage_subdir") or "replays/cs2coach").strip().replace("\\", "/")
        if configured_subdir.startswith("replays/"):
            stage_rel_root = configured_subdir
            local_stage_rel = configured_subdir[len("replays/"):]
        elif configured_subdir == "replays":
            stage_rel_root = "replays"
            local_stage_rel = ""
        else:
            local_stage_rel = configured_subdir.strip("/")
            stage_rel_root = f"replays/{local_stage_rel}" if local_stage_rel else "replays"

        stage_dir = replays_dir if not local_stage_rel else replays_dir / local_stage_rel

        worker_slug = re.sub(r"[^a-zA-Z0-9_-]", "_", (worker_tag or "default"))[:24].strip("_") or "default"
        replay_name = self._deterministic_replay_name(source)
        file_name = f"{replay_name}.dem"
        staged_dir = stage_dir / worker_slug
        staged_dir.mkdir(parents=True, exist_ok=True)
        staged = staged_dir / file_name

        reused = False
        try:
            if staged.exists():
                src_stat = source.stat()
                dst_stat = staged.stat()
                reused = (
                    dst_stat.st_size == src_stat.st_size
                    and int(dst_stat.st_mtime) == int(src_stat.st_mtime)
                )
            if not reused:
                shutil.copy2(str(source), str(staged))
                logger.info("Staged demo to %s", staged)
            else:
                logger.info("Reusing staged demo at %s", staged)
        except Exception as exc:
            return {
                "success": False,
                "error": "demo_staging_failed",
                "message": f"Failed to stage demo: {exc}",
            }

        rel_prefix = stage_rel_root.strip("/")
        rel_parts = [part for part in [rel_prefix, worker_slug, file_name] if part]
        playdemo_path = "/".join(rel_parts)
        return {
            "success": True,
            "source_path": str(source),
            "staged_path": str(staged),
            "stage_root": str(stage_dir),
            "playdemo_path": playdemo_path,
            "replay_name": replay_name,
            "worker_tag": worker_slug,
            "reused": reused,
        }

    def _playdemo_response_failed(self, response: str | None) -> bool:
        """Check if the playdemo command response indicates a real failure.

        CS2 console output often contains benign 'error' or 'failed' tokens
        in unrelated log lines (e.g. shader warnings, asset loading messages).
        Only match phrases that specifically indicate the playdemo command
        itself was rejected or the demo file could not be opened.
        """
        text = str(response or "").lower()
        if not text:
            return False
        # Specific phrases CS2 emits when playdemo actually fails
        playdemo_failure_phrases = [
            "unknown command",
            "couldn't open",
            "could not open",
            "couldn't load",
            "could not load",
            "demo file not found",
            "cdemofile::open",
            "invalid demo",
            "unable to play",
            "cannot open",
            "no such file",
        ]
        return any(phrase in text for phrase in playdemo_failure_phrases)

    def load_demo(self, demo_path: str, *, worker_tag: str | None = None) -> dict:
        """Stage a demo deterministically, then send a relative playdemo command."""
        staged = self.stage_demo_for_playback(demo_path, worker_tag=worker_tag)
        if not staged.get("success"):
            return {
                "success": False,
                "command": "",
                "response": None,
                "error": staged.get("error"),
                "message": staged.get("message"),
                "staged_path": None,
                "playdemo_path": None,
                "replay_name": None,
            }

        rel_path = str(staged.get("playdemo_path"))
        command = f"playdemo \"{rel_path}\""
        result = self.send_command(command, read_response=True)
        result["staged_path"] = staged.get("staged_path")
        result["playdemo_path"] = rel_path
        result["replay_name"] = staged.get("replay_name")
        result["stage_reused"] = bool(staged.get("reused"))
        result["worker_tag"] = staged.get("worker_tag")

        if result.get("success") and self._playdemo_response_failed(result.get("response")):
            result["success"] = False
            result["error"] = "playdemo_command_rejected"
            if not result.get("message"):
                result["message"] = f"playdemo rejected for path: {rel_path}"

        return result

    def demo_goto_tick(self, tick: int) -> dict:
        """Seek to a specific tick in demo playback.

        Uses demo_gototick which is CS2's built-in seek.
        Note: this may not be frame-perfect but gets close.
        """
        return self.send_command(f"demo_gototick {tick}")

    def demo_goto_round(self, round_number: int, round_start_tick: int) -> dict:
        """Seek to the start of a specific round.

        Uses the round's known start tick from parsed demo data.
        """
        return self.send_command(f"demo_gototick {round_start_tick}")

    def demo_pause(self) -> dict:
        """Pause demo playback."""
        return self.send_command("demo_pause")

    def demo_resume(self) -> dict:
        """Resume demo playback."""
        return self.send_command("demo_resume")

    def demo_timescale(self, scale: float) -> dict:
        """Set demo playback speed (1.0 = normal)."""
        return self.send_command(f"demo_timescale {scale}")

    def is_demo_playing(self) -> bool:
        """Check if a demo is currently loaded.

        When using netcon TCP, probes with ``demo_info`` and ``status``
        commands and inspects the response text.  When using the keyboard
        fallback (no response available), always returns True so the
        caller falls through to time-based waiting.
        """
        # Keyboard mode (debug only) — no way to read responses, assume demo loaded.
        if self._allow_ui_fallback and self._kbd_fallback and not self._netcon_connected:
            return True

        res = self.send_command("demo_info", read_response=True)
        resp = (res.get("response") or "").lower()
        if not res.get("success"):
            return False
        # CS2 with no demo loaded returns empty or very short generic text
        if len(resp.strip()) > 5:
            return True
        # Try another probe — status command
        res2 = self.send_command("status", read_response=True)
        resp2 = (res2.get("response") or "").lower()
        return "demo" in resp2 or "gotv" in resp2 or "playdemo" in resp2

    # ── Camera / observer commands ────────────────────────────────────────

    def set_spec_mode(self, mode: str) -> dict:
        """Set spectator camera mode.

        Supported mode strings:
          "first_person" → spec_mode 4
          "third_person" → spec_mode 5
          "freecam"      → spec_mode 6  (noclip/roaming)
          "spec_follow"  → spec_mode 5 + spec_autodirector 0
        """
        mode_map = {
            "first_person": "spec_mode 4",
            "third_person": "spec_mode 5",
            "freecam": "spec_mode 6",
            "spec_follow": "spec_mode 5",
        }
        cmd = mode_map.get(mode)
        if not cmd:
            return {"success": False, "command": "", "response": None, "error": f"Unknown spec mode: {mode}"}
        return self.send_command(cmd)

    def set_spec_player(self, player_name: str) -> dict:
        """Attempt to spectate a specific player by name.

        Uses spec_player_by_name which is available in GOTV demo mode.
        """
        return self.send_command(f"spec_player_by_name \"{player_name}\"")

    def set_hud_mode(self, hud_mode: str) -> dict:
        """Apply HUD visibility settings.

        "clean"     → cl_drawhud 0
        "default"   → cl_drawhud 1
        "cinematic" → cl_drawhud 1; cl_draw_only_deathnotices 1
        """
        cmds_map: dict[str, list[str]] = {
            "clean": ["cl_drawhud 0"],
            "default": ["cl_drawhud 1", "cl_draw_only_deathnotices 0"],
            "cinematic": ["cl_drawhud 1", "cl_draw_only_deathnotices 1"],
        }
        cmds = cmds_map.get(hud_mode)
        if not cmds:
            return {"success": False, "command": "", "response": None, "error": f"Unknown HUD mode: {hud_mode}"}
        results = self.send_commands(cmds)
        return results[-1] if results else {"success": False, "command": "", "response": None, "error": "No commands"}

    def apply_camera_strategy(self, camera_mode: str, observer_mode: str, hud_mode: str,
                               pov_player: str | None = None) -> dict:
        """Apply a full camera strategy (camera + observer + HUD + optional player lock).

        For player_pov mode, uses a deterministic command sequence:
          1. spec_autodirector 0    — prevent auto-director from overriding
          2. spec_mode 4            — first-person spectator
          3. spec_player_by_name    — select the target player
          4. redundant confirmation — re-send autodirector + mode + player

        Returns a summary of what was applied and what failed.
        """
        results: dict[str, Any] = {
            "requested": {
                "camera_mode": camera_mode,
                "observer_mode": observer_mode,
                "hud_mode": hud_mode,
                "pov_player": pov_player,
            },
            "applied": {},
            "warnings": [],
            "success": True,
        }

        # Set HUD first (least likely to interfere with camera)
        hud_result = self.set_hud_mode(hud_mode)
        if hud_result.get("success"):
            results["applied"]["hud_mode"] = hud_mode
        else:
            results["warnings"].append(f"hud_mode failed: {hud_result.get('error')}")
            results["applied"]["hud_mode"] = None

        # Set POV player if camera_mode is player_pov
        if camera_mode == "player_pov" and pov_player:
            # ── Phase 1: Prepare spectator environment ──────────────────
            # Disable auto-director FIRST — prevents CS2 from overriding
            # our player selection with its own camera logic.
            self.send_command("spec_autodirector 0", read_response=False)
            time.sleep(0.3)

            # Force first-person spectator mode
            self.send_command("spec_mode 4", read_response=False)
            time.sleep(0.3)

            # ── Phase 2: Select the target player ───────────────────────
            pov_result = self.set_spec_player(pov_player)
            if not pov_result.get("success"):
                results["success"] = False
                results["error"] = f"player_pov_lock_failed: {pov_result.get('error')}"
                results["warnings"].append(f"spec_player failed: {pov_result.get('error')}")
                results["applied"]["camera_mode"] = None
                results["applied"]["pov_player"] = None
                return results
            time.sleep(0.5)

            # ── Phase 3: Redundant confirmation ─────────────────────────
            # CS2 can silently drop commands during state transitions.
            # Re-send the critical commands to ensure they stick.
            self.send_command("spec_mode 4", read_response=False)
            time.sleep(0.2)
            self.send_command("spec_autodirector 0", read_response=False)
            time.sleep(0.2)
            self.send_command(f"spec_player_by_name \"{pov_player}\"", read_response=False)
            time.sleep(0.3)
            # Final mode confirmation after player re-selection
            self.send_command("spec_mode 4", read_response=False)
            time.sleep(0.2)

            results["applied"]["camera_mode"] = "player_pov"
            results["applied"]["pov_player"] = pov_player
            results["applied"]["observer_mode"] = "first_person"

        elif camera_mode == "player_pov" and not pov_player:
            # player_pov requested but no player name — hard failure
            results["success"] = False
            results["error"] = "player_pov_requested_but_no_pov_player"
            results["applied"]["camera_mode"] = None
            results["applied"]["pov_player"] = None
            return results
        elif camera_mode == "freecam":
            fc_result = self.set_spec_mode("freecam")
            if fc_result.get("success"):
                results["applied"]["camera_mode"] = "freecam"
            else:
                results["warnings"].append(f"freecam failed: {fc_result.get('error')}")
                results["applied"]["camera_mode"] = "observer_auto"
            # Set observer mode for non-player_pov modes
            obs_result = self.set_spec_mode(observer_mode)
            if obs_result.get("success"):
                results["applied"]["observer_mode"] = observer_mode
            else:
                results["applied"]["observer_mode"] = None
        else:
            results["applied"]["camera_mode"] = camera_mode
            # Set observer mode
            obs_result = self.set_spec_mode(observer_mode)
            if obs_result.get("success"):
                results["applied"]["observer_mode"] = observer_mode
            else:
                results["warnings"].append(f"observer_mode failed: {obs_result.get('error')}")
                results["applied"]["observer_mode"] = None

        # For non-player_pov modes, partial warnings are acceptable
        if results["warnings"]:
            results["success"] = len(results["warnings"]) < 3

        return results

    # ── Cleanup ───────────────────────────────────────────────────────────

    def close(self):
        """Release resources. Does NOT terminate CS2."""
        self.disconnect_netcon()
