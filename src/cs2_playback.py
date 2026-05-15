"""
cs2_playback.py
Playback preparation layer for CS2 demo clips.

Handles the full sequence:
1. Ensure CS2 is running
2. Connect via netcon
3. Stage and load the demo
4. Seek to the correct tick
5. Pause at the right moment
6. Report structured playback state
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from src.cs2_config import CS2Config
from src.cs2_controller import CS2Controller, CS2ControlError

log = logging.getLogger(__name__)

TICK_RATE = 64
_INVALID_PROBE_TOKENS = (
    "unknown command",
    "not connected",
    "must be playing",
    "not currently playing",
    "no demo",
)
_DEMO_PLAYING_TOKEN = "playing demo from"
_DEMO_QUEUE_PLAYING_TOKEN = "queuenewrequest( playing demo"
_DEMO_REQUESTING_PLAYBACK_TOKEN = "requesting playback of"
_DEMO_GAME_LOOP_TOKEN = "switchtoloop game requested"
_DEMO_CLIENT_CONNECT_TOKEN = "ccreategameclientjob creating client connection"
_DEMO_PROCESS_SERVERINFO_TOKEN = "cnetworkgameclient::processserverinfo"
_DEMO_SKIP_FINISHED_TOKEN = "demo skipping finished at tick"
_DEMO_TIMESCALE_PATTERNS = (
    re.compile(r'"demo_timescale"\s*=\s*"(?P<value>-?\d+(?:\.\d+)?)"', re.IGNORECASE),
    re.compile(r'\bdemo_timescale\b[^-\d]*(?P<value>-?\d+(?:\.\d+)?)', re.IGNORECASE),
)
_PAUSED_TICK_RE = re.compile(r"paused on tick\s+(?P<tick>\d+)", re.IGNORECASE)

PLAYBACK_NOT_LOADED = "NOT_LOADED"
PLAYBACK_LOADING = "LOADING"
PLAYBACK_PLAYING = "PLAYING"
PLAYBACK_PLAYING_SETTLING = "PLAYING_SETTLING"
PLAYBACK_SEEKING = "SEEKING"
PLAYBACK_SEEK_FAILED = "SEEK_FAILED"
PLAYBACK_PAUSED_READY = "PAUSED_READY"

_LOAD_PROGRESS_MARKERS = (
    _DEMO_REQUESTING_PLAYBACK_TOKEN,
    _DEMO_QUEUE_PLAYING_TOKEN,
    _DEMO_GAME_LOOP_TOKEN,
    _DEMO_CLIENT_CONNECT_TOKEN,
)
_LOAD_PLAYING_MARKERS = (
    _DEMO_PLAYING_TOKEN,
    _DEMO_PROCESS_SERVERINFO_TOKEN,
    _DEMO_SKIP_FINISHED_TOKEN,
)
_SEEK_PROGRESS_MARKERS = (
    "demo skipping: skipping to demo tick",
    "demo skipping paused after",
    "demo skipping flushing last",
    _DEMO_SKIP_FINISHED_TOKEN,
)
_FATAL_RUNTIME_MARKERS = (
    "fatal error",
    "copynewentity",
    "invalid class index",
)
_UNKNOWN_COMMAND_TOKEN = "unknown command"
_SEEK_STAGE_DELTA_TICKS = 6000
_SEEK_PAUSE_DELAY_S = 0.7
_INCREMENTAL_PROBE_OFFSETS = (1000, 2000, 4000, 6000)
_MIRV_SKIP_SEEK_TOLERANCE_TICKS = 16
_MIRV_SKIP_MAX_REFINEMENT_ITERATIONS = 4
_NO_MOVEMENT_TOLERANCE_TICKS = 4


class PlaybackError(Exception):
    """Raised when playback preparation fails irrecoverably."""


class PlaybackRuntimeError(PlaybackError):
    """Raised when runtime output shows a fatal engine/playback condition."""

    def __init__(
        self,
        message: str,
        *,
        marker: str = "",
        snippet: str = "",
        source: str = "",
        failure_code: str = "seek_runtime_failure",
        transport: str = "",
        target_tick: int | None = None,
        strategy_name: str = "",
        stage_index: int | None = None,
        probe_command: str = "",
    ):
        super().__init__(message)
        self.marker = marker
        self.snippet = snippet
        self.source = source
        self.failure_code = failure_code
        self.transport = transport
        self.target_tick = target_tick
        self.strategy_name = strategy_name
        self.stage_index = stage_index
        self.probe_command = probe_command


@dataclass
class PlaybackRequest:
    """What we want to prepare."""
    demo_path: str
    round_number: int
    start_tick: int
    anchor_tick: int
    end_tick: int
    round_start_tick: int = 0
    round_end_tick: int = 0
    freeze_end_tick: int = 0

    @property
    def clip_duration_s(self) -> float:
        return max(0, (self.end_tick - self.start_tick)) / TICK_RATE


@dataclass
class PlaybackResult:
    """Structured result of playback preparation."""
    status: str = "pending"  # pending, ready, failed

    # What was requested
    requested_start_tick: int = 0
    requested_anchor_tick: int = 0
    requested_end_tick: int = 0
    round_number: int = 0

    # What was actually achieved
    actual_seek_tick: int = 0
    demo_loaded: bool = False
    demo_path_used: str = ""
    playdemo_path: str = ""
    paused_at_start: bool = False
    observed_pause_tick: int = 0
    playback_state: str = PLAYBACK_NOT_LOADED

    # CS2 state
    cs2_running: bool = False
    cs2_pid: int | None = None
    netcon_connected: bool = False

    # Timing
    started_at: float = 0.0
    completed_at: float = 0.0
    duration_ms: float = 0.0

    # Errors
    failure_code: str = ""
    error: str = ""
    invalid_seek_boundaries: list[dict[str, Any]] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["duration_ms"] = round(self.duration_ms, 1)
        return d


@dataclass
class PlaybackController:
    """Orchestrates demo playback preparation."""
    config: CS2Config
    cs2: CS2Controller | None = None

    def __post_init__(self):
        if self.cs2 is None:
            self.cs2 = CS2Controller(config=self.config)
        self._playback_state = PLAYBACK_NOT_LOADED
        self._invalid_seek_boundaries: list[dict[str, Any]] = []

    def _emit_playback_event(self, message: str, **fields: Any) -> None:
        suffix = ""
        if fields:
            serialized = json.dumps(fields, sort_keys=True, default=str)
            suffix = f" | {serialized}"
        log.info("[PLAYBACK] %s%s", message, suffix)
        observer = getattr(self.cs2, "command_observer", None)
        if observer is not None:
            try:
                observer(f"[PLAYBACK] {message}{suffix}", "")
            except Exception:
                pass

    def _effective_load_timeout_s(self) -> float:
        base_timeout = max(self.config.demo_ready_settle_s + 2.0, 12.0)
        if (self.config.render_backend or "").strip().lower() == "hlae":
            configured = float(self.config.hlae_render_load_timeout_s or 0)
            return max(base_timeout, configured, 30.0)
        return base_timeout

    def _set_playback_state(self, result: PlaybackResult, state: str, *, reason: str = "") -> None:
        self._playback_state = state
        result.playback_state = state
        if reason:
            self._emit_playback_event(f"state -> {state}", reason=reason)

    @staticmethod
    def _extract_numeric_demo_timescale(command: str, raw: str) -> float | None:
        text = (raw or "").strip()
        if not text:
            return None
        lowered = text.lower()
        if "demo_timescale" not in lowered and command != "demo_timescale":
            return None
        for pattern in _DEMO_TIMESCALE_PATTERNS:
            match = pattern.search(text)
            if not match:
                continue
            try:
                return float(match.group("value"))
            except (ValueError, TypeError):
                return None
        if command == "demo_timescale":
            stripped = text.strip().strip('"')
            if re.fullmatch(r"-?\d+(?:\.\d+)?", stripped):
                return float(stripped)
        return None

    @staticmethod
    def _extract_paused_tick(raw: str) -> int | None:
        match = _PAUSED_TICK_RE.search(raw or "")
        if not match:
            return None
        try:
            return int(match.group("tick"))
        except (TypeError, ValueError):
            return None

    def _inspect_playback_response(self, command: str, raw: str) -> dict[str, Any]:
        text = raw or ""
        lowered = text.lower()
        queue_detected = _DEMO_QUEUE_PLAYING_TOKEN in lowered
        playing_detected = _DEMO_PLAYING_TOKEN in lowered
        progress_markers = [marker for marker in _LOAD_PROGRESS_MARKERS if marker in lowered]
        playing_markers = [marker for marker in _LOAD_PLAYING_MARKERS if marker in lowered]
        seek_progress_markers = [marker for marker in _SEEK_PROGRESS_MARKERS if marker in lowered]
        fatal_markers = [marker for marker in _FATAL_RUNTIME_MARKERS if marker in lowered]
        paused_tick = self._extract_paused_tick(text)
        numeric_timescale = self._extract_numeric_demo_timescale(command, text)
        invalid_tokens = [
            token for token in _INVALID_PROBE_TOKENS
            if token in lowered
        ]

        observed_state = self._playback_state
        if paused_tick is not None:
            observed_state = PLAYBACK_PAUSED_READY
        elif playing_detected or playing_markers or numeric_timescale is not None:
            observed_state = PLAYBACK_PLAYING
        elif self._playback_state == PLAYBACK_PLAYING_SETTLING and not invalid_tokens and not fatal_markers:
            # During the short post-load settle window CS2 often emits benign
            # engine chatter instead of a fresh playback marker. If we were
            # already in PLAYING and the command responses remain non-fatal and
            # non-negative, treat that as "still playing" rather than timing out.
            observed_state = PLAYBACK_PLAYING
        elif (queue_detected or progress_markers) and self._playback_state == PLAYBACK_NOT_LOADED:
            observed_state = PLAYBACK_LOADING
        elif progress_markers and self._playback_state == PLAYBACK_LOADING:
            observed_state = PLAYBACK_LOADING

        return {
            "command": command,
            "raw": text.strip()[:400],
            "queue_detected": queue_detected,
            "playing_detected": playing_detected,
            "progress_markers": progress_markers,
            "playing_markers": playing_markers,
            "seek_progress_markers": seek_progress_markers,
            "fatal_markers": fatal_markers,
            "paused_tick": paused_tick,
            "numeric_timescale": numeric_timescale,
            "invalid_tokens": invalid_tokens,
            "observed_state": observed_state,
        }

    def _poll_playback_runtime(
        self,
        *,
        commands: list[str],
        interval_s: float,
        timeout_s: float,
        accept_states: set[str],
        result: PlaybackResult,
        label: str,
        extend_on_progress: bool = False,
        progress_grace_s: float = 6.0,
        max_extensions: int = 2,
        require_positive_signal: bool = False,
        fail_on_fatal: bool = True,
    ) -> dict[str, Any]:
        deadline = time.time() + timeout_s
        attempts = 0
        last_poll: dict[str, Any] = {"details": [], "state": self._playback_state}
        load_detected_logged = self._playback_state in {PLAYBACK_LOADING, PLAYBACK_PLAYING, PLAYBACK_PAUSED_READY}
        extensions_used = 0
        last_progress_signal = ""
        last_positive_signal = ""
        last_negative_signal = ""
        last_fatal_signal = ""
        last_progress_at = 0.0
        start_ts = time.time()

        while time.time() < deadline:
            attempts += 1
            details: list[dict[str, Any]] = []
            observed_state = self._playback_state
            pause_tick: int | None = None
            queue_detected = False

            for cmd in commands:
                raw = self.cs2.exec_command(cmd)
                inspected = self._inspect_playback_response(cmd, raw)
                details.append(inspected)

                if inspected["fatal_markers"]:
                    last_fatal_signal = inspected["fatal_markers"][0]
                    if fail_on_fatal:
                        self._emit_playback_event(
                            "observed fatal marker",
                            label=label,
                            command=cmd,
                            marker=last_fatal_signal,
                        )
                        raise PlaybackRuntimeError(
                            f"{label} runtime error: {last_fatal_signal}",
                            marker=last_fatal_signal,
                            snippet=inspected["raw"],
                        )

                if inspected["queue_detected"] or inspected["progress_markers"]:
                    queue_detected = True
                    marker = inspected["progress_markers"][0] if inspected["progress_markers"] else _DEMO_QUEUE_PLAYING_TOKEN
                    if marker != last_progress_signal:
                        last_progress_signal = marker
                        self._emit_playback_event("observed queued marker", marker=marker, command=cmd)
                    last_progress_at = time.time()
                if inspected["playing_markers"]:
                    marker = inspected["playing_markers"][0]
                    if marker != last_positive_signal:
                        last_positive_signal = marker
                        self._emit_playback_event("observed playing marker", marker=marker, command=cmd)
                if inspected["seek_progress_markers"]:
                    marker = inspected["seek_progress_markers"][0]
                    if marker != last_positive_signal:
                        last_positive_signal = marker
                        self._emit_playback_event("observed seek marker", marker=marker, command=cmd)
                if inspected["numeric_timescale"] is not None:
                    signal = f"demo_timescale={inspected['numeric_timescale']}"
                    if signal != last_positive_signal:
                        last_positive_signal = signal
                        self._emit_playback_event("observed numeric timescale", value=inspected["numeric_timescale"], command=cmd)
                if inspected["paused_tick"] is not None:
                    pause_tick = inspected["paused_tick"]
                    signal = f"paused_tick={pause_tick}"
                    if signal != last_positive_signal:
                        last_positive_signal = signal
                        self._emit_playback_event("observed paused tick", tick=pause_tick, command=cmd)
                if inspected["invalid_tokens"]:
                    last_negative_signal = inspected["invalid_tokens"][0]
                if inspected["observed_state"] == PLAYBACK_PAUSED_READY:
                    observed_state = PLAYBACK_PAUSED_READY
                elif inspected["observed_state"] == PLAYBACK_PLAYING and observed_state != PLAYBACK_PAUSED_READY:
                    observed_state = PLAYBACK_PLAYING
                elif inspected["observed_state"] == PLAYBACK_LOADING and observed_state == PLAYBACK_NOT_LOADED:
                    observed_state = PLAYBACK_LOADING

            if queue_detected and not load_detected_logged:
                self._set_playback_state(result, PLAYBACK_LOADING, reason="load request observed")
                self._emit_playback_event("load detected")
                load_detected_logged = True

            if observed_state != self._playback_state and observed_state in {
                PLAYBACK_LOADING,
                PLAYBACK_PLAYING,
                PLAYBACK_PAUSED_READY,
            }:
                reason = label.lower()
                self._set_playback_state(result, observed_state, reason=reason)
                if observed_state == PLAYBACK_PLAYING:
                    self._emit_playback_event("demo entered playing state", attempt=attempts)

            if pause_tick is not None:
                result.observed_pause_tick = pause_tick

            last_poll = {
                "attempts": attempts,
                "details": details,
                "state": observed_state,
                "pause_tick": pause_tick,
                "label": label,
                "elapsed_s": round(time.time() - start_ts, 2),
                "last_progress_signal": last_progress_signal,
                "last_positive_signal": last_positive_signal,
                "last_negative_signal": last_negative_signal,
                "last_fatal_signal": last_fatal_signal,
                "extensions_used": extensions_used,
            }

            current_cycle_positive = bool(last_positive_signal)
            if observed_state in accept_states and (not require_positive_signal or current_cycle_positive):
                return last_poll

            if extend_on_progress and last_progress_at:
                remaining = deadline - time.time()
                if remaining <= max(interval_s * 2, 1.0) and extensions_used < max_extensions:
                    deadline += progress_grace_s
                    extensions_used += 1
                    self._emit_playback_event(
                        "load progress observed; extending wait",
                        elapsed_s=round(time.time() - start_ts, 2),
                        extensions_used=extensions_used,
                        last_progress_signal=last_progress_signal,
                        new_timeout_deadline_s=round(deadline - start_ts, 2),
                    )

            self._emit_playback_event(
                f"{label} wait elapsed={round(time.time() - start_ts, 2)}s",
                label=label,
                last_known_state=observed_state,
                last_positive_signal=last_positive_signal,
                last_negative_signal=last_negative_signal,
                last_fatal_signal=last_fatal_signal,
            )

            time.sleep(interval_s)

        raise PlaybackError(
            f"{label} did not reach {sorted(accept_states)} within {round(time.time() - start_ts, 1):.1f}s"
            + (f" (last_positive={last_positive_signal})" if last_positive_signal else "")
            + (f" (last_progress={last_progress_signal})" if last_progress_signal else "")
            + (f" (last_negative={last_negative_signal})" if last_negative_signal else "")
            + (f" (last_fatal={last_fatal_signal})" if last_fatal_signal else "")
        )

    def _pause_tolerance_ticks(self) -> int:
        return max(512, int(max(self.config.post_seek_settle_s, 2.0) * TICK_RATE * 2))

    def _seek_strategy_tolerance_ticks(self) -> int:
        return max(self._pause_tolerance_ticks(), 768)

    @staticmethod
    def _is_mirv_skip_command(command: str) -> bool:
        return (command or "").strip().lower().startswith("mirv_skip tick to ")

    def _seek_tolerance_ticks_for_command(self, command: str) -> int:
        if self._is_mirv_skip_command(command):
            return _MIRV_SKIP_SEEK_TOLERANCE_TICKS
        return self._seek_strategy_tolerance_ticks()

    @staticmethod
    def _classify_seek_outcome(
        *,
        paused_tick: int | None,
        expected_tick: int,
        tolerance: int,
        anchor_tick_before_seek: int | None,
    ) -> str:
        if paused_tick is None:
            return "seek_state_unreadable"
        if abs(paused_tick - expected_tick) <= tolerance:
            return "seek_confirmed"
        if paused_tick > expected_tick + tolerance:
            return "seek_overshoot"
        if anchor_tick_before_seek is not None and paused_tick == anchor_tick_before_seek:
            return "seek_no_movement"
        return "seek_no_movement"

    @staticmethod
    def _extract_tick_from_seek_command(command: str) -> int | None:
        try:
            return int((command or "").strip().split()[-1])
        except (ValueError, IndexError):
            return None

    @staticmethod
    def _seek_transport_from_command(command: str) -> str:
        normalized = (command or "").strip().lower()
        if normalized.startswith("mirv_skip tick to "):
            return "mirv_skip_tick"
        if normalized.startswith("demo_gototick "):
            return "demo_gototick"
        return normalized.split(" ", 1)[0] if normalized else "unknown"

    def _record_invalid_seek_boundary(
        self,
        result: PlaybackResult,
        *,
        transport: str,
        anchor_tick: int | None,
        target_tick: int | None,
        strategy_name: str,
        stage_index: int | None,
        source: str,
        marker: str,
    ) -> None:
        boundary = {
            "transport": transport,
            "anchor_tick": anchor_tick,
            "target_tick": target_tick,
            "strategy": strategy_name,
            "stage": stage_index,
            "source": source,
            "marker": marker,
        }
        if boundary in self._invalid_seek_boundaries:
            return
        self._invalid_seek_boundaries.append(boundary)
        result.invalid_seek_boundaries.append(boundary)
        self._emit_playback_event("invalid seek boundary recorded", **boundary)

    def _is_known_invalid_seek_boundary(
        self,
        *,
        transport: str,
        anchor_tick: int | None,
        target_tick: int | None,
    ) -> bool:
        for boundary in self._invalid_seek_boundaries:
            if (
                boundary.get("transport") == transport
                and boundary.get("anchor_tick") == anchor_tick
                and boundary.get("target_tick") == target_tick
            ):
                return True
        return False

    def _compute_mirv_skip_correction_tick(
        self,
        *,
        target_tick: int,
        command_tick: int,
        observed_tick: int,
        visited_ticks: set[int],
    ) -> int | None:
        # Estimate the current transport offset and counter it directly first.
        estimated_offset = observed_tick - command_tick
        candidate = max(0, target_tick - estimated_offset)

        if candidate == command_tick or candidate in visited_ticks:
            delta = observed_tick - target_tick
            if delta > 0:
                candidate = max(0, command_tick - max(abs(delta) // 2, _MIRV_SKIP_SEEK_TOLERANCE_TICKS))
            else:
                candidate = max(0, command_tick + max(abs(delta) // 2, _MIRV_SKIP_SEEK_TOLERANCE_TICKS))

        if candidate in visited_ticks or candidate == command_tick:
            return None
        return candidate

    def _probe_current_tick_before_seek(self, result: PlaybackResult) -> int | None:
        self._emit_playback_event("probing current playback tick before seek")
        pause_response = self.cs2.exec_command("demo_pause")
        paused_tick = self._extract_paused_tick(pause_response)
        if paused_tick is not None:
            self._emit_playback_event("pre-seek anchor pause", tick=paused_tick)
            self.cs2.resume_demo()
            time.sleep(0.25)
            self._set_playback_state(result, PLAYBACK_PLAYING, reason="pre_seek_anchor_resume")
            return paused_tick
        self._emit_playback_event(
            "pre-seek anchor pause missing tick",
            raw=(pause_response or "").strip()[:200],
        )
        return None

    @staticmethod
    def _build_stage_ticks(current_tick: int | None, target_tick: int, max_delta: int = _SEEK_STAGE_DELTA_TICKS) -> list[int]:
        if current_tick is None or target_tick <= 0:
            return [target_tick]
        if target_tick <= current_tick + max_delta:
            return [target_tick]
        ticks: list[int] = []
        next_tick = current_tick + max_delta
        while next_tick < target_tick:
            ticks.append(next_tick)
            next_tick += max_delta
        if not ticks or ticks[-1] != target_tick:
            ticks.append(target_tick)
        return ticks

    @staticmethod
    def _build_incremental_probe_ticks(anchor_tick: int | None, target_tick: int) -> list[int]:
        if anchor_tick is None or target_tick <= 0 or target_tick <= anchor_tick:
            return []
        ticks: list[int] = []
        for offset in _INCREMENTAL_PROBE_OFFSETS:
            probe_tick = min(target_tick, anchor_tick + offset)
            if probe_tick > anchor_tick and probe_tick not in ticks:
                ticks.append(probe_tick)
        return ticks

    def _execute_seek_command(self, command: str, strategy_name: str, *, stage_index: int | None = None, stage_tick: int | None = None) -> dict[str, Any]:
        fields: dict[str, Any] = {"strategy": strategy_name, "command": command}
        if stage_index is not None:
            fields["stage"] = stage_index
        if stage_tick is not None:
            fields["tick"] = stage_tick
        self._emit_playback_event("seek strategy command", **fields)
        response = self.cs2.exec_command(command)
        inspected = self._inspect_playback_response(command.split(" ", 1)[0], response)
        transport = self._seek_transport_from_command(command)
        self._emit_playback_event(
            "seek command response",
            strategy=strategy_name,
            stage=stage_index,
            tick=stage_tick,
            command=command,
            response=(response or "").strip(),
            seek_markers=inspected["seek_progress_markers"],
            invalid_tokens=inspected["invalid_tokens"],
            fatal_markers=inspected["fatal_markers"],
        )
        if inspected["fatal_markers"]:
            self._emit_playback_event(
                "seek command fatal detected",
                strategy=strategy_name,
                stage=stage_index,
                transport=transport,
                target_tick=stage_tick,
                source="seek_command_response",
                marker=inspected["fatal_markers"][0],
            )
            stage_label = f"{strategy_name} stage {stage_index}" if stage_index is not None else strategy_name
            raise PlaybackRuntimeError(
                f"{stage_label} runtime error: {inspected['fatal_markers'][0]}",
                marker=inspected["fatal_markers"][0],
                snippet=inspected["raw"],
                source="seek_command_response",
                failure_code="seek_command_runtime_failure",
                transport=transport,
                target_tick=stage_tick,
                strategy_name=strategy_name,
                stage_index=stage_index,
            )
        if inspected["invalid_tokens"] and _UNKNOWN_COMMAND_TOKEN in inspected["invalid_tokens"]:
            raise PlaybackError(f"{strategy_name} unavailable: unknown command")
        return {"response": response, "inspected": inspected, "transport": transport}

    def _run_immediate_post_seek_probes(
        self,
        *,
        strategy_name: str,
        stage_index: int | None,
        expected_tick: int,
        result: PlaybackResult,
        seek_command: str,
    ) -> dict[str, Any]:
        probes: list[dict[str, Any]] = []
        probe_commands = ("demo_pause", "spec_mode", "demo_timescale", "spec_autodirector")
        readable = False
        first_demo_pause_tick: int | None = None
        transport = self._seek_transport_from_command(seek_command)

        for probe_command in probe_commands:
            self._emit_playback_event(
                "post-seek probe begin",
                strategy=strategy_name,
                stage=stage_index,
                probe=probe_command,
            )
            raw = self.cs2.exec_command(probe_command)
            inspected = self._inspect_playback_response(probe_command, raw)
            if inspected["fatal_markers"]:
                self._emit_playback_event(
                    "post-seek probe response",
                    strategy=strategy_name,
                    stage=stage_index,
                    probe=probe_command,
                    response=inspected["raw"],
                    fatal_markers=inspected["fatal_markers"],
                )
                self._emit_playback_event(
                    "seek post-probe fatal detected",
                    strategy=strategy_name,
                    stage=stage_index,
                    transport=transport,
                    target_tick=expected_tick,
                    source="post_seek_probe",
                    probe=probe_command,
                    marker=inspected["fatal_markers"][0],
                )
                raise PlaybackRuntimeError(
                    f"{strategy_name} post-seek probe runtime error on {probe_command}: {inspected['fatal_markers'][0]}",
                    marker=inspected["fatal_markers"][0],
                    snippet=inspected["raw"],
                    source="post_seek_probe",
                    failure_code="seek_post_probe_runtime_failure",
                    transport=transport,
                    target_tick=expected_tick,
                    strategy_name=strategy_name,
                    stage_index=stage_index,
                    probe_command=probe_command,
                )

            probe_readable = bool((raw or "").strip()) and not inspected["invalid_tokens"]
            readable = readable or probe_readable or inspected["numeric_timescale"] is not None or inspected["paused_tick"] is not None
            probe_info = {
                "command": probe_command,
                "response": (raw or "").strip(),
                "readable": probe_readable,
                "observed_state": inspected["observed_state"],
                "numeric_timescale": inspected["numeric_timescale"],
                "paused_tick": inspected["paused_tick"],
                "seek_markers": inspected["seek_progress_markers"],
                "playing_markers": inspected["playing_markers"],
                "invalid_tokens": inspected["invalid_tokens"],
            }
            probes.append(probe_info)
            self._emit_playback_event(
                "post-seek probe response",
                strategy=strategy_name,
                stage=stage_index,
                probe=probe_command,
                response=(raw or "").strip(),
                readable=probe_readable,
                observed_state=inspected["observed_state"],
                numeric_timescale=inspected["numeric_timescale"],
                paused_tick=inspected["paused_tick"],
                seek_markers=inspected["seek_progress_markers"],
                playing_markers=inspected["playing_markers"],
                invalid_tokens=inspected["invalid_tokens"],
            )
            if inspected["paused_tick"] is not None:
                result.observed_pause_tick = inspected["paused_tick"]
                if probe_command == "demo_pause" and first_demo_pause_tick is None:
                    first_demo_pause_tick = inspected["paused_tick"]

        return {
            "probes": probes,
            "probe_readable": readable,
            "first_demo_pause_tick": first_demo_pause_tick,
        }

    def _attempt_mirv_skip_refinement(
        self,
        result: PlaybackResult,
        *,
        expected_tick: int,
        strategy_name: str,
        stage_index: int | None,
        is_final_stage: bool,
        anchor_tick_before_seek: int | None,
        initial_command_tick: int,
        initial_paused_tick: int,
        initial_attempts: list[dict[str, Any]],
        initial_seek_response: str,
        initial_immediate_probes: list[dict[str, Any]],
        initial_first_pause_readable: bool,
        initial_second_pause_readable: bool,
    ) -> dict[str, Any]:
        tolerance = self._seek_tolerance_ticks_for_command("mirv_skip tick to 0")
        visited_command_ticks = {initial_command_tick}
        previous_error = abs(initial_paused_tick - expected_tick)
        best_payload: dict[str, Any] | None = None

        self._emit_playback_event(
            "mirv_skip seek begin",
            strategy=strategy_name,
            stage=stage_index,
            reason="seek_overshoot",
            target_tick=expected_tick,
            observed_tick=initial_paused_tick,
            tolerance_ticks=tolerance,
        )

        current_command_tick = initial_command_tick
        current_observed_tick = initial_paused_tick
        refinement_iterations: list[dict[str, Any]] = []

        for iteration in range(1, _MIRV_SKIP_MAX_REFINEMENT_ITERATIONS + 1):
            corrected_tick = self._compute_mirv_skip_correction_tick(
                target_tick=expected_tick,
                command_tick=current_command_tick,
                observed_tick=current_observed_tick,
                visited_ticks=visited_command_ticks,
            )
            if corrected_tick is None:
                break

            visited_command_ticks.add(corrected_tick)
            error = current_observed_tick - expected_tick
            self._emit_playback_event(
                "corrective seek iteration",
                strategy=strategy_name,
                stage=stage_index,
                iteration=iteration,
                target_tick=expected_tick,
                command_tick=current_command_tick,
                observed_tick=current_observed_tick,
                error_ticks=error,
                corrected_target=corrected_tick,
            )

            if result.playback_state == PLAYBACK_PAUSED_READY:
                self.cs2.resume_demo()
                time.sleep(0.25)
                self._set_playback_state(result, PLAYBACK_PLAYING, reason=f"{strategy_name}_refine_resume_{iteration}")

            correction_command = f"mirv_skip tick to {corrected_tick}"
            command_result = self._execute_seek_command(
                correction_command,
                strategy_name,
                stage_index=stage_index,
                stage_tick=corrected_tick,
            )
            seek_acknowledged = bool(command_result["inspected"]["seek_progress_markers"])
            pause_result = self._pause_after_seek(
                result,
                expected_tick=expected_tick,
                strategy_name=strategy_name,
                stage_index=stage_index,
                is_final_stage=is_final_stage,
                anchor_tick_before_seek=anchor_tick_before_seek,
                seek_command=correction_command,
                seek_response=command_result["inspected"]["raw"],
                seek_acknowledged=seek_acknowledged,
                raise_on_nonfatal_failure=False,
            )

            iteration_payload = {
                "iteration": iteration,
                "command_tick": corrected_tick,
                "pause_tick": pause_result["paused_tick"],
                "outcome": pause_result["outcome"],
                "seek_response": pause_result["seek_response"],
                "attempts": pause_result["attempts"],
            }
            refinement_iterations.append(iteration_payload)
            self._emit_playback_event(
                "corrective seek iteration result",
                strategy=strategy_name,
                stage=stage_index,
                iteration=iteration,
                corrected_target=corrected_tick,
                paused_tick=pause_result["paused_tick"],
                outcome=pause_result["outcome"],
            )

            if pause_result["outcome"] == "seek_confirmed":
                pause_result["refinement_iterations"] = refinement_iterations
                pause_result["initial_seek_response"] = initial_seek_response
                pause_result["initial_immediate_probes"] = initial_immediate_probes
                pause_result["initial_attempts"] = initial_attempts
                pause_result["initial_first_pause_readable"] = initial_first_pause_readable
                pause_result["initial_second_pause_readable"] = initial_second_pause_readable
                self._emit_playback_event(
                    "corrective seek success",
                    strategy=strategy_name,
                    stage=stage_index,
                    paused_tick=pause_result["paused_tick"],
                    target_tick=expected_tick,
                    tolerance_ticks=tolerance,
                )
                return pause_result

            paused_tick = pause_result.get("paused_tick")
            if paused_tick is None:
                pause_result["outcome"] = "seek_state_unreadable"
                pause_result["refinement_iterations"] = refinement_iterations
                return pause_result

            best_payload = pause_result
            new_error = abs(paused_tick - expected_tick)
            if new_error >= previous_error:
                break
            previous_error = new_error
            current_command_tick = corrected_tick
            current_observed_tick = paused_tick

        failure_payload = best_payload or {
            "paused_tick": current_observed_tick,
            "outcome": "seek_non_converging",
            "attempts": initial_attempts,
            "pause_response": "",
            "seek_response": initial_seek_response,
            "seek_acknowledged": True,
            "probe_readable": True,
            "immediate_probes": initial_immediate_probes,
            "first_pause_readable": initial_first_pause_readable,
            "second_pause_readable": initial_second_pause_readable,
        }
        failure_payload["outcome"] = "seek_non_converging"
        failure_payload["refinement_iterations"] = refinement_iterations
        failure_payload["initial_seek_response"] = initial_seek_response
        failure_payload["initial_immediate_probes"] = initial_immediate_probes
        failure_payload["initial_attempts"] = initial_attempts
        failure_payload["initial_first_pause_readable"] = initial_first_pause_readable
        failure_payload["initial_second_pause_readable"] = initial_second_pause_readable
        self._emit_playback_event(
            "corrective seek failed",
            strategy=strategy_name,
            stage=stage_index,
            target_tick=expected_tick,
            paused_tick=failure_payload.get("paused_tick"),
            outcome=failure_payload["outcome"],
        )
        return failure_payload

    def _pause_after_seek(
        self,
        result: PlaybackResult,
        *,
        expected_tick: int,
        strategy_name: str,
        stage_index: int | None = None,
        is_final_stage: bool = False,
        anchor_tick_before_seek: int | None = None,
        seek_command: str = "",
        seek_response: str = "",
        seek_acknowledged: bool = False,
        raise_on_nonfatal_failure: bool = True,
    ) -> dict[str, Any]:
        tolerance = self._seek_tolerance_ticks_for_command(seek_command)
        attempts_summary: list[dict[str, Any]] = []
        settle_schedule = (_SEEK_PAUSE_DELAY_S, max(_SEEK_PAUSE_DELAY_S * 2, 1.4))
        last_pause_response = ""
        last_paused_tick: int | None = None
        immediate_probe_result = self._run_immediate_post_seek_probes(
            strategy_name=strategy_name,
            stage_index=stage_index,
            expected_tick=expected_tick,
            result=result,
            seek_command=seek_command,
        )
        authoritative_first_pause_tick = immediate_probe_result.get("first_demo_pause_tick")
        first_pause_readable = False
        second_pause_readable = False

        if (
            authoritative_first_pause_tick is not None
            and anchor_tick_before_seek is not None
            and abs(authoritative_first_pause_tick - anchor_tick_before_seek) <= _NO_MOVEMENT_TOLERANCE_TICKS
        ):
            self._emit_playback_event(
                "no-movement confirmed on first authoritative pause",
                strategy=strategy_name,
                stage=stage_index,
                target_tick=expected_tick,
                anchor_tick=anchor_tick_before_seek,
                first_pause_tick=authoritative_first_pause_tick,
                no_movement_tolerance_ticks=_NO_MOVEMENT_TOLERANCE_TICKS,
            )
            self._emit_playback_event(
                "skipping additional post-seek retries due to authoritative no-movement",
                strategy=strategy_name,
                stage=stage_index,
                target_tick=expected_tick,
                anchor_tick=anchor_tick_before_seek,
            )
            summary = {
                "strategy": strategy_name,
                "stage": stage_index,
                "target_tick": expected_tick,
                "anchor_tick": anchor_tick_before_seek,
                "pause_tick": authoritative_first_pause_tick,
                "first_pause_tick": authoritative_first_pause_tick,
                "authoritative_outcome": "seek_no_movement",
                "outcome": "seek_no_movement",
                "attempt": 0,
                "seek_command": seek_command,
                "seek_response": seek_response,
                "seek_acknowledged": seek_acknowledged,
                "probe_readable": immediate_probe_result["probe_readable"],
                "first_pause_readable": True,
                "second_pause_readable": False,
                "extra_retries_skipped": True,
            }
            self._emit_playback_event("strategy result", **summary)
            failure_payload = {
                "paused_tick": authoritative_first_pause_tick,
                "outcome": "seek_no_movement",
                "attempts": [],
                "pause_response": "",
                "seek_response": seek_response,
                "seek_acknowledged": seek_acknowledged,
                "probe_readable": immediate_probe_result["probe_readable"],
                "immediate_probes": immediate_probe_result["probes"],
                "first_pause_readable": True,
                "second_pause_readable": False,
                "first_pause_tick": authoritative_first_pause_tick,
                "authoritative_outcome": "seek_no_movement",
                "extra_retries_skipped": True,
            }
            if not raise_on_nonfatal_failure:
                return failure_payload
            raise PlaybackError(
                f"{strategy_name} seek_no_movement: authoritative first pause remained at anchor tick {authoritative_first_pause_tick}"
            )

        for attempt_idx, settle_s in enumerate(settle_schedule, start=1):
            self._emit_playback_event(
                "post-seek settle begin",
                strategy=strategy_name,
                stage=stage_index,
                attempt=attempt_idx,
                settle_s=settle_s,
                expected_tick=expected_tick,
            )
            time.sleep(settle_s)
            settle_probe = self._poll_playback_runtime(
                commands=["demo_timescale", "spec_mode"],
                interval_s=0.2,
                timeout_s=0.8,
                accept_states={PLAYBACK_SEEKING, PLAYBACK_PLAYING, PLAYBACK_PAUSED_READY},
                result=result,
                label=f"{strategy_name}_post_seek",
                require_positive_signal=False,
                fail_on_fatal=True,
            )
            pause_response = self.cs2.exec_command("demo_pause")
            paused_tick = self._extract_paused_tick(pause_response)
            pause_readable = bool((pause_response or "").strip())
            last_pause_response = pause_response
            last_paused_tick = paused_tick
            if attempt_idx == 1:
                first_pause_readable = pause_readable
            elif attempt_idx == 2:
                second_pause_readable = pause_readable
            attempt_summary = {
                "attempt": attempt_idx,
                "settle_s": settle_s,
                "pause_response": (pause_response or "").strip(),
                "paused_tick": paused_tick,
                "pause_readable": pause_readable,
                "probe_details": settle_probe.get("details", []),
                "last_positive_signal": settle_probe.get("last_positive_signal", ""),
                "last_negative_signal": settle_probe.get("last_negative_signal", ""),
                "last_fatal_signal": settle_probe.get("last_fatal_signal", ""),
            }
            attempts_summary.append(attempt_summary)
            self._emit_playback_event(
                "post-seek pause response",
                strategy=strategy_name,
                stage=stage_index,
                attempt=attempt_idx,
                paused_tick=paused_tick,
                raw=(pause_response or "").strip(),
                pause_readable=pause_readable,
            )
            if paused_tick is not None:
                outcome = self._classify_seek_outcome(
                    paused_tick=paused_tick,
                    expected_tick=expected_tick,
                    tolerance=tolerance,
                    anchor_tick_before_seek=anchor_tick_before_seek,
                )
                summary = {
                    "strategy": strategy_name,
                    "stage": stage_index,
                    "target_tick": expected_tick,
                    "anchor_tick": anchor_tick_before_seek,
                    "pause_tick": paused_tick,
                    "outcome": outcome,
                    "attempt": attempt_idx,
                    "seek_command": seek_command,
                    "seek_response": seek_response,
                    "seek_acknowledged": seek_acknowledged,
                    "probe_readable": immediate_probe_result["probe_readable"],
                    "first_pause_readable": first_pause_readable,
                    "second_pause_readable": second_pause_readable,
                }
                self._emit_playback_event("strategy result", **summary)
                if outcome == "seek_confirmed":
                    if is_final_stage:
                        result.observed_pause_tick = paused_tick
                        result.paused_at_start = True
                        self._set_playback_state(result, PLAYBACK_PAUSED_READY, reason=f"{strategy_name}_paused_tick={paused_tick}")
                    return {
                        "paused_tick": paused_tick,
                        "outcome": outcome,
                        "attempts": attempts_summary,
                        "pause_response": (pause_response or "").strip(),
                        "seek_response": seek_response,
                        "seek_acknowledged": seek_acknowledged,
                        "probe_readable": immediate_probe_result["probe_readable"],
                        "immediate_probes": immediate_probe_result["probes"],
                        "first_pause_readable": first_pause_readable,
                        "second_pause_readable": second_pause_readable,
                    }
                failure_payload = {
                    "paused_tick": paused_tick,
                    "outcome": outcome,
                    "attempts": attempts_summary,
                    "pause_response": (pause_response or "").strip(),
                    "seek_response": seek_response,
                    "seek_acknowledged": seek_acknowledged,
                    "probe_readable": immediate_probe_result["probe_readable"],
                    "immediate_probes": immediate_probe_result["probes"],
                    "first_pause_readable": first_pause_readable,
                    "second_pause_readable": second_pause_readable,
                }
                if (
                    self._is_mirv_skip_command(seek_command)
                    and seek_acknowledged
                    and paused_tick is not None
                    and outcome == "seek_overshoot"
                ):
                    initial_command_tick = self._extract_tick_from_seek_command(seek_command)
                    if initial_command_tick is not None:
                        refined_payload = self._attempt_mirv_skip_refinement(
                            result,
                            expected_tick=expected_tick,
                            strategy_name=strategy_name,
                            stage_index=stage_index,
                            is_final_stage=is_final_stage,
                            anchor_tick_before_seek=anchor_tick_before_seek,
                            initial_command_tick=initial_command_tick,
                            initial_paused_tick=paused_tick,
                            initial_attempts=attempts_summary,
                            initial_seek_response=seek_response,
                            initial_immediate_probes=immediate_probe_result["probes"],
                            initial_first_pause_readable=first_pause_readable,
                            initial_second_pause_readable=second_pause_readable,
                        )
                        if refined_payload["outcome"] == "seek_confirmed":
                            return refined_payload
                        failure_payload = refined_payload
                elif self._is_mirv_skip_command(seek_command) and outcome == "seek_no_movement":
                    self._emit_playback_event(
                        "corrective seek skipped",
                        strategy=strategy_name,
                        stage=stage_index,
                        reason="seek_no_movement",
                        paused_tick=paused_tick,
                        anchor_tick=anchor_tick_before_seek,
                        target_tick=expected_tick,
                    )
                if not raise_on_nonfatal_failure:
                    return failure_payload
                raise PlaybackError(
                    f"{strategy_name} {failure_payload['outcome']}: paused at tick {failure_payload.get('paused_tick')}, expected near {expected_tick} (+/- {tolerance})"
                )

        self._emit_playback_event(
            "strategy result",
            strategy=strategy_name,
            stage=stage_index,
            target_tick=expected_tick,
            anchor_tick=anchor_tick_before_seek,
            pause_tick=None,
            outcome="seek_state_unreadable",
            seek_command=seek_command,
            seek_response=seek_response,
            seek_acknowledged=seek_acknowledged,
            probe_readable=immediate_probe_result["probe_readable"],
            first_pause_readable=first_pause_readable,
            second_pause_readable=second_pause_readable,
        )
        failure_payload = {
            "paused_tick": None,
            "outcome": "seek_state_unreadable",
            "attempts": attempts_summary,
            "pause_response": (last_pause_response or "").strip(),
            "seek_response": seek_response,
            "seek_acknowledged": seek_acknowledged,
            "probe_readable": immediate_probe_result["probe_readable"],
            "immediate_probes": immediate_probe_result["probes"],
            "first_pause_readable": first_pause_readable,
            "second_pause_readable": second_pause_readable,
        }
        if not raise_on_nonfatal_failure:
            return failure_payload
        raise PlaybackError(
            f"{strategy_name} seek_state_unreadable: demo_pause did not return a tick after seek"
            + (f" (last_response={last_pause_response.strip()[:120]})" if last_pause_response else "")
        )

    def _run_incremental_seek_probes(
        self,
        *,
        result: PlaybackResult,
        anchor_tick: int | None,
        target_tick: int,
    ) -> dict[str, Any]:
        transport = (self.config.hlae_incremental_probe_transport or "demo_gototick").strip().lower()
        if transport == "mirv_skip_tick":
            command_builder = lambda tick: f"mirv_skip tick to {tick}"
        else:
            transport = "demo_gototick"
            command_builder = lambda tick: f"demo_gototick {tick}"
        probe_ticks = self._build_incremental_probe_ticks(anchor_tick, target_tick)
        summary: dict[str, Any] = {
            "anchor_tick": anchor_tick,
            "target_tick": target_tick,
            "transport": transport,
            "probe_ticks": probe_ticks,
            "probes": [],
            "first_movement_tick": None,
            "first_failed_tick": None,
            "first_fatal_tick": None,
            "safe_seek_anchor": anchor_tick,
            "any_seekable_region": False,
            "continuation_ready": False,
        }
        if anchor_tick is None or not probe_ticks:
            return summary

        self._emit_playback_event(
            "incremental seek probe begin",
            anchor_tick=anchor_tick,
            target_tick=target_tick,
            probe_ticks=probe_ticks,
            transport=transport,
        )

        current_anchor = anchor_tick
        for probe_index, probe_tick in enumerate(probe_ticks, start=1):
            command = command_builder(probe_tick)
            probe_entry: dict[str, Any] = {
                "index": probe_index,
                "transport": transport,
                "from_tick": current_anchor,
                "target_tick": probe_tick,
                "command": command,
            }
            if self._is_known_invalid_seek_boundary(
                transport=transport,
                anchor_tick=current_anchor,
                target_tick=probe_tick,
            ):
                probe_entry.update(
                    {
                        "result": "seek_command_runtime_failure",
                        "fatal_marker": "known invalid seek boundary",
                        "fatal_source": "seek_command_response",
                    }
                )
                summary["probes"].append(probe_entry)
                if summary["first_fatal_tick"] is None:
                    summary["first_fatal_tick"] = probe_tick
                self._emit_playback_event(
                    "incremental seek probe skipped due to known invalid boundary",
                    transport=transport,
                    anchor_tick=current_anchor,
                    attempted_tick=probe_tick,
                )
                break
            try:
                command_result = self._execute_seek_command(
                    command,
                    f"incremental_probe_{transport}",
                    stage_index=probe_index,
                    stage_tick=probe_tick,
                )
                seek_acknowledged = bool(command_result["inspected"]["seek_progress_markers"])
                probe_entry["seek_response"] = command_result["inspected"]["raw"]
                probe_entry["seek_acknowledged"] = seek_acknowledged
                pause_result = self._pause_after_seek(
                    result,
                    expected_tick=probe_tick,
                    strategy_name=f"incremental_probe_{transport}",
                    stage_index=probe_index,
                    is_final_stage=False,
                    anchor_tick_before_seek=current_anchor,
                    seek_command=command,
                    seek_response=command_result["inspected"]["raw"],
                    seek_acknowledged=seek_acknowledged,
                    raise_on_nonfatal_failure=False,
                )
                raw_outcome = pause_result["outcome"]
                paused_tick = pause_result["paused_tick"]
                movement_confirmed = raw_outcome == "seek_confirmed"
                probe_entry.update(
                    {
                        "pause_tick": paused_tick,
                        "raw_outcome": raw_outcome,
                        "result": "movement_confirmed" if movement_confirmed else "movement_failed",
                        "probe_readable": pause_result["probe_readable"],
                        "first_pause_readable": pause_result["first_pause_readable"],
                        "second_pause_readable": pause_result["second_pause_readable"],
                        "pause_response": pause_result["pause_response"],
                        "immediate_probes": pause_result["immediate_probes"],
                        "post_seek_attempts": pause_result["attempts"],
                        "refinement_iterations": pause_result.get("refinement_iterations", []),
                    }
                )
                self._emit_playback_event(
                    "incremental seek probe result",
                    transport=transport,
                    from_tick=current_anchor,
                    target_tick=probe_tick,
                    pause_tick=paused_tick,
                    result=probe_entry["result"],
                    raw_outcome=raw_outcome,
                    seek_acknowledged=seek_acknowledged,
                )
                summary["probes"].append(probe_entry)

                if movement_confirmed:
                    if summary["first_movement_tick"] is None:
                        summary["first_movement_tick"] = paused_tick
                    summary["safe_seek_anchor"] = paused_tick
                    summary["any_seekable_region"] = True
                    current_anchor = paused_tick or current_anchor
                    self._emit_playback_event(
                        "safe_seek_anchor identified",
                        safe_seek_anchor=summary["safe_seek_anchor"],
                        target_tick=probe_tick,
                    )
                    if probe_index < len(probe_ticks):
                        self.cs2.resume_demo()
                        time.sleep(0.25)
                        self._set_playback_state(result, PLAYBACK_PLAYING, reason=f"incremental_probe_resume_{probe_index}")
                    else:
                        summary["continuation_ready"] = True
                    continue

                if summary["first_failed_tick"] is None:
                    summary["first_failed_tick"] = probe_tick
                if raw_outcome in {"seek_overshoot", "seek_no_movement"} and paused_tick is not None:
                    summary["continuation_ready"] = bool(summary["any_seekable_region"]) and paused_tick == current_anchor
                self._emit_playback_event(
                    "incremental seek probe boundary detected",
                    transport=transport,
                    anchor_tick=anchor_tick,
                    attempted_tick=probe_tick,
                    pause_tick=paused_tick,
                    outcome=raw_outcome,
                )
                break
            except PlaybackRuntimeError as e:
                probe_entry.update(
                    {
                        "result": e.failure_code or "seek_runtime_failure",
                        "fatal_marker": e.marker,
                        "fatal_snippet": e.snippet,
                        "fatal_source": e.source,
                    }
                )
                summary["probes"].append(probe_entry)
                if summary["first_fatal_tick"] is None:
                    summary["first_fatal_tick"] = probe_tick
                if e.source == "seek_command_response":
                    self._record_invalid_seek_boundary(
                        result,
                        transport=e.transport or transport,
                        anchor_tick=current_anchor,
                        target_tick=probe_tick,
                        strategy_name=e.strategy_name or f"incremental_probe_{transport}",
                        stage_index=e.stage_index,
                        source=e.source,
                        marker=e.marker,
                    )
                self._emit_playback_event(
                    "seek crash boundary detected",
                    anchor_tick=anchor_tick,
                    attempted_tick=probe_tick,
                    offset=(probe_tick - anchor_tick) if anchor_tick is not None else None,
                    strategy_used=f"incremental_probe_{transport}",
                    transport=transport,
                    marker=e.marker,
                    source=e.source,
                )
                setattr(e, "probe_summary", summary)
                raise

        return summary

    def _run_seek_strategy(
        self,
        *,
        result: PlaybackResult,
        strategy_name: str,
        stage_ticks: list[int],
        command_builder,
        anchor_tick_before_seek: int | None = None,
    ) -> dict[str, Any]:
        strategy_step: dict[str, Any] = {
            "strategy": strategy_name,
            "stage_ticks": stage_ticks,
            "stages": [],
        }
        self._emit_playback_event("seek strategy selected", strategy=strategy_name, stage_ticks=stage_ticks)
        for idx, stage_tick in enumerate(stage_ticks, start=1):
            is_final = idx == len(stage_ticks)
            self._emit_playback_event(
                "seek strategy stage",
                strategy=strategy_name,
                stage=idx,
                tick=stage_tick,
                final_stage=is_final,
            )
            command = command_builder(stage_tick)
            command_result = self._execute_seek_command(
                command,
                strategy_name,
                stage_index=idx,
                stage_tick=stage_tick,
            )
            stage_info: dict[str, Any] = {
                "stage": idx,
                "tick": stage_tick,
                "command": command,
                "response": command_result["inspected"]["raw"],
            }
            seek_acknowledged = bool(command_result["inspected"]["seek_progress_markers"])
            pause_result = self._pause_after_seek(
                result,
                expected_tick=stage_tick,
                strategy_name=strategy_name,
                stage_index=idx,
                is_final_stage=is_final,
                anchor_tick_before_seek=anchor_tick_before_seek,
                seek_command=command,
                seek_response=command_result["inspected"]["raw"],
                seek_acknowledged=seek_acknowledged,
            )
            stage_info["paused_tick"] = pause_result["paused_tick"]
            stage_info["outcome"] = pause_result["outcome"]
            stage_info["post_seek_attempts"] = pause_result["attempts"]
            stage_info["pause_response"] = pause_result["pause_response"]
            stage_info["seek_response"] = pause_result["seek_response"]
            stage_info["seek_acknowledged"] = pause_result["seek_acknowledged"]
            stage_info["probe_readable"] = pause_result["probe_readable"]
            stage_info["immediate_probes"] = pause_result["immediate_probes"]
            stage_info["first_pause_readable"] = pause_result["first_pause_readable"]
            stage_info["second_pause_readable"] = pause_result["second_pause_readable"]
            stage_info["refinement_iterations"] = pause_result.get("refinement_iterations", [])
            strategy_step["stages"].append(stage_info)
            if not is_final:
                self.cs2.resume_demo()
                time.sleep(0.25)
                self._set_playback_state(result, PLAYBACK_PLAYING, reason=f"{strategy_name}_resume_stage_{idx}")
        self._emit_playback_event(
            "seek strategy succeeded",
            strategy=strategy_name,
            paused_tick=result.observed_pause_tick,
        )
        return strategy_step

    def _step_pre_seek_settle(self, result: PlaybackResult, seek_tick: int) -> dict[str, Any]:
        settle_s = 0.9
        self._set_playback_state(result, PLAYBACK_PLAYING_SETTLING, reason=f"pre_seek_settle_tick={seek_tick}")
        self._emit_playback_event("entering pre-seek settle", target_tick=seek_tick, settle_s=settle_s)
        time.sleep(settle_s)
        settled = self._poll_playback_runtime(
            commands=["demo_timescale", "spec_mode"],
            interval_s=0.35,
            timeout_s=2.5,
            accept_states={PLAYBACK_PLAYING, PLAYBACK_PAUSED_READY},
            result=result,
            label="pre_seek_settle",
            require_positive_signal=False,
            fail_on_fatal=True,
        )
        self._set_playback_state(result, PLAYBACK_PLAYING, reason="pre_seek_settle_complete")
        self._emit_playback_event(
            "playback still active before seek",
            elapsed_s=settled.get("elapsed_s"),
            last_positive_signal=settled.get("last_positive_signal", ""),
        )
        return settled

    def _probe_playback_channel(self, commands: list[str] | None = None) -> dict[str, Any]:
        return self._probe_playback_channel_with_options(commands=commands, allow_empty=False)

    def _probe_playback_channel_with_options(
        self,
        commands: list[str] | None = None,
        allow_empty: bool = False,
    ) -> dict[str, Any]:
        commands = commands or ["demo_timescale", "spec_mode", "spec_autodirector", "cl_spec_auto_observer"]
        details: list[dict[str, str]] = []
        for cmd in commands:
            try:
                raw = self.cs2.exec_command(cmd)
            except CS2ControlError as e:
                return {"ok": False, "failed_command": cmd, "error": str(e), "details": details}
            lowered = (raw or "").lower()
            if not (raw or "").strip() and not allow_empty:
                return {"ok": False, "failed_command": cmd, "raw": "", "details": details}
            if any(token in lowered for token in _INVALID_PROBE_TOKENS):
                return {"ok": False, "failed_command": cmd, "raw": raw.strip(), "details": details}
            details.append({"command": cmd, "raw": (raw or "").strip()[:200]})
        return {"ok": True, "details": details}

    def _wait_for_stable_probe(
        self,
        label: str,
        timeout_s: float,
        interval_s: float = 0.35,
        consecutive_successes: int = 2,
        commands: list[str] | None = None,
        allow_empty: bool = False,
    ) -> dict[str, Any]:
        deadline = time.time() + timeout_s
        successes = 0
        attempts = 0
        last_probe: dict[str, Any] = {}

        while time.time() < deadline:
            attempts += 1
            last_probe = self._probe_playback_channel_with_options(
                commands=commands,
                allow_empty=allow_empty,
            )
            if last_probe.get("ok"):
                successes += 1
                if successes >= consecutive_successes:
                    return {
                        "attempts": attempts,
                        "stable": True,
                        "last_probe": last_probe,
                        "label": label,
                    }
            else:
                successes = 0
            time.sleep(interval_s)

        raise PlaybackError(
            f"{label} did not stabilize within {timeout_s:.1f}s"
            + (f": {last_probe.get('error')}" if last_probe.get("error") else "")
        )

    def _effective_seek_timeout_s(self) -> float:
        """Use the backend-specific seek timeout when available.

        HLAE demo playback is noticeably slower to settle after `demo_gototick`
        than the old OBS flow, so the generic post-seek timeout is too tight.
        """
        base_timeout = max(self.config.post_seek_settle_s, 1.5) + 1.5
        if (self.config.render_backend or "").strip().lower() == "hlae":
            return max(base_timeout, float(self.config.hlae_render_seek_timeout_s))
        return base_timeout

    def prepare(self, request: PlaybackRequest) -> PlaybackResult:
        """Prepare CS2 for clip capture.

        This executes the full preparation sequence:
        1. Validate request
        2. Ensure CS2 is running
        3. Connect netcon
        4. Stage demo file
        5. Load demo
        6. Wait for demo load
        7. Seek to start tick
        8. Pause playback

        Returns:
            PlaybackResult with full status

        Raises:
            PlaybackError on irrecoverable failure
        """
        result = PlaybackResult(
            started_at=time.time(),
            requested_start_tick=request.start_tick,
            requested_anchor_tick=request.anchor_tick,
            requested_end_tick=request.end_tick,
            round_number=request.round_number,
        )
        self._set_playback_state(result, PLAYBACK_NOT_LOADED, reason="prepare_start")

        try:
            # Step 1: Validate
            self._step_validate(request, result)

            # Step 2: Ensure CS2 running
            self._step_ensure_cs2(result)

            # Step 3: Connect netcon
            self._step_connect_netcon(result)

            # Step 4: Stage demo
            playdemo_path = self._step_stage_demo(request, result)

            # Step 5: Load demo
            self._step_load_demo(playdemo_path, result)

            # Step 6: Wait for demo load
            self._step_wait_for_load(result)

            # Step 7: Seek to start
            self._step_seek(request, result)

            # Step 8: Pause
            self._step_pause(result)

            result.status = "ready"
            result.demo_loaded = True

        except (PlaybackError, CS2ControlError) as e:
            result.status = "failed"
            result.error = str(e)
            log.error(f"Playback preparation failed: {e}")

        finally:
            result.completed_at = time.time()
            result.duration_ms = (result.completed_at - result.started_at) * 1000

        return result

    def _step_validate(self, request: PlaybackRequest, result: PlaybackResult) -> None:
        step = {"step": "validate", "warnings": []}
        if not Path(request.demo_path).exists():
            raise PlaybackError(f"Demo file not found: {request.demo_path}")
        if request.start_tick <= 0:
            step["warnings"].append("start_tick is 0 or negative")
        if request.end_tick <= request.start_tick:
            step["warnings"].append("end_tick <= start_tick")
        result.steps.append(step)
        result.warnings.extend(step["warnings"])

    def _step_ensure_cs2(self, result: PlaybackResult) -> None:
        step = {"step": "ensure_cs2"}
        try:
            pid = self.cs2.launch_cs2()
            result.cs2_running = True
            result.cs2_pid = pid
            step["status"] = "ok"
            step["pid"] = pid
        except CS2ControlError as e:
            step["status"] = "failed"
            step["error"] = str(e)
            result.failure_code = "launch_failure"
            result.steps.append(step)
            raise PlaybackError(f"CS2 launch failed: {e}")
        result.steps.append(step)

    def _step_connect_netcon(self, result: PlaybackResult) -> None:
        step = {"step": "connect_netcon"}
        try:
            self.cs2.ensure_netcon()
            result.netcon_connected = True
            step["status"] = "ok"
        except CS2ControlError as e:
            step["status"] = "failed"
            step["error"] = str(e)
            result.failure_code = "netcon_failure"
            result.steps.append(step)
            raise PlaybackError(f"Netcon connection failed: {e}")
        result.steps.append(step)

    def _step_stage_demo(self, request: PlaybackRequest, result: PlaybackResult) -> str:
        step = {"step": "stage_demo"}
        try:
            playdemo_path = self.cs2.stage_demo(request.demo_path)
            result.demo_path_used = request.demo_path
            result.playdemo_path = playdemo_path
            step["status"] = "ok"
            step["playdemo_path"] = playdemo_path
        except CS2ControlError as e:
            step["status"] = "failed"
            step["error"] = str(e)
            result.failure_code = "staging_failure"
            result.steps.append(step)
            raise PlaybackError(f"Demo staging failed: {e}")
        result.steps.append(step)
        return playdemo_path

    def _step_load_demo(self, playdemo_path: str, result: PlaybackResult) -> None:
        step = {"step": "load_demo"}
        try:
            observer = getattr(self.cs2, "command_observer", None)
            if observer is not None:
                try:
                    observer(f"[SESSION] sending playdemo | {json.dumps({'playdemo_path': playdemo_path}, sort_keys=True)}", "")
                except Exception:
                    pass
            self.cs2.load_demo(playdemo_path)
            self._set_playback_state(result, PLAYBACK_LOADING, reason="playdemo_sent")
            step["status"] = "ok"
        except CS2ControlError as e:
            step["status"] = "failed"
            step["error"] = str(e)
            result.failure_code = "load_failure"
            result.steps.append(step)
            raise PlaybackError(f"Demo load failed: {e}")
        result.steps.append(step)

    def _step_wait_for_load(self, result: PlaybackResult) -> None:
        step = {"step": "wait_for_load"}
        timeout_s = self._effective_load_timeout_s()
        log.info(f"Polling for demo load readiness (timeout={timeout_s:.1f}s)...")
        self._emit_playback_event("waiting for demo playback confirmation", timeout_s=timeout_s)
        time.sleep(1.25)
        settled = self._poll_playback_runtime(
            commands=["demo_timescale", "spec_mode"],
            interval_s=0.45,
            timeout_s=timeout_s,
            accept_states={PLAYBACK_PLAYING, PLAYBACK_PAUSED_READY},
            result=result,
            label="demo_load",
            extend_on_progress=True,
            progress_grace_s=6.0,
            max_extensions=2,
            require_positive_signal=True,
        )
        step["status"] = "ok"
        step["timeout_s"] = timeout_s
        step["attempts"] = settled["attempts"]
        step["playback_state"] = settled["state"]
        step["probe"] = {"ok": True, "details": settled["details"]}
        step["elapsed_s"] = settled.get("elapsed_s")
        step["last_progress_signal"] = settled.get("last_progress_signal", "")
        step["last_positive_signal"] = settled.get("last_positive_signal", "")
        step["last_negative_signal"] = settled.get("last_negative_signal", "")
        step["extensions_used"] = settled.get("extensions_used", 0)
        result.steps.append(step)

    def _step_seek(self, request: PlaybackRequest, result: PlaybackResult) -> None:
        step = {"step": "seek"}
        seek_timeout_s = self._effective_seek_timeout_s()
        seek_tick = request.start_tick
        if self._playback_state != PLAYBACK_PLAYING:
            result.failure_code = "seek_failure"
            raise PlaybackError(f"Cannot seek while playback_state={self._playback_state}")

        pre_seek = self._step_pre_seek_settle(result, seek_tick)
        step["pre_seek_settle"] = {
            "elapsed_s": pre_seek.get("elapsed_s"),
            "last_positive_signal": pre_seek.get("last_positive_signal", ""),
            "last_negative_signal": pre_seek.get("last_negative_signal", ""),
            "last_fatal_signal": pre_seek.get("last_fatal_signal", ""),
        }
        current_tick = self._probe_current_tick_before_seek(result)
        step["pre_seek_current_tick"] = current_tick
        step["strategies"] = []
        try:
            incremental_probe = self._run_incremental_seek_probes(
                result=result,
                anchor_tick=current_tick,
                target_tick=seek_tick,
            )
            step["incremental_probe"] = incremental_probe

            if incremental_probe.get("any_seekable_region"):
                current_tick = incremental_probe.get("safe_seek_anchor") or current_tick
                step["safe_seek_anchor"] = current_tick
                if result.playback_state == PLAYBACK_PAUSED_READY:
                    self.cs2.resume_demo()
                    time.sleep(0.25)
                self._set_playback_state(result, PLAYBACK_PLAYING, reason="incremental_probe_to_strategy")
            elif incremental_probe.get("first_failed_tick") is not None:
                self._set_playback_state(result, PLAYBACK_SEEK_FAILED, reason="seek_region_invalid")
                step["status"] = "failed"
                step["error"] = (
                    f"seek_region_invalid: no forward movement confirmed beyond anchor tick {incremental_probe.get('anchor_tick')}"
                )
                result.failure_code = "seek_region_invalid"
                result.steps.append(step)
                raise PlaybackError(step["error"])

            staged_ticks = self._build_stage_ticks(current_tick, seek_tick)
            midpoint_tick = staged_ticks[0] if staged_ticks else seek_tick
            strategy_plan = [
                {
                    "name": "mirv_skip_tick",
                    "stage_ticks": [seek_tick],
                    "command_builder": lambda tick: f"mirv_skip tick to {tick}",
                },
            ]
            if self.config.hlae_enable_gototick_fallback:
                strategy_plan.extend(
                    [
                        {
                            "name": "staged_gototick",
                            "stage_ticks": staged_ticks,
                            "command_builder": lambda tick: f"demo_gototick {tick}",
                        },
                        {
                            "name": "conservative_two_step_seek",
                            "stage_ticks": [midpoint_tick, seek_tick] if midpoint_tick != seek_tick else [seek_tick],
                            "command_builder": lambda tick: f"demo_gototick {tick}",
                        },
                        {
                            "name": "direct_gototick",
                            "stage_ticks": [seek_tick],
                            "command_builder": lambda tick: f"demo_gototick {tick}",
                        },
                    ]
                )
            step["strategies"] = []

            last_nonfatal_error = ""
            for strategy in strategy_plan:
                strategy_name = strategy["name"]
                stage_list = [tick for tick in strategy["stage_ticks"] if tick > 0]
                if not stage_list:
                    continue

                self._set_playback_state(result, PLAYBACK_SEEKING, reason=f"{strategy_name}_start")
                self._emit_playback_event("seek strategy", strategy=strategy_name)

                try:
                    strategy_step = self._run_seek_strategy(
                        result=result,
                        strategy_name=strategy_name,
                        stage_ticks=stage_list,
                        command_builder=strategy["command_builder"],
                        anchor_tick_before_seek=current_tick,
                    )
                    step["strategies"].append(strategy_step)
                    result.actual_seek_tick = seek_tick
                    step["status"] = "ok"
                    step["seek_tick"] = seek_tick
                    step["playback_state"] = result.playback_state
                    step["strategy"] = strategy_name
                    step["timeout_s"] = seek_timeout_s
                    result.steps.append(step)
                    return
                except PlaybackRuntimeError as e:
                    strategy_failed = {
                        "strategy": strategy_name,
                        "status": "failed",
                        "error": str(e),
                        "fatal_marker": e.marker,
                        "fatal_snippet": e.snippet,
                    }
                    step["strategies"].append(strategy_failed)
                    self._emit_playback_event(
                        "seek strategy failed due to fatal marker",
                        strategy=strategy_name,
                        marker=e.marker,
                    )
                    raise
                except PlaybackError as e:
                    last_nonfatal_error = str(e)
                    step["strategies"].append(
                        {
                            "strategy": strategy_name,
                            "status": "failed",
                            "error": str(e),
                        }
                    )
                    self._emit_playback_event(
                        "seek strategy failed",
                        strategy=strategy_name,
                        error=str(e),
                    )
                    if result.playback_state == PLAYBACK_PAUSED_READY:
                        self.cs2.resume_demo()
                        time.sleep(0.25)
                    self._set_playback_state(result, PLAYBACK_PLAYING, reason=f"{strategy_name}_fallback")
                    continue
            raise PlaybackError(last_nonfatal_error or "all seek strategies failed without a stable paused tick")
        except PlaybackRuntimeError as e:
            self._set_playback_state(result, PLAYBACK_SEEK_FAILED, reason=e.marker or "seek_runtime_failure")
            probe_summary = getattr(e, "probe_summary", None)
            if probe_summary is not None:
                step["incremental_probe"] = probe_summary
            if e.source == "seek_command_response":
                self._record_invalid_seek_boundary(
                    result,
                    transport=e.transport or "unknown",
                    anchor_tick=step.get("pre_seek_current_tick"),
                    target_tick=e.target_tick,
                    strategy_name=e.strategy_name or "seek",
                    stage_index=e.stage_index,
                    source=e.source,
                    marker=e.marker,
                )
            step["status"] = "failed"
            step["error"] = str(e)
            step["fatal_marker"] = e.marker
            step["fatal_snippet"] = e.snippet
            step["fatal_source"] = e.source
            step["failure_code"] = e.failure_code or "seek_runtime_failure"
            if e.probe_command:
                step["fatal_probe"] = e.probe_command
            result.failure_code = e.failure_code or "seek_runtime_failure"
            result.steps.append(step)
            failing_strategy = "incremental_probe"
            if step.get("strategies"):
                failing_strategy = step["strategies"][-1].get("strategy", failing_strategy)
            raise PlaybackError(
                f"{failing_strategy} triggered fatal error: {e.snippet or e.marker or str(e)}"
            )
        except PlaybackError as e:
            self._set_playback_state(result, PLAYBACK_SEEK_FAILED, reason="seek_failure")
            step["status"] = "failed"
            step["error"] = str(e)
            result.failure_code = "seek_failure"
            result.steps.append(step)
            raise

    def _step_pause(self, result: PlaybackResult) -> None:
        step = {"step": "pause"}
        expected_tick = result.actual_seek_tick or result.requested_start_tick
        tolerance = self._pause_tolerance_ticks()
        if result.paused_at_start and result.playback_state == PLAYBACK_PAUSED_READY and result.observed_pause_tick:
            step["status"] = "ok"
            step["attempts"] = 0
            step["pause_tick"] = result.observed_pause_tick
            step["tolerance_ticks"] = tolerance
            step["probe"] = {"ok": True, "details": [{"command": "seek_strategy_pause", "raw": "already paused"}]}
            result.steps.append(step)
            return
        try:
            max_attempts = 2
            for attempt in range(1, max_attempts + 1):
                pause_response = self.cs2.exec_command("demo_pause")
                paused_tick = self._extract_paused_tick(pause_response)
                if paused_tick is not None:
                    result.observed_pause_tick = paused_tick
                    self._emit_playback_event("paused at tick", tick=paused_tick, attempt=attempt)
                    if abs(paused_tick - expected_tick) <= tolerance:
                        result.paused_at_start = True
                        self._set_playback_state(result, PLAYBACK_PAUSED_READY, reason=f"pause_tick={paused_tick}")
                        step["status"] = "ok"
                        step["attempts"] = attempt
                        step["pause_tick"] = paused_tick
                        step["tolerance_ticks"] = tolerance
                        step["probe"] = {
                            "ok": True,
                            "details": [{"command": "demo_pause", "raw": (pause_response or "").strip()[:400]}],
                        }
                        result.steps.append(step)
                        return

                    if attempt < max_attempts:
                        self._emit_playback_event(
                            "pause tick mismatch; retrying seek",
                            observed_tick=paused_tick,
                            expected_tick=expected_tick,
                            tolerance_ticks=tolerance,
                        )
                        self.cs2.resume_demo()
                        time.sleep(0.2)
                        self._set_playback_state(result, PLAYBACK_SEEKING, reason="pause_mismatch_retry")
                        self.cs2.seek_to_tick(expected_tick)
                        self._poll_playback_runtime(
                            commands=["demo_timescale", "spec_mode"],
                            interval_s=0.35,
                            timeout_s=max(self._effective_seek_timeout_s(), 6.0),
                            accept_states={PLAYBACK_PLAYING, PLAYBACK_PAUSED_READY},
                            result=result,
                            label="pause_retry_seek",
                        )
                        self._set_playback_state(result, PLAYBACK_PLAYING, reason="pause_retry_seek_ready")
                        continue

                    raise PlaybackError(
                        f"pause landed at tick {paused_tick}, expected near {expected_tick} (+/- {tolerance})"
                    )

                if attempt < max_attempts:
                    time.sleep(0.25)
                    continue

                raise PlaybackError("demo_pause did not return a paused tick")
        except CS2ControlError as e:
            step["status"] = "warning"
            step["error"] = str(e)
            result.warnings.append(f"Pause command failed: {e}")
        except PlaybackError as e:
            step["status"] = "failed"
            step["error"] = str(e)
            result.failure_code = "pause_failure"
            result.steps.append(step)
            raise
        result.steps.append(step)

    def resume_and_play(self, duration_s: float) -> None:
        """Resume playback for the clip duration."""
        self.cs2.set_timescale(1.0)
        self.cs2.resume_demo()

    def stop_playback(self) -> None:
        """Pause playback after capture."""
        try:
            self.cs2.pause_demo()
        except CS2ControlError:
            pass

    def cleanup(self) -> None:
        """Clean up resources."""
        if self.cs2:
            self.cs2.cleanup()
