"""Run MolScribe inside the MolScribe venv (subprocess)."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

WORKER = Path(__file__).resolve().parents[1] / "workers" / "molscribe_predict_batch.py"


def _venv_python() -> Path:
    scripts = "Scripts" if sys.platform == "win32" else "bin"
    name = "python.exe" if sys.platform == "win32" else "python"
    path = settings.molscribe_venv / scripts / name
    if not path.is_file():
        raise FileNotFoundError(
            f"MolScribe venv python not found at {path}. "
            "Build/install the MolScribe venv first."
        )
    return path


def run_molscribe_batch(
    image_paths: list[Path],
    *,
    output_json: Path,
    device: str | None = None,
) -> list[dict[str, Any]]:
    if not image_paths:
        return []

    entries = [{"id": str(i), "path": str(p.resolve())} for i, p in enumerate(image_paths)]
    with tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False, encoding="utf-8"
    ) as handle:
        json.dump(entries, handle, ensure_ascii=False)
        images_json = handle.name

    device = device or settings.molscribe_device
    env = os.environ.copy()
    env.setdefault("HF_HOME", str(settings.cache_dir / "huggingface"))
    env["NO_ALBUMENTATIONS_UPDATE"] = "1"

    cmd = [
        str(_venv_python()),
        str(WORKER),
        "--images-json",
        images_json,
        "--output",
        str(output_json),
        "--device",
        device,
        "--model-repo",
        settings.molscribe_model_repo,
        "--checkpoint",
        settings.molscribe_checkpoint,
    ]
    logger.info("Running MolScribe worker on %d images", len(image_paths))
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            env=env,
            timeout=settings.mineru_timeout,
        )
        if result.returncode != 0:
            logger.error("MolScribe stderr:\n%s", result.stderr)
            raise RuntimeError(
                f"MolScribe worker failed ({result.returncode}):\n{result.stderr[-4000:]}"
            )
        if result.stdout:
            logger.info("MolScribe stdout: %s", result.stdout.strip()[-1000:])
    finally:
        try:
            os.unlink(images_json)
        except OSError:
            pass

    if not output_json.is_file():
        return []
    return json.loads(output_json.read_text(encoding="utf-8"))
