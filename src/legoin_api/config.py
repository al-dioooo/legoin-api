"""Configuration from environment variables, all prefixed `LEGOIN_`."""

from enum import StrEnum
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_CORS_ORIGINS = "http://localhost:3000,https://legoin-web.vercel.app"


class PipelineKind(StrEnum):
    """Which pipeline runs scans: the real models, or a deterministic stand-in for tests."""

    STUB = "stub"
    MODELS = "models"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LEGOIN_", frozen=True)

    pipeline: PipelineKind = PipelineKind.STUB
    """`models` runs the legoin-ml release; `stub` needs no torch or weights."""

    data_dir: Path = Path("data")
    """The service's only state: the database, photos and downloaded models."""

    model_tag: str = "v0.2.0"
    """The legoin-ml release the models come from. Its files' hashes must be in `weights.py`."""

    model_dir: Path | None = None
    """Where model files are cached. Unset, `<data dir>/models`."""

    torch_threads: int | None = None
    """Threads torch may use. Unset, torch's default: one per core."""

    cors_origins: str = DEFAULT_CORS_ORIGINS
    """Comma-separated origins allowed to call the API from a browser."""

    public_url: str | None = None
    """The absolute base for `photo.src`, e.g. `https://api.example.com`. Unset, each answer
    uses the URL its request came in on."""

    scan_timeout_s: float = 30.0
    """A scan job still running this long after its upload fails with `timeout`."""

    scan_workers: int = 1
    """Scans run at the same time. One by default: torch already uses every core."""

    ideas_timeout_s: float = 30.0
    """A request for ideas still unanswered after this long fails with `504 timeout`."""

    @property
    def models_path(self) -> Path:
        return self.model_dir or self.data_dir / "models"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]
