"""Whether picture builds can run here, worked out at start-up without importing the heavy
packages: the API process never loads BrickLayer or pygfx, only the worker does."""

import importlib.util
import os
from pathlib import Path

from legoin_api.config import (
    IdeaPreviewMode,
    IdeasGeneratorKind,
    PictureBuildsKind,
    Settings,
    anthropic_key_is_set,
)

EXTRA = ("bricklayer", "ldraw2mesh", "gltflib", "pygfx", "rendercanvas", "wgpu")
"""The `picture` extra's packages, by import name."""


def ldraw_library() -> Path | None:
    """The LDraw parts library ldraw2mesh will use: `LDRAW_LIBRARY_PATH`, else the places it
    looks by default. None if there's none."""
    candidates = []
    if configured := os.environ.get("LDRAW_LIBRARY_PATH"):
        candidates.append(Path(configured).expanduser())
    else:
        home = Path.home()
        candidates += [home / "ldraw", Path("/usr/share/ldraw"), Path("/usr/local/share/ldraw")]
    for path in candidates:
        if (path / "parts" / "3001.dat").is_file():
            return path
    return None


def missing(settings: Settings) -> list[str]:
    """What keeps picture builds off; empty when they can run."""
    if settings.picture_builds is PictureBuildsKind.OFF:
        return ["LEGOIN_PICTURE_BUILDS is off"]
    return prerequisites()


def missing_previews(settings: Settings) -> list[str]:
    """What keeps previews of ideas off; empty when they can run. They need Claude's ideas,
    and what picture builds need, but not picture builds themselves."""
    if settings.idea_previews is IdeaPreviewMode.OFF:
        return ["LEGOIN_IDEA_PREVIEWS is off"]
    if settings.ideas_kind is not IdeasGeneratorKind.ANTHROPIC:
        return ["ideas come from the templates, which have previews already"]
    return prerequisites()


def prerequisites() -> list[str]:
    """What the picture engine needs: the key, the `picture` extra and the parts library."""
    problems = []
    if not anthropic_key_is_set():
        problems.append("ANTHROPIC_API_KEY is unset")
    absent = [name for name in EXTRA if importlib.util.find_spec(name) is None]
    if absent:
        problems.append(
            f"the picture extra isn't installed ({', '.join(absent)}): uv sync --extra picture"
        )
    if ldraw_library() is None:
        problems.append("no LDraw parts library: set LDRAW_LIBRARY_PATH")
    return problems
