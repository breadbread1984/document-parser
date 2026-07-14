"""
MolScribe client wrapper for chemical structure recognition.

Converts molecular structure images (PNG/JPG) into SMILES strings
using the MolScribe image-to-graph model.
"""

import logging
from pathlib import Path
from typing import Optional, Dict, Any

import torch
from huggingface_hub import hf_hub_download

from config import ParserConfig

logger = logging.getLogger(__name__)


class MolScribeClient:
    """Thin wrapper around MolScribe for SMILES prediction from images."""

    def __init__(self, config: ParserConfig):
        self.config = config
        self._model = None
        self._device = None

    @property
    def device(self) -> torch.device:
        if self._device is None:
            if self.config.molscribe_device:
                self._device = torch.device(self.config.molscribe_device)
            else:
                self._device = torch.device(
                    "cuda" if torch.cuda.is_available() else "cpu"
                )
            logger.info("MolScribe using device: %s", self._device)
        return self._device

    def _load_model(self):
        """Lazy-load the MolScribe model (downloads checkpoint on first call)."""
        if self._model is not None:
            return

        logger.info(
            "Downloading MolScribe checkpoint '%s' from '%s' ...",
            self.config.molscribe_checkpoint,
            self.config.molscribe_model_repo,
        )
        ckpt_path = hf_hub_download(
            repo_id=self.config.molscribe_model_repo,
            filename=self.config.molscribe_checkpoint,
        )

        # Import here to avoid import errors when MolScribe is not yet installed
        from molscribe import MolScribe

        logger.info("Loading MolScribe model onto %s ...", self.device)
        self._model = MolScribe(ckpt_path, device=self.device)
        logger.info("MolScribe model loaded successfully.")

    def predict(self, image_path: Path) -> Optional[Dict[str, Any]]:
        """
        Predict SMILES from a single image file.

        Args:
            image_path: Path to the molecular structure image.

        Returns:
            Dict with keys 'smiles', 'confidence', 'molfile', etc.,
            or None if prediction fails.
        """
        self._load_model()

        try:
            result = self._model.predict_image_file(
                str(image_path),
                return_atoms_bonds=False,
                return_confidence=True,
            )
            logger.debug(
                "Predicted %s  →  SMILES: %s  (confidence: %.3f)",
                image_path.name,
                result.get("smiles", "N/A"),
                result.get("confidence", 0.0),
            )
            return result
        except Exception as e:
            logger.warning("MolScribe prediction failed for %s: %s", image_path.name, e)
            return None

    def is_chemical_structure(self, image_path: Path) -> bool:
        """
        Check whether an image likely contains a recognizable chemical structure.

        This is a fast heuristic: runs MolScribe and checks if the confidence
        exceeds the configured threshold.
        """
        result = self.predict(image_path)
        if result is None:
            return False
        confidence = result.get("confidence", 0.0)
        return confidence >= self.config.molscribe_confidence_threshold

    def predict_batch(
        self, image_paths: list[Path]
    ) -> dict[Path, Optional[Dict[str, Any]]]:
        """
        Predict SMILES for multiple images.

        Args:
            image_paths: List of image file paths.

        Returns:
            Dict mapping each image_path to its prediction result (or None on failure).
        """
        self._load_model()
        results = {}
        # MolScribe supports batch prediction; process in one call for efficiency
        if not image_paths:
            return results

        try:
            str_paths = [str(p) for p in image_paths]
            batch_results = self._model.predict_image_files(
                str_paths,
                return_confidence=True,
            )
            for path, result in zip(image_paths, batch_results):
                results[path] = result
                if result:
                    logger.debug(
                        "  %s → SMILES: %s (conf: %.3f)",
                        path.name,
                        result.get("smiles", "N/A"),
                        result.get("confidence", 0.0),
                    )
        except Exception as e:
            logger.warning("Batch prediction error: %s. Falling back to sequential.", e)
            for path in image_paths:
                results[path] = self.predict(path)

        return results
