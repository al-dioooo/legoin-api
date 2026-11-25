"""Configuration from environment variables, all prefixed `LEGOIN_`."""

from enum import StrEnum
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_CORS_ORIGINS = "http://localhost:3000,https://legoin-web.vercel.app"


class PipelineKind(StrEnum):
    """Which pipeline runs scans: the real models, or a deterministic stand-in for tests."""

    STUB = "stub"
    MODELS = "models"


class Settings(BaseSettings):
    # An empty variable, as `.env.example` leaves some, means "use the default".
    model_config = SettingsConfigDict(env_prefix="LEGOIN_", env_ignore_empty=True, frozen=True)

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

    container: bool = False
    """Set by the Docker image, where the service is deployed: it warns at start-up about
    settings a deployment needs, such as `LEGOIN_PUBLIC_URL`."""

    scan_timeout_s: float = 30.0
    """A scan job still running this long after its upload fails with `timeout`."""

    scan_workers: int = Field(1, ge=1)
    """Scans run at the same time. One by default: torch already uses every core. The models
    pipeline only runs with 1, since its models aren't thread-safe."""

    scan_queue_size: int = Field(8, ge=1)
    """Scans accepted and not yet finished, running ones included. Beyond it, an upload answers
    `503 unavailable` with `Retry-After`. Each holds its decoded photo, at most 48 MiB."""

    ideas_timeout_s: float = 30.0
    """A request for ideas still unanswered after this long fails with `504 timeout`."""

    @model_validator(mode="after")
    def _one_worker_for_the_models(self) -> "Settings":
        # Every worker would share one YOLO predictor and one classifier, and Ultralytics'
        # predictors aren't thread-safe. torch already uses every core for one scan.
        if self.pipeline is PipelineKind.MODELS and self.scan_workers > 1:
            raise ValueError("LEGOIN_SCAN_WORKERS must be 1 with LEGOIN_PIPELINE=models")
        return self

    @property
    def models_path(self) -> Path:
        return self.model_dir or self.data_dir / "models"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]
