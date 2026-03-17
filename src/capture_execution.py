"""
capture_execution.py
Dedicated capture/render execution layer for real in-game clip capture.

Separates:
  1. capture workspace preparation
  2. capture backend invocation
  3. game-control coordination during capture
  4. raw output integrity checks

Environment-specific dependencies are isolated in the capture backend
implementation so the orchestration layer stays replaceable and testable.
"""

from __future__ import annotations

import json
import logging
import shutil
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Protocol

from src.game_control import GameControlPort
from src.obs_controller import CaptureResult, CaptureStatus, record_clip_window

logger = logging.getLogger(__name__)


class CaptureExecutionStatus(str, Enum):
    PENDING = "pending"
    PREPARING_WORKSPACE = "preparing_workspace"
    STARTING_CAPTURE = "starting_capture"
    RECORDING = "recording"
    STOPPING_CAPTURE = "stopping_capture"
    VERIFYING_OUTPUT = "verifying_output"
    COMPLETED = "completed"
    FAILED = "failed"


class CaptureExecutionError(RuntimeError):
    code = "capture_execution_error"

    def __init__(self, message: str, *, detail: dict[str, Any] | None = None):
        super().__init__(message)
        self.detail = detail or {}


class CaptureBackendFailure(CaptureExecutionError):
    code = "capture_backend_failure"


class CaptureOutputMissing(CaptureExecutionError):
    code = "capture_output_missing"


class CaptureIntegrityFailure(CaptureExecutionError):
    code = "capture_integrity_failure"


@dataclass
class CaptureWorkspace:
    root_dir: str
    raw_output_dir: str
    logs_dir: str
    temp_dir: str
    manifest_path: str

    @classmethod
    def prepare(cls, root: str | Path, *, clip_id: str, job_id: str) -> "CaptureWorkspace":
        root_path = Path(root)
        base = cls._workspace_base(root_path, clip_id=clip_id, job_id=job_id)
        raw_output_dir = base / "raw"
        logs_dir = base / "logs"
        temp_dir = base / "tmp"
        for path in (base, raw_output_dir, logs_dir, temp_dir):
            path.mkdir(parents=True, exist_ok=True)
        manifest_path = base / "workspace.json"
        workspace = cls(
            root_dir=str(base),
            raw_output_dir=str(raw_output_dir),
            logs_dir=str(logs_dir),
            temp_dir=str(temp_dir),
            manifest_path=str(manifest_path),
        )
        manifest_path.write_text(
            json.dumps(
                {
                    "clip_id": clip_id,
                    "job_id": job_id,
                    "workspace": workspace.to_dict(),
                    "prepared_at": time.time(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return workspace

    @staticmethod
    def _workspace_base(root: Path, *, clip_id: str, job_id: str) -> Path:
        parts = list(root.parts)
        short_tag = f"{clip_id[-12:]}_{job_id[:8]}".strip("_")

        # Prefer a short sibling under outputs/generated/_capture_work
        # instead of nesting deeply inside the final clip directory.
        if "generated" in parts:
            idx = parts.index("generated")
            generated_root = Path(*parts[: idx + 1])
            return generated_root / "_capture_work" / short_tag

        return root / "_capture_work" / short_tag

    def to_dict(self) -> dict[str, Any]:
        return {
            "root_dir": self.root_dir,
            "raw_output_dir": self.raw_output_dir,
            "logs_dir": self.logs_dir,
            "temp_dir": self.temp_dir,
            "manifest_path": self.manifest_path,
        }


@dataclass(frozen=True)
class CaptureExecutionRequest:
    job_id: str
    clip_id: str
    demo_id: str
    duration_s: float
    workspace_root: str
    render_mode: str
    pre_roll_s: float = 2.0
    post_roll_s: float = 1.5
    resume_settle_s: float = 1.0
    minimum_output_bytes: int = 1024
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CaptureExecutionResult:
    execution_id: str
    status: CaptureExecutionStatus = CaptureExecutionStatus.PENDING
    backend_name: str = ""
    workspace: dict[str, Any] | None = None
    raw_output_path: str | None = None
    raw_output_size_bytes: int = 0
    capture_result: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    failure_code: str | None = None
    steps: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "status": self.status.value,
            "backend_name": self.backend_name,
            "workspace": self.workspace,
            "raw_output_path": self.raw_output_path,
            "raw_output_size_bytes": self.raw_output_size_bytes,
            "capture_result": self.capture_result,
            "warnings": list(self.warnings),
            "error": self.error,
            "failure_code": self.failure_code,
            "steps": list(self.steps),
        }


class CaptureBackend(Protocol):
    backend_name: str

    def capture_segment(
        self,
        request: CaptureExecutionRequest,
        *,
        workspace: CaptureWorkspace,
        on_capture_started: Callable[[], None] | None = None,
    ) -> CaptureResult:
        ...


class OBSCaptureBackend:
    backend_name = "obs_websocket"

    def __init__(self, obs_config: dict[str, Any] | None = None):
        self.obs_config = obs_config or {}

    def capture_segment(
        self,
        request: CaptureExecutionRequest,
        *,
        workspace: CaptureWorkspace,
        on_capture_started: Callable[[], None] | None = None,
    ) -> CaptureResult:
        return record_clip_window(
            duration_s=request.duration_s,
            output_dir=workspace.raw_output_dir,
            clip_id=request.clip_id,
            obs_config=self.obs_config,
            pre_roll_s=request.pre_roll_s,
            post_roll_s=request.post_roll_s,
            on_recording_started=on_capture_started,
        )


class CaptureExecutionService:
    def __init__(
        self,
        *,
        game_control: GameControlPort,
        backend: CaptureBackend,
        logger_: logging.Logger | None = None,
    ):
        self.game_control = game_control
        self.backend = backend
        self.logger = logger_ or logger

    def execute(self, request: CaptureExecutionRequest) -> CaptureExecutionResult:
        result = CaptureExecutionResult(
            execution_id=f"ce_{uuid.uuid4().hex[:12]}",
            backend_name=self.backend.backend_name,
        )
        workspace: CaptureWorkspace | None = None
        self.logger.info(
            "capture_execution start job=%s clip=%s duration=%.2fs backend=%s",
            request.job_id,
            request.clip_id,
            request.duration_s,
            self.backend.backend_name,
        )
        try:
            result.status = CaptureExecutionStatus.PREPARING_WORKSPACE
            workspace = CaptureWorkspace.prepare(
                request.workspace_root,
                clip_id=request.clip_id,
                job_id=request.job_id,
            )
            result.workspace = workspace.to_dict()
            result.steps.append(
                {
                    "step": "prepare_workspace",
                    "workspace": result.workspace,
                }
            )

            def _on_capture_started() -> None:
                result.steps.append({"step": "capture_started_callback"})
                self._resume_gameplay(request.resume_settle_s)

            result.status = CaptureExecutionStatus.STARTING_CAPTURE
            capture = self.backend.capture_segment(
                request,
                workspace=workspace,
                on_capture_started=_on_capture_started,
            )
            result.capture_result = capture.to_dict()
            result.steps.extend(capture.steps)
            result.warnings.extend(capture.warnings)

            if capture.status != CaptureStatus.COMPLETED:
                raise CaptureBackendFailure(
                    capture.error or "Capture backend did not complete successfully",
                    detail=capture.to_dict(),
                )

            result.status = CaptureExecutionStatus.STOPPING_CAPTURE
            self._pause_after_capture(result)

            result.status = CaptureExecutionStatus.VERIFYING_OUTPUT
            raw_output = self._verify_raw_output(request, capture)
            result.raw_output_path = str(raw_output)
            result.raw_output_size_bytes = raw_output.stat().st_size
            result.steps.append(
                {
                    "step": "verify_raw_output",
                    "path": str(raw_output),
                    "size_bytes": result.raw_output_size_bytes,
                }
            )

            result.status = CaptureExecutionStatus.COMPLETED
            return result
        except CaptureExecutionError as exc:
            result.status = CaptureExecutionStatus.FAILED
            result.error = str(exc)
            result.failure_code = exc.code
            result.steps.append(
                {
                    "step": "capture_execution_failed",
                    "error": str(exc),
                    "failure_code": exc.code,
                    "detail": getattr(exc, "detail", {}),
                }
            )
            self.logger.error("capture_execution failed job=%s: %s", request.job_id, exc)
            return result
        except Exception as exc:
            result.status = CaptureExecutionStatus.FAILED
            result.error = str(exc)
            result.failure_code = "unexpected_capture_exception"
            result.steps.append(
                {
                    "step": "capture_execution_failed",
                    "error": str(exc),
                    "failure_code": result.failure_code,
                }
            )
            self.logger.error(
                "capture_execution unexpected failure job=%s: %s",
                request.job_id,
                exc,
                exc_info=True,
            )
            return result

    def _resume_gameplay(self, settle_s: float) -> None:
        try:
            if hasattr(self.game_control, "set_demo_timescale"):
                self.game_control.set_demo_timescale(1.0)  # type: ignore[attr-defined]
        except Exception as exc:
            self.logger.warning("demo_timescale failed before resume: %s", exc)
        resume = self.game_control.resume_demo()
        if not resume.get("success"):
            raise CaptureBackendFailure(
                f"Failed to resume demo before capture: {resume.get('error') or 'unknown error'}",
                detail=resume,
            )

        # ── Apply camera IMMEDIATELY after resume ────────────────────────
        # Minimize the freecam window: the demo just resumed and CS2 may
        # have reverted to auto-director.  Send camera commands right away
        # so only a few frames are captured with the wrong view.
        if hasattr(self.game_control, "reapply_last_camera"):
            try:
                self.game_control.reapply_last_camera()  # type: ignore[attr-defined]
            except Exception as exc:
                self.logger.warning("immediate camera reapply after resume failed (will retry): %s", exc)

        time.sleep(max(0.0, settle_s))

        # ── Re-apply camera after settle — authoritative check ───────────
        # This second application catches any drift that occurred during the
        # settle period.  Failure here is HARD — the POV cannot be trusted.
        if hasattr(self.game_control, "reapply_last_camera"):
            try:
                camera = self.game_control.reapply_last_camera()  # type: ignore[attr-defined]
                if not camera.get("success", False):
                    error_info = camera.get("error") or camera.get("warnings")
                    self.logger.error(
                        "camera reapply after resume FAILED: %s — "
                        "POV may have drifted to freecam/observer",
                        error_info,
                    )
                    raise CaptureBackendFailure(
                        f"Camera re-apply failed after demo resume: {error_info}. "
                        "The intended player POV could not be maintained.",
                        detail=camera,
                    )
            except CaptureBackendFailure:
                raise
            except Exception as exc:
                self.logger.error("camera reapply after resume exception: %s", exc)
                raise CaptureBackendFailure(
                    f"Camera re-apply failed after demo resume: {exc}",
                    detail={"exception": str(exc)},
                ) from exc

    def _pause_after_capture(self, result: CaptureExecutionResult) -> None:
        try:
            pause = self.game_control.pause_demo()
            result.steps.append({"step": "pause_after_capture", **pause})
            if not pause.get("success"):
                result.warnings.append(
                    f"pause_after_capture_failed: {pause.get('error') or 'unknown error'}"
                )
        except Exception as exc:
            result.warnings.append(f"pause_after_capture_exception: {exc}")

    def _verify_raw_output(
        self,
        request: CaptureExecutionRequest,
        capture: CaptureResult,
    ) -> Path:
        raw_output = str(capture.output_path or "").strip()
        if not raw_output:
            raise CaptureOutputMissing(
                "Capture backend reported success but produced no output path",
                detail=capture.to_dict(),
            )
        raw_path = Path(raw_output)
        if not raw_path.is_file():
            raise CaptureOutputMissing(
                f"Raw capture output not found: {raw_output}",
                detail=capture.to_dict(),
            )
        size_bytes = raw_path.stat().st_size
        if size_bytes <= 0:
            raise CaptureIntegrityFailure(
                f"Raw capture output is empty: {raw_output}",
                detail={"path": raw_output, "size_bytes": size_bytes},
            )
        if size_bytes < max(1, int(request.minimum_output_bytes)):
            raise CaptureIntegrityFailure(
                f"Raw capture output is smaller than expected: {size_bytes} bytes",
                detail={
                    "path": raw_output,
                    "size_bytes": size_bytes,
                    "minimum_output_bytes": request.minimum_output_bytes,
                },
            )
        return raw_path


def promote_raw_capture_to_final(raw_output_path: str | Path, final_output_path: str | Path) -> Path:
    raw = Path(raw_output_path)
    final = Path(final_output_path)
    final.parent.mkdir(parents=True, exist_ok=True)
    if raw.resolve() == final.resolve():
        return final
    try:
        shutil.move(str(raw), str(final))
    except Exception:
        shutil.copy2(str(raw), str(final))
    return final
