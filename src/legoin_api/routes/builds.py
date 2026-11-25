"""Builds' pictures and models.

- The library's (ADR-0010): public catalogue content, not anyone's photos, so no
  `Authorization` header, and cached for good, since each URL carries its file's version.
- Those of the builds Claude made from a scan (ADR-0013), at `/picture-files/{name}`: no header
  either, but an unguessable name instead, as for photos."""

from fastapi import APIRouter
from fastapi.responses import FileResponse

from legoin_api.builds import FILES
from legoin_api.deps import BuildsDep, PictureFilesDep
from legoin_api.errors import ApiError
from legoin_api.picture.files import MEDIA_TYPES

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


@router.get("/picture-files/{name}", response_class=FileResponse)
def get_picture_file(name: str, files: PictureFilesDep) -> FileResponse:
    """A built idea's preview or model."""
    path = files.path(name)
    if path is None or not path.is_file():
        raise ApiError("not_found", "No such picture file.")
    suffix = name.rsplit(".", 1)[1]
    headers = {"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"}
    if suffix == "ldr":
        headers["Content-Disposition"] = 'attachment; filename="legoin-build.ldr"'
    return FileResponse(path, media_type=MEDIA_TYPES[suffix], headers=headers)
