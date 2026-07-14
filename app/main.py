"""FastAPI service: upload PDF → OCR + structure SMILES → download Markdown."""

from __future__ import annotations

import logging
import shutil
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.config import settings
from app.jobs import JobStatus, create_job, job_dir, read_meta, write_meta
from app.pipeline import process_pdf

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-7s] %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("api")

settings.jobs_dir.mkdir(parents=True, exist_ok=True)
settings.cache_dir.mkdir(parents=True, exist_ok=True)

app = FastAPI(
    title="Document Parser Service",
    description=(
        "Upload a PDF; MinerU OCR produces Markdown; chemical structure images "
        "are converted to SMILES via MolScribe (dual-venv subprocess)."
    ),
    version="1.0.0",
)

_executor = ThreadPoolExecutor(max_workers=1)


class JobCreated(BaseModel):
    job_id: str
    status: str
    message: str


class JobInfo(BaseModel):
    job_id: str
    status: str
    filename: str | None = None
    error: str | None = None
    stats: dict | None = None
    created_at: str | None = None
    updated_at: str | None = None
    result_url: str | None = None


def _run_job(job_id: str, pdf_path: Path) -> None:
    write_meta(job_id, status=JobStatus.running.value, error=None)
    work = job_dir(job_id) / "work"
    try:
        result = process_pdf(pdf_path, work)
        final_md: Path = result["final_md"]
        # Copy to a stable download path
        download_md = job_dir(job_id) / "result.md"
        shutil.copy2(final_md, download_md)
        images_src = final_md.parent / "images"
        if images_src.is_dir():
            images_dst = job_dir(job_id) / "images"
            if images_dst.exists():
                shutil.rmtree(images_dst)
            shutil.copytree(images_src, images_dst)
        write_meta(
            job_id,
            status=JobStatus.done.value,
            result_markdown=str(download_md),
            stats=result["stats"],
            error=None,
        )
        logger.info("Job %s done", job_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Job %s failed", job_id)
        write_meta(
            job_id,
            status=JobStatus.failed.value,
            error=f"{exc}\n{traceback.format_exc()[-2000:]}",
        )


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "mineru_venv": str(settings.mineru_venv),
        "molscribe_venv": str(settings.molscribe_venv),
    }


@app.post("/v1/jobs", response_model=JobCreated)
async def submit_job(
    file: UploadFile = File(..., description="PDF document"),
) -> JobCreated:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only .pdf uploads are supported")

    meta = create_job(file.filename)
    job_id = meta["job_id"]
    pdf_path = job_dir(job_id) / "input.pdf"

    data = await file.read()
    max_bytes = settings.max_upload_mb * 1024 * 1024
    if len(data) > max_bytes:
        write_meta(job_id, status=JobStatus.failed.value, error="file too large")
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds {settings.max_upload_mb} MB limit",
        )
    pdf_path.write_bytes(data)

    # Single worker thread: MinerU/MolScribe are heavy; avoid concurrent GPU/CPU thrash
    _executor.submit(_run_job, job_id, pdf_path)
    return JobCreated(
        job_id=job_id,
        status=JobStatus.queued.value,
        message="Job accepted. Poll GET /v1/jobs/{job_id}; download GET /v1/jobs/{job_id}/markdown when done.",
    )


@app.get("/v1/jobs/{job_id}", response_model=JobInfo)
def get_job(job_id: str) -> JobInfo:
    meta = read_meta(job_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Job not found")
    result_url = None
    if meta.get("status") == JobStatus.done.value:
        result_url = f"/v1/jobs/{job_id}/markdown"
    return JobInfo(
        job_id=job_id,
        status=meta.get("status", "unknown"),
        filename=meta.get("filename"),
        error=meta.get("error"),
        stats=meta.get("stats"),
        created_at=meta.get("created_at"),
        updated_at=meta.get("updated_at"),
        result_url=result_url,
    )


@app.get("/v1/jobs/{job_id}/markdown")
@app.get("/v1/jobs/{job_id}/result.md")
def download_markdown(job_id: str) -> FileResponse:
    meta = read_meta(job_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Job not found")
    if meta.get("status") != JobStatus.done.value:
        raise HTTPException(
            status_code=409,
            detail=f"Job not ready (status={meta.get('status')})",
        )
    path = Path(meta.get("result_markdown") or job_dir(job_id) / "result.md")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Markdown result missing")
    stem = Path(meta.get("filename") or "result").stem
    return FileResponse(
        path,
        media_type="text/markdown; charset=utf-8",
        filename=f"{stem}_final.md",
    )


@app.get("/v1/jobs/{job_id}/images/{image_name}")
def download_image(job_id: str, image_name: str) -> FileResponse:
    """Optional: fetch a kept original image referenced from the markdown."""
    meta = read_meta(job_id)
    if not meta or meta.get("status") != JobStatus.done.value:
        raise HTTPException(status_code=404, detail="Not available")
    # Prevent path traversal
    safe = Path(image_name).name
    path = job_dir(job_id) / "images" / safe
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(path)
