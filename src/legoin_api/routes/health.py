"""`GET /health`: for a host's health check. Not part of the contract, so outside `/v1`."""

from fastapi import APIRouter, Request

from legoin_api import __version__

router = APIRouter(tags=["health"])


@router.get("/health")
def health(request: Request) -> dict[str, str]:
    settings = request.app.state.settings
    answer = {"status": "ok", "version": __version__, "pipeline": settings.pipeline}
    if settings.pipeline == "models":
        answer["modelTag"] = settings.model_tag
    return answer
