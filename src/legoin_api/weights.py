"""The model files, downloaded from a pinned legoin-ml release and checked by SHA-256.

Each release tag the backend knows has its files' hashes here, copied from the release notes
(ADR-0005, ADR-0008). A file that doesn't match is never loaded. Moving to a new tag is a code
change: add its hashes, and its settings in `pipeline/models.py`, from its release notes.

`python -m legoin_api.weights` downloads them ahead of time, e.g. while building an image.
"""

import hashlib
import logging
import sys
import urllib.request
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger(__name__)

RELEASES_URL = "https://github.com/al-dioooo/legoin-ml/releases/download"
ATTEMPTS = 3

DETECTOR = "best.pt"
CLASSIFIER = "legoin_mobilenetv3_part.pt"
CLASSIFIER_LABELS = "legoin_mobilenetv3_part_labels.json"
COLOR_PARAMETERS = "legoin_warna.json"

MANIFESTS: dict[str, dict[str, str]] = {
    # https://github.com/al-dioooo/legoin-ml/releases/tag/v0.2.0
    "v0.2.0": {
        DETECTOR: "60f5b3a881b2a289aa2de86f7e1695cb68d8e63c91687e1722c6454fb00b2a21",
        CLASSIFIER: "e8c596a91b85ada2985670e1289a862240f03536456b675e8f691158baaa5e5b",
        CLASSIFIER_LABELS: "9ead94b90b3254886b79dc545602248e1c2ecc65ac3d65e31daea161601049fc",
        COLOR_PARAMETERS: "bda4af6c3595570274dcb91379571573704c0c42d2b68d7de0a02c216b473634",
    },
}


class ModelFileError(RuntimeError):
    """A model file is missing, can't be downloaded, or isn't the released one."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, target: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "legoin-api"})
    with urllib.request.urlopen(request, timeout=60) as response, target.open("wb") as file:
        while chunk := response.read(1 << 20):
            file.write(chunk)


def ensure_models(
    tag: str, directory: Path, fetch: Callable[[str, Path], None] = download
) -> dict[str, Path]:
    """Every model file for `tag`, checked, downloading what's missing into `directory/tag`."""
    if tag not in MANIFESTS:
        known = ", ".join(MANIFESTS)
        raise ModelFileError(f"No SHA-256 hashes for model tag {tag!r}; known tags: {known}.")
    folder = directory / tag
    folder.mkdir(parents=True, exist_ok=True)
    files = {}
    for name, expected in MANIFESTS[tag].items():
        path = folder / name
        if path.exists() and sha256(path) != expected:
            log.warning("%s doesn't match its SHA-256; downloading it again", path)
            path.unlink()
        if not path.exists():
            url = f"{RELEASES_URL}/{tag}/{name}"
            log.info("Downloading %s", url)
            partial = path.with_name(path.name + ".part")
            for attempt in range(1, ATTEMPTS + 1):
                try:
                    fetch(url, partial)
                    break
                except Exception as error:
                    partial.unlink(missing_ok=True)
                    if attempt == ATTEMPTS:
                        raise ModelFileError(f"Couldn't download {url}: {error}") from error
                    log.warning("Download failed (%s); trying again", error)
            actual = sha256(partial)
            if actual != expected:
                partial.unlink()
                raise ModelFileError(f"{url} has SHA-256 {actual}, not {expected}.")
            partial.rename(path)
        files[name] = path
    return files


def main() -> None:
    from legoin_api.config import Settings

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    settings = Settings()
    try:
        files = ensure_models(settings.model_tag, settings.models_path)
    except ModelFileError as error:
        sys.exit(str(error))
    for name, path in files.items():
        print(f"{name}: {path} (SHA-256 checked)")


if __name__ == "__main__":
    main()
