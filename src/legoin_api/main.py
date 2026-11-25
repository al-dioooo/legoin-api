"""The FastAPI app.

Run it with `uv run uvicorn --factory legoin_api.main:create_app`.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from legoin_api import __version__, errors
from legoin_api.config import PipelineKind, Settings
from legoin_api.jobs import JobManager
from legoin_api.photos import PhotoStore
from legoin_api.pipeline import Pipeline
from legoin_api.routes import health, photos, scan_jobs, scans
from legoin_api.store import Store

log = logging.getLogger("legoin_api")


def load_pipeline(settings: Settings) -> Pipeline:
    if settings.pipeline is PipelineKind.STUB:
        log.warning(
            "Serving the stub pipeline: detections are made up. Set LEGOIN_PIPELINE=models."
        )
        from legoin_api.pipeline.stub import StubPipeline

        return StubPipeline()
    raise RuntimeError("LEGOIN_PIPELINE=models isn't available yet.")


def create_app(settings: Settings | None = None, *, pipeline: Pipeline | None = None) -> FastAPI:
    """The app. Tests pass their own `pipeline`; otherwise `LEGOIN_PIPELINE` picks it."""
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.store = Store(settings.data_dir / "legoin.db")
        app.state.photos = PhotoStore(settings.data_dir / "photos")
        app.state.jobs = JobManager(
            pipeline or load_pipeline(settings),
            app.state.store,
            app.state.photos,
            timeout_s=settings.scan_timeout_s,
            workers=settings.scan_workers,
        )
        yield
        app.state.jobs.shutdown()
        app.state.store.close()

    app = FastAPI(title="Legoin API", version=__version__, lifespan=lifespan)
    app.state.settings = settings

    # Before CORS, so the CORS middleware wraps every answer, errors included.
    errors.install(app)
    # The contract's CORS rule: the web app's origins, its four methods and two headers.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "Legoin-Device-Id"],
    )

    app.include_router(health.router)
    app.include_router(scan_jobs.router)
    app.include_router(scans.router)
    app.include_router(photos.router)
    return app
