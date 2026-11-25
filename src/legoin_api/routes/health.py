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
    # What was configured at start-up; a request Claude can't answer still gets templates.
    answer["ideas"] = settings.ideas_kind
    if settings.ideas_kind == "anthropic":
        answer["ideasModel"] = settings.ideas_model
    # Whether picture jobs are accepted: configured, and everything they need is here.
    jobs = getattr(request.app.state, "picture_jobs", None)
    answer["pictureBuilds"] = "anthropic" if jobs and jobs.enabled else "off"
    if answer["pictureBuilds"] == "anthropic":
        answer["pictureModel"] = settings.picture_model
    return answer
