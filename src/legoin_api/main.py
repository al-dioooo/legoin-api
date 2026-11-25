"""The FastAPI app.

Run it with `uv run uvicorn --factory legoin_api.main:create_app`.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from argon2 import PasswordHasher
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from legoin_api import __version__, errors
from legoin_api.accounts import FailedLogins, Passwords
from legoin_api.builds import BuildLibrary
from legoin_api.config import IdeasGeneratorKind, PipelineKind, ScanBuildsKind, Settings
from legoin_api.ideas.generator import IdeaGenerator, TemplateIdeaGenerator
from legoin_api.jobs import JobManager
from legoin_api.photos import PhotoStore
from legoin_api.picture import availability
from legoin_api.picture.files import PictureFiles
from legoin_api.picture.jobs import BuildJobs, Limits, Runner
from legoin_api.picture.runner import ProcessRunner
from legoin_api.picture.scan_builds import ScanBuilds
from legoin_api.picture.store import PictureStore
from legoin_api.pipeline import Pipeline
from legoin_api.routes import auth, builds, health, ideas, photos, scan_jobs, scans
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


def load_build_runner(settings: Settings) -> Runner | None:
    """The worker process runner, or None when builds from scans can't run here (ADR-0013)."""
    if missing := availability.missing(settings):
        if settings.scan_builds is ScanBuildsKind.ANTHROPIC:
            log.warning("Builds from scans are off: %s.", "; ".join(missing))
        return None
    limits = build_limits(settings)
    log.info(
        "Builds from scans on, with %s at effort %s: $%.2f a day, %d sets a day per account, "
        "$%.2f a build",
        limits.model,
        limits.effort,
        limits.daily_budget,
        limits.daily_sets,
        limits.build_cost,
    )
    return ProcessRunner(
        deadline_s=limits.deadline_s(3), memory_mb=settings.scan_build_worker_memory_mb
    )


def build_limits(settings: Settings) -> Limits:
    return Limits(
        model=settings.scan_build_model,
        effort=settings.scan_build_effort,
        daily_budget=settings.scan_build_daily_budget_usd,
        daily_sets=settings.scan_build_daily_sets,
        build_cost=settings.scan_build_cost_usd,
        build_seconds=settings.scan_build_seconds,
        build_calls=settings.scan_build_calls,
        max_pieces=settings.scan_build_max_pieces,
        queue_size=settings.scan_build_queue_size,
        stagger_s=settings.scan_build_stagger_s,
    )


def delete_files(files: PictureFiles):
    def delete(names: list[str]) -> None:
        for name in names:
            files.delete(name)
        if names:
            log.info("Deleted %d files of the standalone picture builds (ADR-0013)", len(names))

    return delete


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
    build_runner: Runner | None = None,
) -> FastAPI:
    """The app. Tests pass their own `pipeline`, `idea_generator`, a cheap `password_hasher`
    and a `build_runner`; otherwise `LEGOIN_PIPELINE` picks the pipeline,
    `LEGOIN_IDEAS_GENERATOR` what writes ideas, passwords get Argon2id's defaults, and builds
    from scans run in a worker process when `LEGOIN_SCAN_BUILDS` and the rest allow."""
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
        # Built ideas stay readable when builds are off; only new sets need a runner.
        app.state.picture_files = PictureFiles(settings.data_dir / "picture-files")
        app.state.picture_store = PictureStore(
            settings.data_dir / "picture.db", delete_files=delete_files(app.state.picture_files)
        )
        # A runner passed in (tests) turns builds on.
        app.state.build_jobs = BuildJobs(
            app.state.picture_store,
            app.state.picture_files,
            settings.data_dir / "picture-jobs",
            build_limits(settings),
            build_runner or load_build_runner(settings),
        )
        app.state.scan_builds = ScanBuilds(
            app.state.build_jobs,
            app.state.store,
            app.state.photos,
            app.state.builds,
            app.state.ideas,
            asyncio.get_running_loop(),
        )
        yield
        # The workers first, so no job writes to a closed store.
        app.state.jobs.shutdown()
        app.state.build_jobs.shutdown()
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
    return app
