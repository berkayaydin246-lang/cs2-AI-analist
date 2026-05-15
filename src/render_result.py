"""
render_result.py
Structured HLAE render result model.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class RenderResult:
    job_id: str
    success: bool = False
    status: str = "pending"
    final_video_path: str = ""
    raw_output_path: str = ""
    thumbnail_path: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0
    duration_seconds: float = 0.0
    log_path: str = ""
    error_code: str = ""
    error_message: str = ""
    diagnostics: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def start(cls, job_id: str) -> "RenderResult":
        return cls(job_id=job_id, status="starting", started_at=time.time())

    def mark_failed(self, error_code: str, error_message: str, **diagnostics: Any) -> "RenderResult":
        self.success = False
        self.status = "failed"
        self.error_code = error_code
        self.error_message = error_message
        if diagnostics:
            self.diagnostics.update(diagnostics)
        self.finished_at = time.time()
        self.duration_seconds = max(0.0, self.finished_at - (self.started_at or self.finished_at))
        return self

    def mark_completed(
        self,
        *,
        final_video_path: str,
        raw_output_path: str,
        thumbnail_path: str = "",
        **diagnostics: Any,
    ) -> "RenderResult":
        self.success = True
        self.status = "completed"
        self.final_video_path = final_video_path
        self.raw_output_path = raw_output_path
        self.thumbnail_path = thumbnail_path
        if diagnostics:
            self.diagnostics.update(diagnostics)
        self.finished_at = time.time()
        self.duration_seconds = max(0.0, self.finished_at - (self.started_at or self.finished_at))
        return self

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
