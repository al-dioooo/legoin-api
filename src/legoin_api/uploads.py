"""Reading a multipart upload with one image part, within the contract's 10 MB limit."""

from starlette.datastructures import UploadFile
from starlette.requests import Request
from starlette.types import Message, Receive

from legoin_api.errors import ApiError
from legoin_api.photos import MAX_PHOTO_BYTES

# Room for the multipart boundaries and headers around a photo at the limit.
MAX_BODY_BYTES = MAX_PHOTO_BYTES + 64 * 1024


def _limit_body(receive: Receive, limit: int) -> Receive:
    """Counts the body's bytes as they arrive and refuses it once past `limit`.

    A chunked upload has no `Content-Length` to check first, and would otherwise be spooled
    to disk in full before the photo's size is known.
    """
    seen = 0

    async def limited() -> Message:
        nonlocal seen
        message = await receive()
        if message["type"] == "http.request":
            seen += len(message.get("body", b""))
            if seen > limit:
                raise ApiError("photo_too_large", "The photo is over 10 MB.")
        return message

    return limited


async def read_upload(
    request: Request, part: str, fields: tuple[str, ...] = ()
) -> tuple[bytes, dict[str, str]]:
    """The bytes of the image in `part`, and the text `fields` that were sent."""
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > MAX_BODY_BYTES:
        raise ApiError("photo_too_large", "The photo is over 10 MB.")
    counted = Request(request.scope, _limit_body(request.receive, MAX_BODY_BYTES))
    try:
        form = await counted.form(max_files=1, max_fields=10)
    except ApiError:
        raise
    except Exception as error:
        raise ApiError("invalid_request", "The body isn't multipart/form-data.") from error
    upload = form.get(part)
    if not isinstance(upload, UploadFile):
        await form.close()
        raise ApiError("invalid_request", f"There's no {part} part.", field=part)
    # Read one byte past the limit: enough to know it's too large, without reading it all.
    data = await upload.read(MAX_PHOTO_BYTES + 1)
    values = {name: value for name in fields if isinstance(value := form.get(name), str)}
    await form.close()
    return data, values
