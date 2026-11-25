"""Photos on the local disk, served at unguessable URLs.

ADR-0008's provisional answer to the contract's open question 3: each photo is a JPEG in
`<data dir>/photos/` with a random 128-bit name, so `photo.src` loads in a plain `<img>` with
no header but can't be guessed. It lives exactly as long as its scan.
"""

import re
import secrets
from pathlib import Path

PHOTO_NAME = re.compile(r"^[A-Za-z0-9_-]{22}\.jpg$")


class PhotoStore:
    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def new_name() -> str:
        return f"{secrets.token_urlsafe(16)}.jpg"

    def path(self, name: str) -> Path | None:
        """The file for a name, or None for a name the store never makes."""
        return self.directory / name if PHOTO_NAME.match(name) else None

    def delete(self, name: str) -> None:
        path = self.path(name)
        if path:
            path.unlink(missing_ok=True)
