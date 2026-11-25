"""Configuration from environment variables, all prefixed `LEGOIN_`."""

import os
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_CORS_ORIGINS = "http://localhost:3000,https://legoin-web.vercel.app"


class PipelineKind(StrEnum):
    """Which pipeline runs scans: the real models, or a deterministic stand-in for tests."""

    STUB = "stub"
    MODELS = "models"


class IdeasGeneratorKind(StrEnum):
    """What writes ideas: Claude, or legoin-web's 18 templates."""

    ANTHROPIC = "anthropic"
    TEMPLATES = "templates"


class ScanBuildsKind(StrEnum):
    """Whether Claude builds a scan's ideas from its photo and pieces (ADR-0013), and who."""

    OFF = "off"
    ANTHROPIC = "anthropic"


Effort = Literal["low", "medium", "high", "xhigh", "max"]


def anthropic_key_is_set() -> bool:
    """Whether `ANTHROPIC_API_KEY` is in the environment. Its value is never read here."""
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


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

    builds_dir: Path = Path("builds")
    """The library of checked builds: `manifest.json`, and each build's preview and model
    (ADR-0010). Without it, ideas get no previews."""

    public_url: str | None = None
    """The absolute base for `photo.src` and previews, e.g. `https://api.example.com`. Unset,
    each answer uses the URL its request came in on."""

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

    ideas_generator: IdeasGeneratorKind | None = None
    """`anthropic` has Claude write ideas, `templates` picks from legoin-web's. Unset,
    `anthropic` when `ANTHROPIC_API_KEY` is set and `templates` otherwise."""

    ideas_model: str = "claude-sonnet-5-5"
    """The Claude model that writes ideas. Sonnet 5.5 answered within the contract's 10 s for
    ideas at `low`; `claude-opus-5-5` also works, at about 17 s (ADR-0009)."""

    ideas_effort: Effort = "low"
    """How hard Claude thinks. Always sent, since the models' own defaults differ; `low` is the
    fastest."""

    scan_builds: ScanBuildsKind = ScanBuildsKind.OFF
    """Claude builds a scan's ideas from its photo and pieces only when this is `anthropic`,
    `ANTHROPIC_API_KEY` is set, the `picture` extra is installed and the LDraw parts library is
    found (ADR-0013). Otherwise, and whenever builds can't run, ideas are written as before."""

    scan_build_model: str = "claude-opus-5-5"
    """The Claude model that builds. Opus 5.5 made the more faithful builds in the spikes."""

    scan_build_effort: Effort = "medium"

    scan_build_daily_budget_usd: float = Field(3.0, ge=0)
    """Dollars a day (UTC) for every build together, counted from real usage."""

    scan_build_daily_sets: int = Field(3, ge=0)
    """Sets of builds each account may start a day (UTC). Past it, ideas are written as text."""

    scan_build_cost_usd: float = Field(0.35, gt=0)
    """Dollars one build may spend. A set reserves this much of the daily budget per build."""

    scan_build_seconds: float = Field(150.0, gt=0)
    """Seconds one build may design for. The worker is killed a minute after that."""

    scan_build_calls: int = Field(12, ge=1)
    """Claude API calls one build may make."""

    scan_build_max_pieces: int = Field(40, ge=3, le=60)
    """The most pieces one build may use, besides what the scan found."""

    scan_build_stagger_s: float = Field(5.0, ge=0)
    """How long each build of a set after the first waits to start. The first one's request has
    then cached the photo and the pieces, which the others read instead of writing again."""

    scan_build_queue_size: int = Field(3, ge=1)
    """Sets of builds waiting or running. Past it, ideas are written as text."""

    scan_build_worker_memory_mb: int = Field(1024, ge=256)
    """The build worker's resident memory; past it, the worker is killed."""

    @model_validator(mode="after")
    def _one_worker_for_the_models(self) -> "Settings":
        # Every worker would share one YOLO predictor and one classifier, and Ultralytics'
        # predictors aren't thread-safe. torch already uses every core for one scan.
        if self.pipeline is PipelineKind.MODELS and self.scan_workers > 1:
            raise ValueError("LEGOIN_SCAN_WORKERS must be 1 with LEGOIN_PIPELINE=models")
        return self

    @model_validator(mode="after")
    def _a_key_for_claude(self) -> "Settings":
        if self.ideas_generator is IdeasGeneratorKind.ANTHROPIC and not anthropic_key_is_set():
            raise ValueError("LEGOIN_IDEAS_GENERATOR=anthropic needs ANTHROPIC_API_KEY")
        return self

    @property
    def ideas_kind(self) -> IdeasGeneratorKind:
        if self.ideas_generator:
            return self.ideas_generator
        if anthropic_key_is_set():
            return IdeasGeneratorKind.ANTHROPIC
        return IdeasGeneratorKind.TEMPLATES

    @property
    def models_path(self) -> Path:
        return self.model_dir or self.data_dir / "models"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]
