#!/usr/bin/env python3
"""
Chemical Patent/Paper Parser

Converts a PDF document (chemical patents, papers) into Markdown,
with chemical structural formulas automatically converted to SMILES notation.

Workflow:
    PDF  →  MinerU (parse text + extract images)
         →  MolScribe (structural formula → SMILES)
         →  Final Markdown with SMILES

Usage:
    python main.py input.pdf
    python main.py input.pdf -o output_dir --confidence 0.6
    python main.py input.pdf --backend vlm-engine --vllm-model MinerU2.5-2509-1.2B
    python main.py input.pdf --backend hybrid-engine --tp 2 --gpu-mem 0.7
    python main.py input.pdf --backend vlm-http-client --vllm-endpoint http://10.0.0.5:30000
"""

import argparse
import logging
import sys
from pathlib import Path

from config import load_from_env
from src.pipeline import ChemicalDocumentPipeline


def setup_logging(verbose: bool = False) -> None:
    """Configure logging for the application."""
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s [%(levelname)-7s] %(name)s | %(message)s"
    logging.basicConfig(level=level, format=fmt, datefmt="%H:%M:%S")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Parse chemical PDFs to Markdown with SMILES for structural formulas.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s paper.pdf
  %(prog)s paper.pdf -o ./results --confidence 0.7
  %(prog)s paper.pdf --backend pipeline
  %(prog)s paper.pdf --backend vlm-engine --vllm-model /path/to/model
  %(prog)s paper.pdf --backend hybrid-engine --tp 2 --gpu-mem 0.7 --cuda-devices 0,1
  %(prog)s paper.pdf --backend vlm-http-client --vllm-endpoint http://10.0.0.5:30000
  %(prog)s paper.pdf --device cuda --verbose
        """,
    )

    parser.add_argument(
        "pdf",
        type=Path,
        help="Path to the input PDF file.",
    )

    parser.add_argument(
        "-o", "--output-dir",
        type=Path,
        default=None,
        help="Output directory (default: ./output/<pdf_basename>/).",
    )

    # ---- MinerU options ----
    mineru_group = parser.add_argument_group("MinerU Options")
    mineru_group.add_argument(
        "--backend",
        type=str,
        default="pipeline",
        choices=["pipeline", "hybrid-engine", "vlm-engine", "hybrid-http-client", "vlm-http-client"],
        help="MinerU backend (default: pipeline). 'vlm-engine'/'hybrid-engine' use local vLLM for GPU-accelerated inference. '*-http-client' variants connect to a remote mineru-openai-server.",
    )
    mineru_group.add_argument(
        "--timeout",
        type=int,
        default=None,
        help="Timeout in seconds for the MinerU subprocess (default: 3600).",
    )
    mineru_group.add_argument(
        "--no-ocr",
        action="store_true",
        help="Disable OCR in MinerU (enabled by default).",
    )

    # ---- vLLM options (used with --backend vlm-engine or hybrid-engine) ----
    vllm_group = parser.add_argument_group("vLLM Options (for --backend vlm-engine/hybrid-engine)")
    vllm_group.add_argument(
        "--vllm-model",
        type=str,
        default=None,
        help="Path or HuggingFace model ID for the VLM. Uses MinerU default if not set.",
    )
    vllm_group.add_argument(
        "--tp", "--tensor-parallel-size",
        type=int,
        default=None,
        dest="tensor_parallel_size",
        help="Number of GPUs for tensor parallelism (default: 1).",
    )
    vllm_group.add_argument(
        "--gpu-mem", "--gpu-memory-utilization",
        type=float,
        default=None,
        dest="gpu_memory_utilization",
        help="GPU memory fraction for vLLM (0.0-1.0). Auto-calculated by MinerU if not set.",
    )
    vllm_group.add_argument(
        "--virtual-vram",
        type=int,
        default=None,
        help="Override VRAM size in GB for memory calculation.",
    )
    vllm_group.add_argument(
        "--cuda-devices", "--cuda-visible-devices",
        type=str,
        default=None,
        dest="cuda_visible_devices",
        help="Comma-separated GPU indices (e.g. '0,1'). Sets CUDA_VISIBLE_DEVICES.",
    )
    vllm_group.add_argument(
        "--max-model-len",
        type=int,
        default=None,
        help="Maximum model context length in tokens.",
    )
    vllm_group.add_argument(
        "--vllm-dtype",
        type=str,
        default=None,
        choices=["auto", "float16", "bfloat16"],
        help="vLLM data type (default: auto).",
    )
    vllm_group.add_argument(
        "--vllm-endpoint",
        type=str,
        default=None,
        help="Remote vLLM server URL for -b *-http-client backends (e.g. http://10.0.0.5:30000).",
    )
    vllm_group.add_argument(
        "--vllm-api-key",
        type=str,
        default=None,
        help="API key for authenticated remote vLLM endpoints.",
    )

    # ---- MolScribe options ----
    ms_group = parser.add_argument_group("MolScribe Options")
    ms_group.add_argument(
        "--confidence",
        type=float,
        default=0.5,
        help="Confidence threshold for SMILES replacement (0.0-1.0, default: 0.5).",
    )
    ms_group.add_argument(
        "--device",
        type=str,
        default=None,
        choices=["cpu", "cuda"],
        help="Device for MolScribe inference (default: auto-detect).",
    )
    ms_group.add_argument(
        "--keep-images",
        action="store_true",
        help="Keep original structural formula images alongside SMILES in output.",
    )

    # ---- General options ----
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose/debug logging.",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    setup_logging(args.verbose)
    logger = logging.getLogger("main")

    # Build configuration — environment (.env) overrides defaults, CLI overrides both
    config = load_from_env()

    # --- Apply CLI overrides ---
    # MinerU
    config.mineru_backend = args.backend
    config.mineru_enable_ocr = not args.no_ocr
    if args.timeout is not None:
        config.mineru_timeout = args.timeout

    # vLLM (only applicable when backend is vlm/hybrid or *-http-client)
    v = config.vllm
    if args.vllm_model is not None:
        v.model = args.vllm_model
    if args.tensor_parallel_size is not None:
        v.tensor_parallel_size = args.tensor_parallel_size
    if args.gpu_memory_utilization is not None:
        v.gpu_memory_utilization = args.gpu_memory_utilization
    if args.virtual_vram is not None:
        v.virtual_vram_size = args.virtual_vram
    if args.cuda_visible_devices is not None:
        v.cuda_visible_devices = args.cuda_visible_devices
    if args.max_model_len is not None:
        v.max_model_len = args.max_model_len
    if args.vllm_dtype is not None:
        v.dtype = args.vllm_dtype
    if args.vllm_endpoint is not None:
        v.endpoint = args.vllm_endpoint
    if args.vllm_api_key is not None:
        v.api_key = args.vllm_api_key

    # MolScribe
    config.molscribe_confidence_threshold = args.confidence
    config.molscribe_device = args.device
    config.keep_original_image = args.keep_images

    # ---- Log configuration ----
    logger.info("Configuration:")
    logger.info("  PDF:              %s", args.pdf)
    logger.info("  Output dir:       %s", args.output_dir or f"output/{args.pdf.stem}")
    logger.info("  MinerU backend:   %s", config.mineru_backend)
    logger.info("  OCR:              %s", "enabled" if config.mineru_enable_ocr else "disabled")
    logger.info("  MinerU timeout:   %s s", config.mineru_timeout)
    logger.info("  SMILES conf:      %.2f", config.molscribe_confidence_threshold)
    logger.info("  MolScribe dev:    %s", config.molscribe_device or "auto")
    logger.info("  Keep images:      %s", config.keep_original_image)

    # Determine which backends imply vLLM usage
    vllm_backends = {"vlm-engine", "hybrid-engine", "vlm-http-client", "hybrid-http-client"}
    is_vllm = config.mineru_backend in vllm_backends
    is_http_client = config.mineru_backend in {"vlm-http-client", "hybrid-http-client"}

    if is_vllm:
        logger.info("  --- vLLM config ---")
        if is_http_client:
            logger.info("  Endpoint:         %s", v.endpoint or "(default http://127.0.0.1:30000)")
            logger.info("  API key:          %s", "****" if v.api_key else "(not set)")
        else:
            logger.info("  Model:            %s", v.model or "(mineru default)")
            logger.info("  Tensor parallel:  %s", v.tensor_parallel_size or "1")
            logger.info("  GPU mem util:     %s", v.gpu_memory_utilization or "(auto)")
            logger.info("  Virtual VRAM:     %s", f"{v.virtual_vram_size} GB" if v.virtual_vram_size else "(auto)")
            logger.info("  CUDA devices:     %s", v.cuda_visible_devices or "(all)")
            logger.info("  Max model len:    %s", v.max_model_len or "(auto)")
            logger.info("  dtype:            %s", v.dtype)
            logger.info("  v1 engine:        %s", v.use_v1_engine)

    # ---- Run pipeline ----
    pipeline = ChemicalDocumentPipeline(config)

    try:
        final_md = pipeline.run(args.pdf, args.output_dir)
        logger.info("Success! Final markdown written to: %s", final_md)
        sys.exit(0)
    except FileNotFoundError as e:
        logger.error("File error: %s", e)
        sys.exit(1)
    except RuntimeError as e:
        logger.error("Pipeline error: %s", e)
        sys.exit(1)
    except KeyboardInterrupt:
        logger.warning("Interrupted by user.")
        sys.exit(130)


if __name__ == "__main__":
    main()
