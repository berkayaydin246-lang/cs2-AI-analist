"""
hlae_renderer.py
Deterministic HLAE-based clip renderer for CS2 POV MVP clips.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.cs2_config import CS2Config
from src.cs2_controller import CS2Controller
from src.ffmpeg_finalize import FFmpegFinalizeResult, FinalizeError, finalize_render_output
from src.hlae_commands import (
    build_bootstrap_commands,
    build_mirv_streams_setup_commands,
    build_mirv_streams_start_commands,
    build_mirv_streams_stop_commands,
    build_pov_commands,
)
from src.hlae_launcher import HLAELaunchError, HLAELauncher
from src.playback_controller import HLAEPlaybackController
from src.pov_controller import POVController, POVError
from src.render_recipe import RenderRecipe
from src.render_result import RenderResult

log = logging.getLogger(__name__)


@dataclass
class HLAERenderer:
    config: CS2Config

    @staticmethod
    def _record_runtime_event(
        commands_log_path: Path,
        diagnostics: dict[str, Any],
        prefix: str,
        message: str,
        **fields: Any,
    ) -> None:
        suffix = ""
        if fields:
            suffix = f" | {json.dumps(fields, sort_keys=True, default=str)}"
        HLAERenderer._record_command(
            commands_log_path,
            diagnostics,
            f"[{prefix}] {message}{suffix}",
            "",
        )

    def render(self, recipe: RenderRecipe) -> RenderResult:
        recipe.validate()
        result = RenderResult.start(recipe.job_id)

        output_dir = Path(recipe.output_dir)
        raw_dir = output_dir / "raw"
        take_dir = raw_dir / recipe.raw_name_prefix
        logs_dir = output_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        raw_dir.mkdir(parents=True, exist_ok=True)
        commands_log_path = logs_dir / "commands.log"
        diagnostics: dict[str, Any] = {
            "recipe": recipe.to_dict(),
            "phases": [],
            "commands": [],
        }
        result.log_path = str(logs_dir / "renderer.log")

        cs2 = CS2Controller(config=self.config)
        cs2.command_observer = lambda cmd, response: self._record_command(
            commands_log_path, diagnostics, cmd, response
        )
        launcher = HLAELauncher(config=self.config)
        playback = HLAEPlaybackController(config=self.config, cs2=cs2)
        pov = POVController(cs2=cs2)

        try:
            stale = launcher.find_stale_processes()
            self._record_runtime_event(
                commands_log_path,
                diagnostics,
                "SESSION",
                "pre-launch stale process scan",
                found=stale,
            )
            if stale:
                cleaned = launcher.cleanup_stale_processes()
                self._record_runtime_event(
                    commands_log_path,
                    diagnostics,
                    "SESSION",
                    "pre-launch stale processes cleaned",
                    cleaned=cleaned,
                )
                time.sleep(0.8)

            self._phase(diagnostics, "launch_hlae")
            session = launcher.launch(recipe, logs_dir)
            result.log_path = session.stdout_log_path
            diagnostics["launch_command"] = session.command
            self._record_runtime_event(
                commands_log_path,
                diagnostics,
                "SESSION",
                "process launched",
                pid=session.process.pid if session.process else None,
                launch_command=session.command,
                demo_path=recipe.demo_path,
            )

            self._phase(diagnostics, "wait_for_netcon")
            launcher.wait_for_netcon(cs2, timeout_s=self.config.hlae_render_load_timeout_s)
            self._record_runtime_event(
                commands_log_path,
                diagnostics,
                "SESSION",
                "netcon connected",
                netcon_port=self.config.netcon_port,
            )

            self._phase(diagnostics, "bootstrap")
            cs2.exec_commands(build_bootstrap_commands(), delay=0.08)

            startup_settle_s = max(0.0, float(self.config.hlae_render_startup_settle_s))
            self._record_runtime_event(
                commands_log_path,
                diagnostics,
                "SESSION",
                "startup settle begin",
                settle_s=startup_settle_s,
            )
            if startup_settle_s > 0:
                time.sleep(startup_settle_s)
            self._record_runtime_event(
                commands_log_path,
                diagnostics,
                "SESSION",
                "startup settle end",
            )

            self._phase(diagnostics, "prepare_playback")
            playback_result = playback.prepare_recipe(recipe)
            diagnostics["playback_result"] = playback_result.to_dict()
            if playback_result.status != "ready":
                return result.mark_failed(
                    playback_result.failure_code or "demo_load_failure",
                    playback_result.error or "Playback preparation failed",
                    diagnostics=diagnostics,
                )

            self._phase(diagnostics, "apply_pov")
            pov_state = pov.apply_pov(
                player_name=recipe.player_name,
                player_steamid64=recipe.player_steamid64,
                camera_mode="player_pov",
                observer_mode=recipe.observer_mode,
            )
            diagnostics["pov_apply"] = pov_state.to_dict()
            pov.prepare_for_recording()
            diagnostics["pov_pre_record"] = pov.state.to_dict()

            self._phase(diagnostics, "configure_mirv_streams")
            setup_commands = build_mirv_streams_setup_commands(
                recipe,
                raw_dir,
                image_format=self.config.hlae_render_image_format,
            )
            cs2.exec_commands(setup_commands, delay=0.08)
            raw_discovery_started_at = time.time()

            self._phase(diagnostics, "record_start")
            cs2.exec_commands(build_mirv_streams_start_commands(recipe), delay=0.08)

            self._phase(diagnostics, "resume_playback")
            playback.resume(recipe.render_duration_seconds)
            pov_after_resume = pov.verify_after_resume(settle_s=0.45, allow_reapply=False)
            diagnostics["pov_after_resume"] = pov_after_resume.to_dict()

            time.sleep(recipe.render_duration_seconds)

            self._phase(diagnostics, "record_stop")
            playback.stop()
            cs2.exec_commands(build_mirv_streams_stop_commands(), delay=0.08)

            self._phase(diagnostics, "discover_raw_output")
            raw_output = self._wait_for_raw_output(
                take_dir=take_dir,
                started_after=raw_discovery_started_at,
                timeout_s=self.config.hlae_render_output_timeout_s,
            )
            diagnostics["raw_output"] = raw_output

            self._phase(diagnostics, "finalize")
            finalize_result = finalize_render_output(
                self.config,
                raw_path=raw_output,
                output_dir=str(output_dir),
                output_name=recipe.output_name,
                fps=recipe.fps,
                width=recipe.width,
                height=recipe.height,
                image_format=self.config.hlae_render_image_format,
                container=recipe.video_container,
                encode_preset=recipe.encode_preset,
            )
            diagnostics["finalize_result"] = finalize_result.to_dict()

            artifact_meta = {
                "recipe": recipe.to_dict(),
                "playback": playback_result.to_dict(),
                "pov": pov.state.to_dict(),
                "finalize": finalize_result.to_dict(),
            }
            with open(output_dir / "artifact_hlae_debug.json", "w", encoding="utf-8") as f:
                json.dump(artifact_meta, f, indent=2)

            return result.mark_completed(
                final_video_path=finalize_result.final_video_path,
                raw_output_path=finalize_result.raw_output_path,
                thumbnail_path=finalize_result.thumbnail_path,
                diagnostics=diagnostics,
            )

        except HLAELaunchError as e:
            return result.mark_failed("launch_failure", str(e), diagnostics=diagnostics)
        except POVError as e:
            return result.mark_failed("pov_failure", str(e), diagnostics=diagnostics)
        except FinalizeError as e:
            return result.mark_failed("finalize_failure", str(e), diagnostics=diagnostics)
        except Exception as e:
            log.exception("Unexpected HLAE render failure for job %s", recipe.job_id)
            return result.mark_failed("render_failure", str(e), diagnostics=diagnostics)
        finally:
            try:
                playback.cleanup()
            except Exception:
                pass
            try:
                launcher.shutdown()
            except Exception:
                pass
            try:
                cs2.cleanup()
            except Exception:
                pass

    @staticmethod
    def _phase(diagnostics: dict[str, Any], name: str) -> None:
        diagnostics.setdefault("phases", []).append(
            {"phase": name, "ts": time.time()}
        )

    @staticmethod
    def _record_command(
        commands_log_path: Path,
        diagnostics: dict[str, Any],
        cmd: str,
        response: str,
    ) -> None:
        entry = {
            "ts": time.time(),
            "command": cmd,
            "response": (response or "")[:1000],
        }
        diagnostics.setdefault("commands", []).append(entry)
        with open(commands_log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    def _wait_for_raw_output(
        self,
        *,
        take_dir: Path,
        started_after: float,
        timeout_s: int,
    ) -> str:
        deadline = time.time() + timeout_s
        image_glob = f"*.{self.config.hlae_render_image_format.lstrip('.')}"
        while time.time() < deadline:
            frame_matches = [
                p for p in take_dir.glob(image_glob)
                if p.stat().st_mtime >= started_after
            ]
            if frame_matches:
                return str(take_dir)
            video_matches = []
            for ext in (".mp4", ".mov", ".mkv", ".avi", ".webm"):
                video_matches.extend(
                    p for p in take_dir.glob(f"*{ext}")
                    if p.stat().st_mtime >= started_after
                )
            if video_matches:
                return str(sorted(video_matches)[0])
            time.sleep(0.5)
        raise FinalizeError(f"No mirv_streams output appeared under {take_dir} within {timeout_s}s")
