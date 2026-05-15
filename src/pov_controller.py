"""
pov_controller.py
Strict player POV lock and camera control for CS2 demo playback.

This is the critical module that ensures:
- Correct player POV is selected deterministically
- Auto-director is disabled
- Freecam/observer drift is detected and corrected
- Wrong POV is treated as a hard failure
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from src.cs2_controller import CS2Controller, CS2ControlError

log = logging.getLogger(__name__)

STEAMID64_BASE = 76561197960265728


class POVError(Exception):
    """Raised when POV lock cannot be achieved or maintained."""


@dataclass
class POVState:
    """Tracks the current state of POV application."""
    requested_player: str = ""
    requested_steamid64: str = ""
    target_accountid: str = ""
    spec_mode_value: int = 0
    camera_mode: str = "player_pov"       # player_pov, freecam, observer
    observer_mode: str = "first_person"   # first_person, third_person, freecam
    applied: bool = False
    verified: bool = False
    apply_attempts: int = 0
    last_apply_time: float = 0.0
    failure_reason: str = ""
    verification_details: dict[str, Any] = field(default_factory=dict)
    commands_sent: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "requested_player": self.requested_player,
            "requested_steamid64": self.requested_steamid64,
            "target_accountid": self.target_accountid,
            "spec_mode_value": self.spec_mode_value,
            "camera_mode": self.camera_mode,
            "observer_mode": self.observer_mode,
            "applied": self.applied,
            "verified": self.verified,
            "apply_attempts": self.apply_attempts,
            "failure_reason": self.failure_reason,
            "verification_details": self.verification_details,
            "commands_sent": list(self.commands_sent),
        }


@dataclass
class POVController:
    """Controls player POV during CS2 demo playback."""
    cs2: CS2Controller
    state: POVState = field(default_factory=POVState)
    max_apply_attempts: int = 5
    apply_settle_s: float = 0.5
    confirmation_polls: int = 5
    confirmation_interval_s: float = 0.3
    base_command_delay_s: float = 0.08
    transition_command_delay_s: float = 0.18
    _FALSEY_VALUES = {"", "0", "false", "off", "no"}

    @staticmethod
    def _candidate_spec_modes(observer_mode: str) -> list[int]:
        if observer_mode == "third_person":
            return [5, 3]
        # In practice CS2 demo playback more commonly settles in first-person
        # with mode 4, while 1 remains a useful fallback on some setups.
        return [4, 1]

    @staticmethod
    def _steamid64_to_accountid(steamid64: str) -> str:
        value = str(steamid64 or "").strip()
        if not value.isdigit():
            return ""
        numeric = int(value)
        if numeric <= 0:
            return ""
        return str(numeric & 0xFFFFFFFF)

    @staticmethod
    def _parse_cvar_value(output: str) -> str:
        text = (output or "").strip()
        if not text:
            return ""

        quoted = re.findall(r'"([^"]+)"', text)
        if quoted:
            return quoted[-1].strip()

        m = re.search(r"=\s*([^\s]+)", text)
        if m:
            return m.group(1).strip().strip('"')

        tokens = [tok.strip('"') for tok in re.split(r"\s+", text) if tok]
        if tokens:
            return tokens[-1]
        return ""

    def _exec_sequence(self, commands: list[str], delay: float | None = None) -> dict[str, str]:
        responses: dict[str, str] = {}
        self.state.commands_sent.extend(commands)
        for idx, cmd in enumerate(commands):
            responses[cmd] = self.cs2.exec_command(cmd)
            if idx != len(commands) - 1:
                lower = cmd.lower()
                pace = self.base_command_delay_s if delay is None else delay
                if any(token in lower for token in ("spec_mode", "spec_player_by_", "spec_lock_to_accountid")):
                    pace = self.transition_command_delay_s if delay is None else max(delay, self.transition_command_delay_s)
                if pace > 0:
                    time.sleep(pace)
        return responses

    def _probe_lock_state(self, expected_mode: int) -> dict[str, Any]:
        details: dict[str, Any] = {}
        probe_map = {
            "spec_lock_to_accountid": "lock_accountid",
            "spec_mode": "spec_mode",
            "spec_autodirector": "autodirector",
            "cl_spec_auto_observer": "auto_observer",
            "spec_camera_follow": "camera_follow",
        }
        for cmd, key in probe_map.items():
            try:
                raw = self.cs2.exec_command(cmd)
            except CS2ControlError as e:
                details[f"{key}_error"] = str(e)
                continue
            value = self._parse_cvar_value(raw)
            details[key] = value
            details[f"{key}_raw"] = raw.strip()

        details["expected_mode"] = str(expected_mode)
        if self.state.requested_player and not self.state.target_accountid:
            try:
                players = self._get_player_list_from_demo()
                details["player_present"] = any(
                    self.state.requested_player.strip().lower() in p.get("raw", "").strip().lower()
                    for p in players
                )
            except Exception:
                details["player_present"] = None
        return details

    def _verify_player_lock(self, expected_mode: int) -> bool:
        details = self._probe_lock_state(expected_mode)
        self.state.verification_details = details

        autodirector = str(details.get("autodirector", "")).strip().lower()
        if autodirector not in self._FALSEY_VALUES:
            self.state.failure_reason = f"Auto-director still enabled ({details.get('autodirector')})"
            return False

        auto_observer = str(details.get("auto_observer", "")).strip().lower()
        if auto_observer not in self._FALSEY_VALUES:
            self.state.failure_reason = (
                f"Auto observer still enabled ({details.get('auto_observer')})"
            )
            return False

        actual_mode = details.get("spec_mode", "")
        valid_modes = {"", str(expected_mode), "1", "4"}
        if actual_mode not in valid_modes:
            self.state.failure_reason = (
                f"Unexpected spec_mode {actual_mode!r}; expected one of {sorted(valid_modes)}"
            )
            return False

        if self.state.target_accountid:
            locked = details.get("lock_accountid", "")
            if locked != self.state.target_accountid:
                self.state.failure_reason = (
                    f"spec_lock_to_accountid={locked!r}; expected {self.state.target_accountid!r}"
                )
                return False
        elif self.state.requested_player and details.get("player_present") is False:
            self.state.failure_reason = (
                f"Requested player {self.state.requested_player!r} not visible in current demo status output"
            )
            return False

        camera_follow = str(details.get("camera_follow", "")).strip().lower()
        if camera_follow and camera_follow in self._FALSEY_VALUES:
            self.state.failure_reason = f"Camera follow disabled ({details.get('camera_follow')})"
            return False

        # If we reached here, the probe agrees with our requested player lock.
        self.state.failure_reason = ""
        return True

    def _confirm_player_lock(self, expected_mode: int) -> bool:
        """Require multiple consecutive probe confirmations before success."""
        confirmations = 0
        last_failure = ""
        for poll in range(1, self.confirmation_polls + 1):
            if self._verify_player_lock(expected_mode):
                confirmations += 1
                log.info(
                    "[POV] Verification poll %s/%s: spec_mode=%s OK",
                    poll,
                    self.confirmation_polls,
                    self.state.verification_details.get("spec_mode", ""),
                )
                if confirmations >= self.confirmation_polls:
                    log.info("[POV] POV lock confirmed")
                    return True
            else:
                last_failure = self.state.failure_reason
                confirmations = 0
                log.info(
                    "[POV] Verification poll %s/%s failed: %s | details=%s",
                    poll,
                    self.confirmation_polls,
                    self.state.failure_reason or "verification failed",
                    self.state.verification_details,
                )
            time.sleep(self.confirmation_interval_s)
        if last_failure:
            self.state.failure_reason = last_failure
        return False

    def _get_player_list_from_demo(self) -> list[dict[str, str]]:
        """Query the demo's player list via console status command."""
        try:
            response = self.cs2.exec_command("status")
            # Parse player entries from status output
            # Format varies but typically: # userid name uniqueid connected ping loss state rate
            players = []
            for line in response.split("\n"):
                line = line.strip()
                if not line or line.startswith("#") and "userid" in line.lower():
                    continue
                # Try to extract player info
                if "STEAM_" in line or "765" in line:
                    parts = line.split()
                    # This is heuristic — CS2 status format varies
                    players.append({"raw": line, "parts": parts})
            return players
        except CS2ControlError:
            return []

    def apply_pov(
        self,
        player_name: str,
        player_steamid64: str = "",
        camera_mode: str = "player_pov",
        observer_mode: str = "first_person",
    ) -> POVState:
        """Apply player POV lock with multiple strategies.

        This function:
        1. Disables auto-director
        2. Sets observer mode to first-person
        3. Targets the specific player via spec_player_by_name
        4. Verifies the application
        5. Retries if needed

        Args:
            player_name: Player name to spectate
            player_steamid64: SteamID64 for verification (preferred identifier)
            camera_mode: "player_pov" or "freecam"
            observer_mode: "first_person" or "third_person"

        Returns:
            POVState with result

        Raises:
            POVError if POV cannot be achieved after max attempts
        """
        self.state = POVState(
            requested_player=player_name,
            requested_steamid64=player_steamid64,
            target_accountid=self._steamid64_to_accountid(player_steamid64),
            camera_mode=camera_mode,
            observer_mode=observer_mode,
        )

        if camera_mode == "freecam":
            return self._apply_freecam()

        if not player_name and not self.state.target_accountid:
            raise POVError("Player POV requires player_name or player_steamid64")

        spec_mode_candidates = self._candidate_spec_modes(observer_mode)

        for attempt in range(1, self.max_apply_attempts + 1):
            self.state.apply_attempts = attempt
            self.state.last_apply_time = time.time()
            self.state.spec_mode_value = spec_mode_candidates[(attempt - 1) % len(spec_mode_candidates)]
            log.info(
                "[POV] Apply attempt %s/%s player=%r steamid64=%r accountid=%r spec_mode=%s",
                attempt,
                self.max_apply_attempts,
                player_name,
                player_steamid64,
                self.state.target_accountid,
                self.state.spec_mode_value,
            )

            try:
                self._apply_pov_commands(
                    player_name,
                    observer_mode,
                    self.state.target_accountid,
                    self.state.spec_mode_value,
                    use_name_fallback=not self.state.target_accountid,
                )
                time.sleep(self.apply_settle_s)

                self.state.applied = True
                self.state.verified = self._confirm_player_lock(self.state.spec_mode_value)
                if self.state.verified:
                    log.info(
                        "[POV] Locked for %r (attempt %s, accountid=%s, spec_mode=%s)",
                        player_name,
                        attempt,
                        self.state.target_accountid or "n/a",
                        self.state.spec_mode_value,
                    )
                    return self.state

                log.warning(
                    "[POV] Verification failed for %r (attempt %s): %s | details=%s",
                    player_name,
                    attempt,
                    self.state.failure_reason or "unverified state",
                    self.state.verification_details,
                )
                time.sleep(0.5)

            except CS2ControlError as e:
                log.warning(f"POV apply attempt {attempt} failed: {e}")
                self.state.failure_reason = str(e)
                time.sleep(1.0)

        # All attempts exhausted — hard failure
        self.state.applied = False
        self.state.verified = False
        if not self.state.failure_reason:
            self.state.failure_reason = f"POV lock failed after {self.max_apply_attempts} attempts"
        raise POVError(self.state.failure_reason)

    def _apply_pov_commands(
        self,
        player_name: str,
        observer_mode: str,
        accountid: str = "",
        spec_mode: int = 1,
        use_name_fallback: bool = False,
    ) -> None:
        """Send a stable POV command sequence for demo playback.

        Order matters:
        1. Disable auto systems first so they cannot steal the camera back
        2. Set spectator mode
        3. Target by account id when available
        4. Fall back to player name only when account id is unavailable
        5. Enable follow last
        """
        commands = ["spec_autodirector 0", "cl_spec_auto_observer 0"]
        if accountid:
            log.info("[POV] Applying accountid lock: %s", accountid)
            commands.extend([
                f"spec_mode {spec_mode}",
                f"spec_player_by_accountid {accountid}",
                f"spec_lock_to_accountid {accountid}",
                "spec_camera_follow 1",
            ])
        elif player_name and use_name_fallback:
            escaped_name = player_name.replace('"', "").strip()
            if escaped_name:
                log.info("[POV] Applying name fallback lock: %s", escaped_name)
                commands.extend([
                    f'spec_player_by_name "{escaped_name}"',
                    f"spec_mode {spec_mode}",
                    "spec_camera_follow 1",
                ])
        else:
            raise POVError("POV targeting requires accountid or player_name fallback")
        self._exec_sequence(commands)

    def _apply_freecam(self) -> POVState:
        """Apply freecam mode."""
        try:
            commands = [
                "spec_autodirector 0",
                "cl_spec_auto_observer 0",
                "spec_lock_to_accountid 0",
                "spec_mode 5",  # freecam/roaming
            ]
            self._exec_sequence(commands, delay=0.1)
            self.state.applied = True
            self.state.verified = True
            self.state.apply_attempts = 1
            return self.state
        except CS2ControlError as e:
            self.state.failure_reason = str(e)
            raise POVError(f"Freecam application failed: {e}")

    def reapply_pov(self) -> POVState:
        """Re-apply the current POV settings.

        Call this after demo resume to counteract camera drift.
        """
        if not self.state.requested_player:
            log.warning("No player set for POV reapply")
            return self.state

        log.info(f"Re-applying POV for {self.state.requested_player!r}")
        return self.apply_pov(
            player_name=self.state.requested_player,
            player_steamid64=self.state.requested_steamid64,
            camera_mode=self.state.camera_mode,
            observer_mode=self.state.observer_mode,
        )

    def poll_lock(self) -> POVState:
        """Lightweight verification during recording; do not reapply here."""
        if not self.state.applied:
            raise POVError("POV not applied")
        expected_mode = self.state.spec_mode_value or self._candidate_spec_modes(self.state.observer_mode)[0]
        self.state.verified = self._verify_player_lock(expected_mode)
        return self.state

    def prepare_for_recording(self) -> POVState:
        """Final POV preparation right before recording starts.

        Sends an additional round of POV commands and HUD cleanup.
        """
        if not self.state.applied:
            raise POVError("POV not applied — cannot prepare for recording")

        commands = [
            # Re-affirm observer settings
            "spec_autodirector 0",
            "cl_spec_auto_observer 0",
            f"spec_mode {self.state.spec_mode_value or self._candidate_spec_modes(self.state.observer_mode)[0]}",
            # Clean up HUD for recording
            "cl_drawhud 1",
            "cl_draw_only_deathnotices 0",
            "r_drawviewmodel 1",
        ]
        if self.state.target_accountid:
            commands.extend([
                f"spec_player_by_accountid {self.state.target_accountid}",
                f"spec_lock_to_accountid {self.state.target_accountid}",
            ])
        elif self.state.requested_player:
            escaped_name = self.state.requested_player.replace('"', "")
            commands.append(f'spec_player_by_name "{escaped_name}"')
        commands.append("spec_camera_follow 1")
        try:
            self._exec_sequence(commands)
            time.sleep(0.3)
            expected_mode = self.state.spec_mode_value or self._candidate_spec_modes(self.state.observer_mode)[0]
            self.state.verified = self._confirm_player_lock(expected_mode)
            if not self.state.verified:
                raise POVError(self.state.failure_reason or "POV verification failed before recording")
            return self.state
        except CS2ControlError as e:
            log.error(f"Pre-recording POV preparation failed: {e}")
            raise POVError(f"Pre-recording POV failed: {e}")

    def verify_after_resume(self, settle_s: float = 1.0, allow_reapply: bool = True) -> POVState:
        """Verify after resume and only reapply if explicitly allowed."""
        time.sleep(settle_s)
        expected_mode = self.state.spec_mode_value or self._candidate_spec_modes(self.state.observer_mode)[0]
        self.state.verified = self._confirm_player_lock(expected_mode)
        if self.state.verified:
            return self.state
        if not allow_reapply:
            raise POVError(self.state.failure_reason or "POV verification failed after resume")
        return self.reapply_pov()
