"""
MinerU PDF parser wrapper.

Launches MinerU as a subprocess to convert PDF documents into Markdown
with extracted images. Supports pipeline, hybrid, and VLM backends;
the VLM/Hybrid backends use vLLM for high-performance inference.
"""

import logging
import subprocess
import shutil
import os
from pathlib import Path
from typing import Optional

from config import ParserConfig

logger = logging.getLogger(__name__)

# Backends that use vLLM for inference (embedded / local mode)
_VLLM_EMBEDDED_BACKENDS = {"vlm-engine", "hybrid-engine"}

# Backends that connect to a remote vLLM / mineru-openai-server via HTTP
_VLLM_HTTP_BACKENDS = {"vlm-http-client", "hybrid-http-client"}

# All vLLM-using backends
_VLLM_BACKENDS = _VLLM_EMBEDDED_BACKENDS | _VLLM_HTTP_BACKENDS


class MinerUParser:
    """Wrapper around the MinerU CLI for PDF-to-Markdown conversion."""

    def __init__(self, config: ParserConfig):
        self.config = config

    def _check_mineru_installed(self) -> bool:
        """Verify that the 'mineru' command is available."""
        return shutil.which("mineru") is not None

    def parse(self, pdf_path: Path, output_dir: Path, timeout: int = 3600) -> Path:
        """
        Parse a PDF file using MinerU and return the path to the generated
        Markdown file.

        Args:
            pdf_path: Path to the input PDF file.
            output_dir: Directory where MinerU should write its output.
            timeout: Maximum time in seconds for the MinerU subprocess.

        Returns:
            Path to the generated Markdown file.

        Raises:
            RuntimeError: If MinerU is not installed or parsing fails.
        """
        if not self._check_mineru_installed():
            raise RuntimeError(
                "MinerU CLI ('mineru') not found. "
                "Install it with: pip install 'mineru[all]'"
            )

        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF file not found: {pdf_path}")

        output_dir.mkdir(parents=True, exist_ok=True)

        # Base command
        cmd = [
            "mineru",
            "-p", str(pdf_path),
            "-o", str(output_dir),
            "-b", self.config.mineru_backend,
        ]

        if self.config.mineru_enable_ocr:
            cmd.extend(["--enable-ocr", "true"])

        # ---- VLLM backend: inject vLLM-specific args & env vars ----
        env = os.environ.copy()
        if self.config.mineru_backend in _VLLM_BACKENDS:
            vllm = self.config.vllm

            # Environment variables (common to both embedded and HTTP-client modes)
            for key, val in vllm.to_env_vars().items():
                env[key] = val

            if self.config.mineru_backend in _VLLM_EMBEDDED_BACKENDS:
                # Embedded mode: pass vLLM engine args (model, TP, memory, etc.)
                vllm_args = vllm.to_cli_args()
                if vllm_args:
                    logger.info(
                        "vLLM embedded backend detected. Passing vLLM args: %s",
                        " ".join(vllm_args),
                    )
                    cmd.extend(vllm_args)
            else:
                # HTTP-client mode: pass endpoint URL (required).
                # API key is passed via OPENAI_API_KEY env var (see to_env_vars()),
                # which most OpenAI-compatible HTTP clients pick up automatically.
                if vllm.endpoint:
                    cmd.extend(["-u", vllm.endpoint])
                    logger.info(
                        "vLLM HTTP-client backend detected. Endpoint: %s",
                        vllm.endpoint,
                    )
                else:
                    logger.warning(
                        "HTTP-client backend '%s' used but no endpoint (-u) provided. "
                        "MinerU will attempt to connect to http://127.0.0.1:30000 by default.",
                        self.config.mineru_backend,
                    )

        # Pass any extra args from config
        cmd.extend(self.config.mineru_extra_args)

        logger.info("Running MinerU: %s", " ".join(cmd))

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
                # 600s may be too short for first-run model downloads;
                # default is 3600s, configurable via --timeout / MINERU_TIMEOUT
                env=env,
            )

            if result.returncode != 0:
                logger.error("MinerU stderr:\n%s", result.stderr)
                raise RuntimeError(
                    f"MinerU exited with code {result.returncode}:\n{result.stderr}"
                )

            logger.info("MinerU stdout:\n%s", result.stdout)

        except subprocess.TimeoutExpired:
            raise RuntimeError(f"MinerU timed out after {timeout} seconds.")

        # Determine the output markdown path.
        # MinerU writes: <output_dir>/<basename>/<basename>.md
        # or (for single files): <output_dir>/<basename>.md
        md_path = self._find_markdown(output_dir, pdf_path)

        if md_path is None:
            raise RuntimeError(
                f"MinerU completed but no .md file found under {output_dir}"
            )

        logger.info("Generated Markdown: %s", md_path)
        return md_path

    def _find_markdown(self, output_dir: Path, pdf_path: Path) -> Optional[Path]:
        """Locate the generated Markdown file in MinerU's output directory."""
        basename = pdf_path.stem

        # Check common output patterns
        candidates = [
            output_dir / f"{basename}.md",
            output_dir / basename / f"{basename}.md",
        ]

        for candidate in candidates:
            if candidate.exists():
                return candidate

        # Fallback: search recursively
        for md_file in output_dir.rglob("*.md"):
            return md_file

        return None

    def find_images_dir(self, md_path: Path) -> Optional[Path]:
        """
        Find the 'images' directory associated with a MinerU-generated markdown.

        MinerU places extracted images in an 'images/' subdirectory next to
        (or in the same parent directory as) the markdown file.
        """
        # Look in the same directory as the .md file
        candidates = [
            md_path.parent / "images",
            md_path.parent.parent / "images",
        ]
        for candidate in candidates:
            if candidate.is_dir():
                return candidate

        # Search for any 'images' dir under the output tree
        for d in md_path.parent.rglob("images"):
            if d.is_dir():
                return d

        return None
