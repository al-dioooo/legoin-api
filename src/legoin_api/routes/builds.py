"""The library's previews and models (ADR-0010): public catalogue content, not anyone's photos,
so no device header, and cached for good, since each URL carries its file's version."""

from fastapi import APIRouter
from fastapi.responses import FileResponse

from legoin_api.builds import FILES
from legoin_api.deps import BuildsDep
from legoin_api.errors import ApiError

router = APIRouter(prefix="/v1", tags=["builds"])

CACHE_FOREVER = "public, max-age=31536000, immutable"


@router.get("/builds/{build_id}/{name}", response_class=FileResponse)
def get_build_file(build_id: str, name: str, builds: BuildsDep) -> FileResponse:
    path = builds.file(build_id, name)
    if path is None:
        raise ApiError("not_found", "No such build file.")
    headers = {"Cache-Control": CACHE_FOREVER, "X-Content-Type-Options": "nosniff"}
    if name == "model.ldr":
        headers["Content-Disposition"] = f'attachment; filename="legoin-{build_id}.ldr"'
    return FileResponse(path, media_type=FILES[name], headers=headers)
