"""The FastAPI app.

Run it with `uv run uvicorn --factory legoin_api.main:create_app`.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from legoin_api import __version__, errors
from legoin_api.config import Settings
from legoin_api.photos import PhotoStore
from legoin_api.routes import health, photos, scans
from legoin_api.store import Store


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.store = Store(settings.data_dir / "legoin.db")
        app.state.photos = PhotoStore(settings.data_dir / "photos")
        yield
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
    app.include_router(scans.router)
    app.include_router(photos.router)
    return app
