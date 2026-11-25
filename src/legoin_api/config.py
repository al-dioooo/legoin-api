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


class PictureBuildsKind(StrEnum):
    """Whether picture builds (ADR-0012) run, and with whom."""

    OFF = "off"
    ANTHROPIC = "anthropic"


class IdeaPreviewMode(StrEnum):
    """When ideas Claude wrote get a preview built (ADR-0012, "Idea previews")."""

    ALL = "all"
    """All three, as soon as the ideas are answered."""
    ON_OPEN = "on_open"
    """Only when someone opens that idea's page."""
    OFF = "off"


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

    picture_builds: PictureBuildsKind = PictureBuildsKind.OFF
    """Picture builds run only when this is `anthropic`, `ANTHROPIC_API_KEY` is set, the
    `picture` extra is installed and the LDraw parts library is found (ADR-0012)."""

    picture_model: str = "claude-opus-5-5"
    """The Claude model that designs picture builds. Opus 5.5 made the more faithful builds in
    the spike, at about $0.14 each; `claude-sonnet-5-5` costs about $0.05."""

    picture_effort: Effort = "medium"

    picture_daily_budget_usd: float = Field(3.0, ge=0)
    """Dollars a day (UTC) for every picture job together, counted from real usage."""

    picture_daily_limit: int = Field(2, ge=0)
    """Picture jobs each account may start a day (UTC)."""

    picture_job_cost_usd: float = Field(0.50, gt=0)
    """Dollars one picture job may spend. Each job reserves this much of the daily budget."""

    picture_job_seconds: float = Field(300.0, gt=0)
    """Seconds one picture job may design for. The worker is killed a minute after that."""

    picture_job_calls: int = Field(24, ge=1)
    """Claude API calls one picture job may make."""

    picture_queue_size: int = Field(3, ge=1)
    """Picture jobs waiting or running. Beyond it, an upload answers `503 unavailable`."""

    picture_worker_memory_mb: int = Field(1024, ge=256)
    """The picture worker's resident memory; past it, the worker is killed."""

    idea_previews: IdeaPreviewMode = IdeaPreviewMode.ALL
    """Previews of the ideas Claude writes, built by the picture engine. They also need Claude
    ideas, the key, the `picture` extra and the LDraw parts library."""

    idea_preview_model: str = "claude-opus-5-5"
    """Opus 5.5 made the more faithful previews in the spike, at about $0.04 each."""

    idea_preview_effort: Effort = "medium"

    idea_preview_daily_limit: int = Field(9, ge=0)
    """Previews each account may start a day (UTC): three sets of ideas with `all`."""

    idea_preview_cost_usd: float = Field(0.25, gt=0)
    """Dollars one preview may spend. It reserves this much of the picture builds' daily
    budget, which previews share, while it waits or runs."""

    idea_preview_seconds: float = Field(120.0, gt=0)

    idea_preview_calls: int = Field(10, ge=1)

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
