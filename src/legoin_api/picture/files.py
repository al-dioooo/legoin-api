"""A picture build's files on the local disk, served at unguessable URLs, like photos.

Each build has three: the uploaded picture as a JPEG with no metadata, its preview as WebP,
and its LDraw model. Each has a random 128-bit name, so `/v1/picture-files/{name}` loads in a
plain `<img>`, or downloads, with no header, but can't be guessed. They live exactly as long as
their build.
"""

import re
import secrets
import shutil
from pathlib import Path

from PIL import Image

NAME = re.compile(r"[A-Za-z0-9_-]{22}\.(jpg|webp|ldr)")

MEDIA_TYPES = {"jpg": "image/jpeg", "webp": "image/webp", "ldr": "application/x-ldraw"}


def save_jpeg(image: Image.Image, path: Path) -> None:
    """Writes the upright picture as a JPEG with no EXIF, comment or XMP, as photos are."""
    image.info = {}
    image.save(path, "JPEG", quality=90)


class PictureFiles:
    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def new_name(suffix: str) -> str:
        return f"{secrets.token_urlsafe(16)}.{suffix}"

    def adopt(self, source: Path, suffix: str) -> str:
        """Moves a finished job's file into place under a new name, and gives the name."""
        name = self.new_name(suffix)
        shutil.move(source, self.directory / name)
        return name

    def path(self, name: str) -> Path | None:
        """The file for a name, or None for a name the store never makes."""
        return self.directory / name if NAME.fullmatch(name) else None

    def delete(self, name: str) -> None:
        path = self.path(name)
        if path:
            path.unlink(missing_ok=True)
