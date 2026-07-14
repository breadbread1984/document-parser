"""Run MinerU CLI inside the MinerU venv (subprocess)."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)


def _mineru_bin() -> Path:
    scripts = "Scripts" if sys.platform == "win32" else "bin"
    name = "mineru.exe" if sys.platform == "win32" else "mineru"
    path = settings.mineru_venv / scripts / name
    if not path.is_file():
        raise FileNotFoundError(
            f"mineru not found at {path}. Build/install the MinerU venv first."
        )
    return path


def find_markdown(output_dir: Path, pdf_stem: str) -> Path:
    candidates = [
        output_dir / f"{pdf_stem}.md",
        output_dir / pdf_stem / f"{pdf_stem}.md",
        output_dir / pdf_stem / "ocr" / f"{pdf_stem}.md",
        output_dir / pdf_stem / "auto" / f"{pdf_stem}.md",
        output_dir / pdf_stem / "txt" / f"{pdf_stem}.md",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    for md in sorted(output_dir.rglob("*.md")):
        if md.name.endswith("_final.md"):
            continue
        return md
    raise FileNotFoundError(f"No MinerU markdown under {output_dir}")


def find_images_dir(md_path: Path) -> Optional[Path]:
    for candidate in (md_path.parent / "images", md_path.parent.parent / "images"):
        if candidate.is_dir():
            return candidate
    for d in md_path.parent.rglob("images"):
        if d.is_dir():
            return d
    return None


def run_mineru(pdf_path: Path, output_dir: Path) -> Path:
    """Parse PDF with MinerU OCR; return path to intermediate markdown."""
    output_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(_mineru_bin()),
        "-p",
        str(pdf_path),
        "-o",
        str(output_dir),
        "-b",
        settings.mineru_backend,
        "-m",
        settings.mineru_method,
    ]
    env = os.environ.copy()
    # Prefer shared model/cache volumes
    env.setdefault("HF_HOME", str(settings.cache_dir / "huggingface"))
    env.setdefault("MINERU_MODEL_SOURCE", os.environ.get("MINERU_MODEL_SOURCE", "huggingface"))

    logger.info("Running MinerU: %s", " ".join(cmd))
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
        timeout=settings.mineru_timeout,
        env=env,
    )
    if result.returncode != 0:
        logger.error("MinerU stderr:\n%s", result.stderr)
        raise RuntimeError(
            f"MinerU exited with code {result.returncode}:\n{result.stderr[-4000:]}"
        )
    if result.stdout:
        logger.info("MinerU stdout (tail):\n%s", result.stdout[-2000:])

    return find_markdown(output_dir, pdf_path.stem)
