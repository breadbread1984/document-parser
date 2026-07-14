"""Orchestrate MinerU OCR → MolScribe SMILES → final Markdown (subprocess only)."""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path
from typing import Any

from app.config import settings
from app.mineru_runner import find_images_dir, run_mineru
from app.molscribe_runner import run_molscribe_batch

logger = logging.getLogger(__name__)

_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


def _resolve_image(
    rel_path: str, images_dir: Path | None, base_dir: Path
) -> Path | None:
    candidates = []
    if images_dir:
        candidates.append(images_dir / Path(rel_path).name)
        candidates.append(images_dir / rel_path)
        candidates.append(base_dir / rel_path)
    candidates.append(base_dir / rel_path)
    candidates.append(Path(rel_path))
    for c in candidates:
        if c.is_file():
            return c
    return None


def _replace_with_smiles(
    md_text: str,
    image_refs: list[tuple[str, str]],
    resolved: dict[tuple[str, str], Path | None],
    predictions: dict[Path, dict[str, Any]],
) -> tuple[str, int]:
    threshold = settings.molscribe_confidence
    template = settings.smiles_template
    replaced = 0

    for alt, rel_path in image_refs:
        img_path = resolved.get((alt, rel_path))
        pred = predictions.get(img_path) if img_path else None
        if not pred:
            continue
        confidence = float(pred.get("confidence") or 0.0)
        smiles = (pred.get("smiles") or "").strip()
        if not smiles or confidence < threshold:
            continue

        smiles_text = template.format(smiles=smiles, confidence=confidence)
        if settings.keep_original_image:
            replacement = f"{smiles_text}\n\n![{alt}]({rel_path})"
        else:
            replacement = smiles_text

        old_tag = f"![{alt}]({rel_path})"
        if old_tag in md_text:
            md_text = md_text.replace(old_tag, replacement, 1)
            replaced += 1
            logger.info("Replaced %s → %s (conf=%.3f)", rel_path, smiles, confidence)

    return md_text, replaced


def process_pdf(pdf_path: Path, work_dir: Path) -> dict[str, Any]:
    """
    Full pipeline for one PDF.

    Returns dict with final_md path and stats.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    mineru_out = work_dir / "mineru"
    final_md_path = work_dir / f"{pdf_path.stem}_final.md"

    logger.info("[1/4] MinerU OCR: %s", pdf_path.name)
    intermediate_md = run_mineru(pdf_path, mineru_out)

    logger.info("[2/4] Locate images in markdown")
    md_text = intermediate_md.read_text(encoding="utf-8")
    images_dir = find_images_dir(intermediate_md)
    image_refs = _IMAGE_RE.findall(md_text)
    logger.info("Found %d image references", len(image_refs))

    resolved: dict[tuple[str, str], Path | None] = {}
    for alt, rel in image_refs:
        resolved[(alt, rel)] = _resolve_image(rel, images_dir, intermediate_md.parent)

    valid_paths = [p for p in resolved.values() if p is not None]
    # unique preserve order
    seen: set[Path] = set()
    unique_paths: list[Path] = []
    for p in valid_paths:
        if p not in seen:
            seen.add(p)
            unique_paths.append(p)

    predictions: dict[Path, dict[str, Any]] = {}
    if unique_paths:
        logger.info("[3/4] MolScribe on %d images", len(unique_paths))
        pred_json = work_dir / "molscribe_predictions.json"
        batch = run_molscribe_batch(unique_paths, output_json=pred_json)
        by_path = {Path(item["path"]): item for item in batch if "path" in item}
        predictions = by_path
    else:
        logger.info("[3/4] No images — skip MolScribe")

    logger.info("[4/4] Write final markdown")
    final_text, replaced = _replace_with_smiles(
        md_text, image_refs, resolved, predictions
    )

    if images_dir and images_dir.is_dir():
        dest_images = final_md_path.parent / "images"
        if dest_images.exists():
            shutil.rmtree(dest_images)
        shutil.copytree(images_dir, dest_images)

    final_md_path.write_text(final_text, encoding="utf-8")

    stats = {
        "images_found": len(image_refs),
        "images_resolved": len(unique_paths),
        "smiles_replaced": replaced,
        "intermediate_md": str(intermediate_md),
        "final_md": str(final_md_path),
    }
    logger.info("Done: %s", stats)
    return {"final_md": final_md_path, "stats": stats}
