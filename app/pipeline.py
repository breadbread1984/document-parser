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
            return c.resolve()
    return None


def _index_predictions(
    batch: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Index by basename so path string differences cannot drop matches."""
    by_name: dict[str, dict[str, Any]] = {}
    for item in batch:
        raw = item.get("path") or ""
        name = Path(raw).name
        if name:
            by_name[name] = item
        # also keep id→ later if needed
    return by_name


def _replace_with_smiles(
    md_text: str,
    image_refs: list[tuple[str, str]],
    resolved: dict[tuple[str, str], Path | None],
    predictions_by_name: dict[str, dict[str, Any]],
) -> tuple[str, int, dict[str, int]]:
    threshold = settings.molscribe_confidence
    template = settings.smiles_template
    replaced = 0
    counters = {
        "pred_missing": 0,
        "pred_error": 0,
        "pred_no_smiles": 0,
        "pred_low_conf": 0,
        "pred_ok": 0,
    }

    for alt, rel_path in image_refs:
        img_path = resolved.get((alt, rel_path))
        name = Path(rel_path).name
        pred = predictions_by_name.get(name) if name else None
        if img_path is not None and pred is None:
            # fallback: try resolved basename
            pred = predictions_by_name.get(img_path.name)

        if not pred:
            counters["pred_missing"] += 1
            continue
        if pred.get("error"):
            counters["pred_error"] += 1
            logger.warning("MolScribe error for %s: %s", name, pred.get("error"))
            continue

        confidence = float(pred.get("confidence") or 0.0)
        smiles = (pred.get("smiles") or "").strip()
        if not smiles:
            counters["pred_no_smiles"] += 1
            continue
        if confidence < threshold:
            counters["pred_low_conf"] += 1
            logger.info(
                "Skip %s: conf=%.3f < threshold=%.3f", name, confidence, threshold
            )
            continue

        counters["pred_ok"] += 1
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

    return md_text, replaced, counters


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
    seen: set[Path] = set()
    unique_paths: list[Path] = []
    for p in valid_paths:
        rp = p.resolve()
        if rp not in seen:
            seen.add(rp)
            unique_paths.append(rp)

    predictions_by_name: dict[str, dict[str, Any]] = {}
    batch: list[dict[str, Any]] = []
    if unique_paths:
        logger.info("[3/4] MolScribe on %d images", len(unique_paths))
        pred_json = work_dir / "molscribe_predictions.json"
        batch = run_molscribe_batch(unique_paths, output_json=pred_json)
        predictions_by_name = _index_predictions(batch)
        logger.info(
            "MolScribe returned %d rows, indexed %d basenames",
            len(batch),
            len(predictions_by_name),
        )
    else:
        logger.info("[3/4] No images — skip MolScribe")

    logger.info("[4/4] Write final markdown")
    final_text, replaced, counters = _replace_with_smiles(
        md_text, image_refs, resolved, predictions_by_name
    )

    if images_dir and images_dir.is_dir():
        dest_images = final_md_path.parent / "images"
        if dest_images.exists():
            shutil.rmtree(dest_images)
        shutil.copytree(images_dir, dest_images)

    final_md_path.write_text(final_text, encoding="utf-8")

    # Helpful one-liner for operators
    hint = None
    if len(image_refs) > 0 and replaced == 0:
        if counters["pred_error"] > 0:
            hint = (
                "MolScribe ran but every/most predictions errored "
                "(often albumentations version mismatch). "
                "Inspect work/molscribe_predictions.json and pin albumentations==1.3.1."
            )
        elif counters["pred_missing"] == len(image_refs):
            hint = "No MolScribe predictions matched image names — check worker output."
        elif counters["pred_low_conf"] > 0:
            hint = (
                f"Predictions existed but all below MOLSCRIBE_CONFIDENCE="
                f"{settings.molscribe_confidence}."
            )
        else:
            hint = "Images kept; check molscribe_predictions.json and container logs."

    stats = {
        "images_found": len(image_refs),
        "images_resolved": len(unique_paths),
        "molscribe_results": len(batch),
        "smiles_replaced": replaced,
        "pred_missing": counters["pred_missing"],
        "pred_error": counters["pred_error"],
        "pred_no_smiles": counters["pred_no_smiles"],
        "pred_low_conf": counters["pred_low_conf"],
        "pred_ok": counters["pred_ok"],
        "molscribe_confidence_threshold": settings.molscribe_confidence,
        "hint": hint,
        "intermediate_md": str(intermediate_md),
        "final_md": str(final_md_path),
        "predictions_json": str(work_dir / "molscribe_predictions.json")
        if unique_paths
        else None,
    }
    if hint:
        logger.warning("SMILES replace warning: %s", hint)
    logger.info("Done: %s", stats)
    return {"final_md": final_md_path, "stats": stats}
