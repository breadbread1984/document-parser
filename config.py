"""
Configuration for the chemical patent/document parser.

Settings can be controlled via:
  - Direct assignment in code (config.field = value)
  - Environment variables (loaded from .env via python-dotenv)
  - CLI arguments in main.py
"""

import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class VLLMConfig:
    """vLLM inference engine configuration for MinerU's VLM/Hybrid backends.

    All vLLM-supported parameters can be passed through MinerU as regular
    CLI arguments. This dataclass exposes the most commonly used ones.
    See: https://docs.vllm.ai/en/stable/serving/engine_args.html
    """

    # ---- Model ----
    # Path or HuggingFace model ID for the VLM (e.g., MinerU2.5-2509-1.2B).
    # When None, MinerU auto-downloads the default model.
    model: Optional[str] = None

    # ---- GPU / Parallelism ----
    # Number of GPUs for tensor parallelism
    tensor_parallel_size: Optional[int] = None

    # Fraction of GPU memory reserved for the model (0.0–1.0).
    # MinerU auto-calculates based on VRAM if left as None:
    #   VRAM ≤ 8 GB → 0.7,  otherwise → 0.5
    gpu_memory_utilization: Optional[float] = None

    # Override VRAM detection (in GB). Sets MINERU_VIRTUAL_VRAM_SIZE env var.
    virtual_vram_size: Optional[int] = None

    # Comma-separated GPU indices visible to the process (e.g. "0,1").
    # Sets CUDA_VISIBLE_DEVICES.
    cuda_visible_devices: Optional[str] = None

    # ---- Inference ----
    # Maximum model context length (tokens)
    max_model_len: Optional[int] = None

    # Data type: "auto", "float16", "bfloat16"
    dtype: str = "auto"

    # Trust code in the model repository
    trust_remote_code: bool = True

    # ---- Server mode (mineru-openai-server) ----
    # Port for the vLLM OpenAI-compatible server (default: 30000)
    port: Optional[int] = None

    # ---- Remote / HTTP-client mode ----
    # When using -b vlm-http-client / hybrid-http-client, the URL of the
    # remote mineru-openai-server or any OpenAI-compatible vLLM endpoint.
    # Example: http://10.0.0.5:30000/v1
    endpoint: Optional[str] = None

    # API key for authenticated remote vLLM / OpenAI-compatible endpoints.
    # If set, passed as OPENAI_API_KEY environment variable to the subprocess,
    # or as --api-key to the mineru CLI depending on the version.
    api_key: Optional[str] = None

    # ---- Engine ----
    # Use vLLM v1 engine (recommended). Set to False to use legacy v0 engine.
    use_v1_engine: bool = True

    # Device type override (auto-detected if None).
    # Valid values: None, "cuda", "ascend", "corex", "kxpu"
    device: Optional[str] = None

    def to_env_vars(self) -> dict[str, str]:
        """Convert config to environment variable dict for subprocess."""
        env = {}
        if self.use_v1_engine:
            env["VLLM_USE_V1"] = "1"
        else:
            env["VLLM_USE_V1"] = "0"
        if self.device:
            env["MINERU_VLLM_DEVICE"] = self.device
        if self.virtual_vram_size is not None:
            env["MINERU_VIRTUAL_VRAM_SIZE"] = str(self.virtual_vram_size)
        if self.cuda_visible_devices is not None:
            env["CUDA_VISIBLE_DEVICES"] = self.cuda_visible_devices
        if self.api_key is not None:
            env["OPENAI_API_KEY"] = self.api_key
        return env

    def to_cli_args(self) -> list[str]:
        """Convert config to MinerU CLI argument list for embedded vLLM engines.

        NOTE: endpoint and api_key are intentionally EXCLUDED here — they are only
        relevant for HTTP-client backends and are handled separately in mineru_parser.py.
        """
        args = []
        if self.model:
            args.extend(["--model", self.model])
        if self.tensor_parallel_size is not None:
            args.extend(["--tensor-parallel-size", str(self.tensor_parallel_size)])
        if self.gpu_memory_utilization is not None:
            args.extend(["--gpu-memory-utilization", str(self.gpu_memory_utilization)])
        if self.max_model_len is not None:
            args.extend(["--max-model-len", str(self.max_model_len)])
        if self.dtype and self.dtype != "auto":
            args.extend(["--dtype", self.dtype])
        if self.trust_remote_code:
            args.append("--trust-remote-code")
        return args


@dataclass
class ParserConfig:
    """Central configuration for the document parsing pipeline."""

    # ---- MolScribe settings ----
    # HuggingFace model repo and checkpoint filename
    molscribe_model_repo: str = "yujieq/MolScribe"
    molscribe_checkpoint: str = "swin_base_char_aux_1m680k.pth"

    # Confidence threshold for accepting a SMILES prediction.
    # Images producing a confidence below this value are kept as-is (not replaced).
    molscribe_confidence_threshold: float = 0.5

    # Device for MolScribe inference: "cuda", "cpu", or None (auto-detect)
    molscribe_device: Optional[str] = None

    # ---- MinerU settings ----
    # Backend: "pipeline" (CPU-friendly), "hybrid-engine", "vlm-engine"
    mineru_backend: str = "pipeline"

    # Whether to enable OCR in MinerU (recommended for scanned documents)
    mineru_enable_ocr: bool = True

    # Timeout in seconds for the MinerU subprocess (default: 3600 = 1 hour).
    # Increase if model download on first run takes a long time.
    mineru_timeout: int = 3600

    # Extra CLI arguments passed to mineru (list of strings)
    mineru_extra_args: list = field(default_factory=list)

    # ---- MinerU: VLLM backend settings (used with "vlm-engine" / "hybrid-engine") ----
    vllm: VLLMConfig = field(default_factory=VLLMConfig)

    # ---- Output settings ----
    # Whether to keep the original image alongside the SMILES replacement
    keep_original_image: bool = False

    # Template for SMILES in output markdown.
    # {smiles} = the predicted SMILES string
    # {confidence} = confidence score (0-1)
    smiles_template: str = "`{smiles}`"

    # Alternative: include confidence in the output
    # smiles_template: str = "`{smiles}` *(conf: {confidence:.2f})*"


# Default config instance
default_config = ParserConfig()

# Allow overrides from environment variables
def load_from_env() -> ParserConfig:
    """Build a ParserConfig with values from environment variables (via .env)."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    config = ParserConfig()

    # --- MolScribe ---
    config.molscribe_confidence_threshold = float(
        os.environ.get("MOLSCRIBE_CONFIDENCE", config.molscribe_confidence_threshold)
    )
    if os.environ.get("MOLSCRIBE_DEVICE"):
        config.molscribe_device = os.environ["MOLSCRIBE_DEVICE"]
    if os.environ.get("MOLSCRIBE_MODEL_REPO"):
        config.molscribe_model_repo = os.environ["MOLSCRIBE_MODEL_REPO"]
    if os.environ.get("MOLSCRIBE_CHECKPOINT"):
        config.molscribe_checkpoint = os.environ["MOLSCRIBE_CHECKPOINT"]

    # --- MinerU ---
    config.mineru_backend = os.environ.get("MINERU_BACKEND", config.mineru_backend)
    config.mineru_enable_ocr = os.environ.get("MINERU_ENABLE_OCR", "true").lower() == "true"
    if os.environ.get("MINERU_TIMEOUT"):
        config.mineru_timeout = int(os.environ["MINERU_TIMEOUT"])

    # --- VLLM ---
    v = config.vllm
    if os.environ.get("MINERU_VLLM_MODEL"):
        v.model = os.environ["MINERU_VLLM_MODEL"]
    if os.environ.get("MINERU_VLLM_TENSOR_PARALLEL_SIZE"):
        v.tensor_parallel_size = int(os.environ["MINERU_VLLM_TENSOR_PARALLEL_SIZE"])
    if os.environ.get("MINERU_VLLM_GPU_MEMORY_UTILIZATION"):
        v.gpu_memory_utilization = float(os.environ["MINERU_VLLM_GPU_MEMORY_UTILIZATION"])
    if os.environ.get("MINERU_VIRTUAL_VRAM_SIZE"):
        v.virtual_vram_size = int(os.environ["MINERU_VIRTUAL_VRAM_SIZE"])
    if os.environ.get("CUDA_VISIBLE_DEVICES"):
        v.cuda_visible_devices = os.environ["CUDA_VISIBLE_DEVICES"]
    if os.environ.get("MINERU_VLLM_MAX_MODEL_LEN"):
        v.max_model_len = int(os.environ["MINERU_VLLM_MAX_MODEL_LEN"])
    if os.environ.get("MINERU_VLLM_DTYPE"):
        v.dtype = os.environ["MINERU_VLLM_DTYPE"]
    if os.environ.get("MINERU_VLLM_PORT"):
        v.port = int(os.environ["MINERU_VLLM_PORT"])
    if os.environ.get("VLLM_USE_V1"):
        v.use_v1_engine = os.environ["VLLM_USE_V1"] != "0"
    if os.environ.get("MINERU_VLLM_DEVICE"):
        v.device = os.environ["MINERU_VLLM_DEVICE"]
    if os.environ.get("MINERU_VLLM_TRUST_REMOTE_CODE"):
        v.trust_remote_code = os.environ["MINERU_VLLM_TRUST_REMOTE_CODE"].lower() != "false"
    if os.environ.get("MINERU_VLLM_ENDPOINT"):
        v.endpoint = os.environ["MINERU_VLLM_ENDPOINT"]
    if os.environ.get("MINERU_VLLM_API_KEY"):
        v.api_key = os.environ["MINERU_VLLM_API_KEY"]

    return config
