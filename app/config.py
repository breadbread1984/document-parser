"""Service settings (API process only — no MinerU/MolScribe imports)."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        env_file_encoding="utf-8",
    )

    data_dir: Path = Path("/data")
    host: str = "0.0.0.0"
    port: int = 8000

    mineru_venv: Path = Path("/opt/venvs/mineru")
    molscribe_venv: Path = Path("/opt/venvs/molscribe")

    mineru_backend: str = "pipeline"
    mineru_method: str = "ocr"
    mineru_timeout: int = 3600

    molscribe_device: str = "cuda"
    molscribe_confidence: float = 0.5
    molscribe_model_repo: str = "yujieq/MolScribe"
    molscribe_checkpoint: str = "swin_base_char_aux_1m680k.pth"
    smiles_template: str = "`{smiles}`"
    keep_original_image: bool = False

    max_upload_mb: int = 200

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"


settings = Settings()
