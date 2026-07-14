#!/usr/bin/env python3
"""MolScribe batch worker — must run with the MolScribe venv Python only."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("molscribe_worker")


def _patch_swin_aliases() -> None:
    from molscribe.transformer import swin_transformer as st

    aliases = {
        "swin_base": "swin_base_patch4_window12_384",
        "swin_large": "swin_large_patch4_window12_384",
        "swin_small": "swin_small_patch4_window7_224",
        "swin_tiny": "swin_tiny_patch4_window7_224",
    }
    for short, full in aliases.items():
        if short not in st._swin_configs and full in st._swin_configs:
            st._swin_configs[short] = st._swin_configs[full]


def main() -> int:
    parser = argparse.ArgumentParser(description="Batch MolScribe inference worker")
    parser.add_argument("--images-json", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--device", default="cuda", choices=("cuda", "cpu"))
    parser.add_argument("--model-repo", default="yujieq/MolScribe")
    parser.add_argument("--checkpoint", default="swin_base_char_aux_1m680k.pth")
    parser.add_argument(
        "--model-path",
        default=None,
        help="Optional local .pth path (skips HuggingFace download)",
    )
    args = parser.parse_args()

    import torch
    from huggingface_hub import hf_hub_download
    from molscribe import MolScribe

    _patch_swin_aliases()

    if args.model_path:
        ckpt_path = args.model_path
    else:
        ckpt_path = hf_hub_download(
            repo_id=args.model_repo,
            filename=args.checkpoint,
        )

    with open(args.images_json, encoding="utf-8") as handle:
        entries = json.load(handle)

    use_cuda = args.device == "cuda" and torch.cuda.is_available()
    device = torch.device("cuda" if use_cuda else "cpu")
    logger.info("Loading MolScribe on %s", device)
    model = MolScribe(ckpt_path, device=device)

    results = []
    for entry in entries:
        image_id = entry["id"]
        image_path = Path(entry["path"])
        if not image_path.is_file():
            results.append(
                {
                    "id": image_id,
                    "path": str(image_path),
                    "error": "image_not_found",
                }
            )
            continue
        try:
            output = model.predict_image_file(
                str(image_path),
                return_confidence=True,
            )
            results.append(
                {
                    "id": image_id,
                    "path": str(image_path),
                    "smiles": output.get("smiles", ""),
                    "confidence": output.get("confidence", 0.0),
                }
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed %s: %s", image_path.name, exc)
            results.append(
                {
                    "id": image_id,
                    "path": str(image_path),
                    "error": str(exc),
                }
            )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(results, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(json.dumps({"predicted": len(results), "output": str(output_path)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
