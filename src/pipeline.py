"""
Pipeline orchestration: PDF → MinerU → MolScribe → final Markdown.

Flow:
1. MinerU parses the PDF → produces intermediate markdown + extracted images.
2. All image references in the markdown are identified.
3. Each referenced image is passed through MolScribe to predict a SMILES string.
4. Images that yield a confident SMILES prediction have their markdown image
   tag replaced with the SMILES notation.
5. The final markdown is written to the output directory.
"""

import logging
import re
import shutil
from pathlib import Path
from typing import Optional

from config import ParserConfig
from src.mineru_parser import MinerUParser
from src.molscribe_client import MolScribeClient

logger = logging.getLogger(__name__)

# Regex to match markdown image syntax: ![alt](path)
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


class ChemicalDocumentPipeline:
    """
    End-to-end pipeline that converts a PDF chemical document into Markdown
    with chemical structural formulas replaced by SMILES strings.
    """

    def __init__(self, config: Optional[ParserConfig] = None):
        self.config = config or ParserConfig()
        self.mineru = MinerUParser(self.config)
        self.molscribe: Optional[MolScribeClient] = None

    def _get_molscribe(self) -> MolScribeClient:
        """Lazy-init MolScribe client."""
        if self.molscribe is None:
            self.molscribe = MolScribeClient(self.config)
        return self.molscribe

    def run(self, pdf_path: Path, output_dir: Optional[Path] = None) -> Path:
        """
        Process a PDF document end-to-end.

        Args:
            pdf_path: Path to the input PDF file.
            output_dir: Directory for MinerU intermediate + final output.
                        Defaults to ``./output/<pdf_basename>/``.

        Returns:
            Path to the final Markdown file.
        """
        pdf_path = Path(pdf_path).resolve()
        if not pdf_path.exists():
            raise FileNotFoundError(f"Input PDF not found: {pdf_path}")

        if output_dir is None:
            output_dir = Path("output") / pdf_path.stem
        output_dir = Path(output_dir).resolve()

        logger.info("=" * 60)
        logger.info("Processing: %s", pdf_path.name)
        logger.info("Output dir: %s", output_dir)
        logger.info("=" * 60)

        # ---- Step 1: Parse PDF with MinerU ----
        logger.info("[1/4] Parsing PDF with MinerU (backend=%s) ...", self.config.mineru_backend)
        intermediate_md = self.mineru.parse(pdf_path, output_dir, timeout=self.config.mineru_timeout)

        # ---- Step 2: Read markdown and find images ----
        logger.info("[2/4] Reading intermediate markdown and locating images ...")
        original_md_text = intermediate_md.read_text(encoding="utf-8")
        images_dir = self.mineru.find_images_dir(intermediate_md)

        image_refs = self._extract_image_refs(original_md_text, images_dir, intermediate_md.parent)
        logger.info("Found %d image references in markdown.", len(image_refs))

        if not image_refs:
            logger.info("No images to process. Copying markdown as final output.")
            final_md_path = output_dir / f"{pdf_path.stem}_final.md"
            final_md_path.write_text(original_md_text, encoding="utf-8")
            return final_md_path

        # ---- Step 3: Predict SMILES for each image ----
        logger.info("[3/4] Running MolScribe on %d images ...", len(image_refs))
        molscribe_client = self._get_molscribe()

        # Resolve actual file paths for images
        resolved_images = self._resolve_image_paths(image_refs, images_dir, intermediate_md.parent)

        predictions = {}
        valid_paths = [p for p in resolved_images.values() if p and p.exists()]
        invalid_count = len(resolved_images) - len(valid_paths)

        if invalid_count > 0:
            logger.warning(
                "%d image reference(s) could not be resolved to files; they will be kept as-is.",
                invalid_count,
            )

        if valid_paths:
            batch_results = molscribe_client.predict_batch(valid_paths)
            predictions = batch_results

        # ---- Step 4: Replace images with SMILES in markdown ----
        logger.info("[4/4] Replacing chemical structure images with SMILES ...")
        final_md_text = self._replace_with_smiles(
            original_md_text,
            image_refs,
            resolved_images,
            predictions,
        )

        # Write final markdown
        final_md_path = output_dir / f"{pdf_path.stem}_final.md"
        # Copy images dir to make final markdown self-contained
        if images_dir and images_dir.is_dir():
            dest_images = final_md_path.parent / "images"
            if not dest_images.exists():
                shutil.copytree(images_dir, dest_images)

        final_md_path.write_text(final_md_text, encoding="utf-8")

        # ---- Summary ----
        replaced_count = sum(
            1 for _img, pred in predictions.items()
            if pred and pred.get("confidence", 0) >= self.config.molscribe_confidence_threshold
        )
        kept_count = len(image_refs) - replaced_count

        logger.info("=" * 60)
        logger.info("Done! Final markdown: %s", final_md_path)
        logger.info("  Total images found:  %d", len(image_refs))
        logger.info("  Replaced with SMILES: %d", replaced_count)
        logger.info("  Kept as images:       %d", kept_count)
        logger.info("=" * 60)

        return final_md_path

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_image_refs(md_text: str, *_, **__) -> list[tuple[str, str]]:
        """
        Extract all (alt_text, relative_path) pairs from markdown image syntax.

        Returns a list of tuples: [(alt_text, relative_path), ...]
        """
        return _IMAGE_RE.findall(md_text)

    @staticmethod
    def _resolve_image_paths(
        image_refs: list[tuple[str, str]],
        images_dir: Optional[Path],
        base_dir: Path,
    ) -> dict[tuple[str, str], Optional[Path]]:
        """
        Resolve each image reference to an absolute filesystem path.

        Returns a dict: {(alt, rel_path) → resolved_absolute_path or None}
        """
        resolved = {}
        for alt, rel_path in image_refs:
            candidates = []
            if images_dir:
                # MinerU stores images as images/<hash>.jpg relative to md
                candidates.append(images_dir / Path(rel_path).name)
                candidates.append(images_dir / rel_path)
                # Some versions put images relative to md parent
                candidates.append(base_dir / rel_path)
            # Also try relative to base_dir
            candidates.append(base_dir / rel_path)
            # Try resolving the path directly
            candidates.append(Path(rel_path))

            found = None
            for c in candidates:
                if c.exists():
                    found = c
                    break

            if found is None:
                logger.debug("Could not resolve image: %s", rel_path)
            else:
                logger.debug("Resolved %s → %s", rel_path, found)

            resolved[(alt, rel_path)] = found
        return resolved

    def _replace_with_smiles(
        self,
        md_text: str,
        image_refs: list[tuple[str, str]],
        resolved_images: dict[tuple[str, str], Optional[Path]],
        predictions: dict[Path, dict],
    ) -> str:
        """
        Replace image references with SMILES strings where MolScribe
        predictions are confident enough.
        """
        threshold = self.config.molscribe_confidence_threshold
        template = self.config.smiles_template

        for alt, rel_path in image_refs:
            img_path = resolved_images.get((alt, rel_path))
            pred = predictions.get(img_path) if img_path else None

            if pred is None or pred.get("confidence", 0.0) < threshold:
                # Keep the original image reference
                continue

            smiles = pred.get("smiles", "")
            confidence = pred.get("confidence", 0.0)

            if not smiles:
                continue

            # Build the replacement text
            smiles_text = template.format(smiles=smiles, confidence=confidence)

            if self.config.keep_original_image:
                replacement = f"{smiles_text}\n\n![{alt}]({rel_path})"
            else:
                replacement = smiles_text

            old_tag = f"![{alt}]({rel_path})"
            md_text = md_text.replace(old_tag, replacement)

            logger.info(
                "  Replaced: %s  →  %s  (conf: %.3f)",
                rel_path,
                smiles,
                confidence,
            )

        return md_text
