"""Job metadata store (filesystem JSON)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from app.config import settings


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_job_id() -> str:
    return uuid.uuid4().hex


def job_dir(job_id: str) -> Path:
    path = settings.jobs_dir / job_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def meta_path(job_id: str) -> Path:
    return job_dir(job_id) / "meta.json"


def write_meta(job_id: str, **fields: Any) -> dict[str, Any]:
    path = meta_path(job_id)
    meta: dict[str, Any] = {}
    if path.is_file():
        meta = json.loads(path.read_text(encoding="utf-8"))
    meta.update(fields)
    meta["job_id"] = job_id
    meta["updated_at"] = _utc_now()
    path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return meta


def read_meta(job_id: str) -> Optional[dict[str, Any]]:
    path = meta_path(job_id)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def create_job(filename: str) -> dict[str, Any]:
    job_id = new_job_id()
    jdir = job_dir(job_id)
    (jdir / "work").mkdir(exist_ok=True)
    return write_meta(
        job_id,
        status=JobStatus.queued.value,
        filename=filename,
        created_at=_utc_now(),
        error=None,
        result_markdown=None,
        stats={},
    )
