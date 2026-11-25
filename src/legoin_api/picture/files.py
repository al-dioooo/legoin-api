"""The files of the builds Claude makes from scans, on the local disk, served at unguessable
URLs, like photos (ADR-0013).

Each build has two: its preview as WebP and its LDraw model. Each has a random 128-bit name, so
`/v1/picture-files/{name}` loads in a plain `<img>`, or downloads, with no header, but can't be
guessed. They live exactly as long as their build.
"""

import re
import secrets
import shutil
from pathlib import Path

NAME = re.compile(r"[A-Za-z0-9_-]{22}\.(jpg|webp|ldr)")
"""`jpg` is still a name the files had: the standalone picture builds' uploads (ADR-0012)."""

MEDIA_TYPES = {"jpg": "image/jpeg", "webp": "image/webp", "ldr": "application/x-ldraw"}


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
