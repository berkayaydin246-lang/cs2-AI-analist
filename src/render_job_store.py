"""
render_job_store.py
Persistent per-job state + structured event logging for render workers.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.utils import atomic_json_write


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RenderJobStateStore:
    def __init__(self, base_dir: str | Path):
        self.base_dir = Path(base_dir)
        self.jobs_dir = self.base_dir / "jobs"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)

    def job_dir(self, job_id: str) -> Path:
        path = self.jobs_dir / str(job_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def state_path(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "state.json"

    def events_path(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "events.jsonl"

    def write_job_state(self, payload: dict[str, Any]) -> str:
        job_id = str(payload.get("job_id") or "")
        if not job_id:
            raise ValueError("job_id is required for job state persistence")
        path = self.state_path(job_id)
        atomic_json_write(path, payload)
        return str(path)

    def append_event(self, job_id: str, *, level: str = "info", event: str, **fields: Any) -> str:
        path = self.events_path(job_id)
        record = {
            "timestamp": utc_now_iso(),
            "job_id": str(job_id),
            "level": str(level),
            "event": str(event),
            **fields,
        }
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=True) + "\n")
        return str(path)

    def read_events(self, job_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
        path = self.events_path(job_id)
        if not path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except Exception:
                continue
            if isinstance(payload, dict):
                rows.append(payload)
        return rows[-max(1, int(limit)) :]

    def sync_jobs(self, jobs: list[dict[str, Any]]) -> None:
        active_job_ids = set()
        for payload in jobs:
            job_id = str(payload.get("job_id") or "")
            if not job_id:
                continue
            active_job_ids.add(job_id)
            self.write_job_state(payload)

        for state_path in self.jobs_dir.glob("*/state.json"):
            job_id = state_path.parent.name
            if job_id in active_job_ids:
                continue
            # Preserve event logs, remove stale state snapshot if queue no longer tracks the job.
            try:
                state_path.unlink(missing_ok=True)
            except Exception:
                pass
