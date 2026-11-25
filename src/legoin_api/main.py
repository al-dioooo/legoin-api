"""The FastAPI app.

Run it with `uv run uvicorn --factory legoin_api.main:create_app`.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from argon2 import PasswordHasher
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from legoin_api import __version__, errors
from legoin_api.accounts import FailedLogins, Passwords
from legoin_api.builds import BuildLibrary
from legoin_api.config import IdeasGeneratorKind, PictureBuildsKind, PipelineKind, Settings
from legoin_api.ideas.generator import IdeaGenerator, TemplateIdeaGenerator
from legoin_api.jobs import JobManager
from legoin_api.photos import PhotoStore
from legoin_api.picture import availability
from legoin_api.picture.files import PictureFiles
from legoin_api.picture.jobs import Limits, PictureJobs, Runner
from legoin_api.picture.runner import ProcessRunner
from legoin_api.picture.store import PictureStore
from legoin_api.pipeline import Pipeline
from legoin_api.routes import auth, builds, health, ideas, photos, picture_builds, scan_jobs, scans
from legoin_api.store import Store

log = logging.getLogger("legoin_api")
if not log.handlers:
    # Uvicorn only sets up its own loggers; this shows the service's, stage timings included.
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(levelname)s:     %(name)s: %(message)s"))
    log.addHandler(_handler)
    log.setLevel(logging.INFO)


def load_pipeline(settings: Settings) -> Pipeline:
    if settings.pipeline is PipelineKind.STUB:
        log.warning(
            "Serving the stub pipeline: detections are made up. Set LEGOIN_PIPELINE=models."
        )
        from legoin_api.pipeline.stub import StubPipeline

        return StubPipeline()
    # Imported here, so the stub and the tests never need torch.
    from legoin_api.pipeline.models import ModelPipeline
    from legoin_api.weights import ensure_models

    files = ensure_models(settings.model_tag, settings.models_path)
    return ModelPipeline(files, threads=settings.torch_threads)


def load_idea_generator(settings: Settings, builds: BuildLibrary) -> IdeaGenerator:
    if settings.ideas_kind is IdeasGeneratorKind.TEMPLATES:
        log.info("Writing ideas from the templates. Set ANTHROPIC_API_KEY to use Claude.")
        return TemplateIdeaGenerator()
    from legoin_api.ideas.claude import anthropic_generator

    log.info(
        "Writing ideas with %s at effort %s, with %d library builds, the templates as fallback",
        settings.ideas_model,
        settings.ideas_effort,
        len(builds),
    )
    # Claude gets most of the deadline; the templates answer in what's left.
    return anthropic_generator(
        settings.ideas_model,
        settings.ideas_effort,
        budget_s=0.8 * settings.ideas_timeout_s,
        library=builds,
    )


def load_picture_runner(settings: Settings) -> Runner | None:
    """The worker process runner, or None when picture builds can't run here (ADR-0012)."""
    missing = availability.missing(settings)
    if missing:
        if settings.picture_builds is PictureBuildsKind.ANTHROPIC:
            log.warning("Picture builds are off: %s.", "; ".join(missing))
        return None
    log.info(
        "Picture builds on, with %s at effort %s: $%.2f a day, %d a day per account, $%.2f a job",
        settings.picture_model,
        settings.picture_effort,
        settings.picture_daily_budget_usd,
        settings.picture_daily_limit,
        settings.picture_job_cost_usd,
    )
    return ProcessRunner(
        deadline_s=picture_limits(settings).deadline_s,
        memory_mb=settings.picture_worker_memory_mb,
    )


def picture_limits(settings: Settings) -> Limits:
    return Limits(
        model=settings.picture_model,
        effort=settings.picture_effort,
        daily_budget=settings.picture_daily_budget_usd,
        daily_limit=settings.picture_daily_limit,
        job_cost=settings.picture_job_cost_usd,
        job_seconds=settings.picture_job_seconds,
        job_calls=settings.picture_job_calls,
        queue_size=settings.picture_queue_size,
    )


def warn_about_deployment(settings: Settings) -> None:
    if settings.container and not settings.public_url:
        # uvicorn runs without --forwarded-allow-ips, so behind a TLS proxy the request's URL
        # is the proxy's view of the container: http://<internal host>/...
        log.warning(
            "LEGOIN_PUBLIC_URL is unset, so photo URLs use each request's own URL, which "
            "behind a proxy is the internal one. Set it to the API's public base URL."
        )


def create_app(
    settings: Settings | None = None,
    *,
    pipeline: Pipeline | None = None,
    idea_generator: IdeaGenerator | None = None,
    password_hasher: PasswordHasher | None = None,
    picture_runner: Runner | None = None,
) -> FastAPI:
    """The app. Tests pass their own `pipeline`, `idea_generator`, a cheap `password_hasher`
    and a `picture_runner`; otherwise `LEGOIN_PIPELINE` picks the pipeline,
    `LEGOIN_IDEAS_GENERATOR` what writes ideas, passwords get Argon2id's defaults, and picture
    builds run in a worker process when `LEGOIN_PICTURE_BUILDS` and the rest allow."""
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        warn_about_deployment(settings)
        app.state.store = Store(settings.data_dir / "legoin.db")
        app.state.photos = PhotoStore(settings.data_dir / "photos")
        app.state.jobs = JobManager(
            pipeline or load_pipeline(settings),
            app.state.store,
            app.state.photos,
            timeout_s=settings.scan_timeout_s,
            workers=settings.scan_workers,
            queue_size=settings.scan_queue_size,
        )
        app.state.ideas = idea_generator or load_idea_generator(settings, app.state.builds)
        # Built builds stay readable when picture builds are off; only new jobs need a runner.
        app.state.picture_store = PictureStore(settings.data_dir / "picture.db")
        app.state.picture_files = PictureFiles(settings.data_dir / "picture-files")
        app.state.picture_jobs = PictureJobs(
            app.state.picture_store,
            app.state.picture_files,
            settings.data_dir / "picture-jobs",
            picture_limits(settings),
            picture_runner or load_picture_runner(settings),
        )
        yield
        # The workers first, so no job writes to a closed store.
        app.state.jobs.shutdown()
        app.state.picture_jobs.shutdown()
        app.state.picture_store.close()
        app.state.store.close()
        if close := getattr(app.state.ideas, "aclose", None):
            await close()

    app = FastAPI(title="Legoin API", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.builds = BuildLibrary(settings.builds_dir)
    app.state.passwords = Passwords(password_hasher)
    app.state.failed_logins = FailedLogins()

    # Before CORS, so the CORS middleware wraps every answer, errors included.
    errors.install(app)
    # The contract's CORS rule: the web app's origins, its four methods and two headers. No
    # cookies, so no credentials (ADR-0011).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "Authorization"],
    )

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(scan_jobs.router)
    app.include_router(scans.router)
    app.include_router(ideas.router)
    app.include_router(photos.router)
    app.include_router(builds.router)
    app.include_router(picture_builds.router)
    return app
