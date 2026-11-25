"""Photos on the local disk, served at unguessable URLs.

ADR-0008's provisional answer to the contract's open question 3: each photo is a JPEG in
`<data dir>/photos/` with a random 128-bit name, so `photo.src` loads in a plain `<img>` with
no header but can't be guessed. It lives exactly as long as its scan.
"""

import io
import re
import secrets
from pathlib import Path

from PIL import Image, ImageOps

from legoin_api.errors import ApiError

PHOTO_NAME = re.compile(r"[A-Za-z0-9_-]{22}\.jpg")

MAX_PHOTO_BYTES = 10 * 1024 * 1024
"""The contract's limit, legoin-web's `MAX_PHOTO_BYTES`."""

MAX_PHOTO_PIXELS = 4096 * 4096
"""Refuses decompression bombs, a small file that decodes to a huge image, from its header
alone. The web app uploads at most 1200 px; this leaves room for a 12 MP phone photo, and
caps a decoded photo at 48 MiB."""

# Pillow names some phone JPEGs, which hold a second, smaller image, "MPO".
FORMATS = {"JPEG", "MPO", "PNG", "WEBP"}


def read_photo(data: bytes) -> Image.Image:
    """The upright RGB image from an upload, or the contract's photo error.

    The EXIF orientation is applied before anything else, so the boxes, the stored photo and
    its width and height all agree.
    """
    if len(data) > MAX_PHOTO_BYTES:
        raise ApiError("photo_too_large", "The photo is over 10 MB.")
    try:
        with Image.open(io.BytesIO(data)) as opened:
            if opened.format not in FORMATS:
                raise ApiError("photo_type", "The photo isn't JPEG, PNG or WebP.")
            if opened.width * opened.height > MAX_PHOTO_PIXELS:
                raise ApiError("photo_too_large", "The photo has too many pixels.")
            return ImageOps.exif_transpose(opened).convert("RGB")
    except (ApiError, MemoryError):
        raise
    except Exception as error:
        # A corrupt file can raise almost anything from Pillow: `OSError`, `SyntaxError`,
        # `ValueError`, `TypeError` (from `exif_transpose`), `struct.error`, and so on.
        raise ApiError("photo_type", "The photo can't be decoded.") from error


class PhotoStore:
    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def new_name() -> str:
        return f"{secrets.token_urlsafe(16)}.jpg"

    def save(self, image: Image.Image) -> str:
        """Writes the upright photo as a JPEG with no metadata: no EXIF, so no location or
        camera data, and no comment or XMP. It clears `image.info`, which Pillow would copy."""
        name = self.new_name()
        image.info = {}
        image.save(self.directory / name, "JPEG", quality=90)
        return name

    def path(self, name: str) -> Path | None:
        """The file for a name, or None for a name the store never makes."""
        return self.directory / name if PHOTO_NAME.fullmatch(name) else None

    def delete(self, name: str) -> None:
        path = self.path(name)
        if path:
            path.unlink(missing_ok=True)
