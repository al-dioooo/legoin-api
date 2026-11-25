"""Photos, for a plain `<img>`: no device header, an unguessable name instead."""

from fastapi import APIRouter
from fastapi.responses import FileResponse

from legoin_api.deps import PhotosDep
from legoin_api.errors import ApiError

router = APIRouter(prefix="/v1", tags=["photos"])


@router.get("/photos/{name}", response_class=FileResponse)
def get_photo(name: str, photos: PhotosDep) -> FileResponse:
    path = photos.path(name)
    if path is None or not path.is_file():
        raise ApiError("not_found", "No such photo.")
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"},
    )
