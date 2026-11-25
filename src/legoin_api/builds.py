"""The library of checked builds (ADR-0010), read from `builds/manifest.json`.

`scripts/builds.py` makes the library offline with BrickLayer: each build's text, parts,
LDraw model and rendered preview. The service never runs BrickLayer. It serves the files,
gives template ideas their build's `buildId` and `preview`, and tells Claude what's in the
library.
"""

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from legoin_api.ideas.generator import ProposedIdea
from legoin_api.schemas import Difficulty, IdeaSet, IdeaTheme, Locale, PartCount, Preview

log = logging.getLogger(__name__)

FILES = {"preview.webp": "image/webp", "model.ldr": "application/x-ldraw"}
"""The files a build serves, with their media types."""


@dataclass(frozen=True)
class LibraryBuild:
    id: str
    theme: IdeaTheme
    difficulty: Difficulty
    title: dict[Locale, str]
    summary: dict[Locale, str]
    steps: dict[Locale, list[str]]
    parts: list[PartCount]
    """Every part the build needs, counted from the build itself."""
    versions: dict[str, str]
    """Each served file's name and the start of its SHA-256, which versions its URL."""

    @property
    def key(self) -> str:
        """The generator key of the template with this id, so "Try other ideas" knows a
        library build Claude picked as the same idea as that template."""
        return f"template:{self.id}"

    def propose(self, locale: Locale) -> ProposedIdea:
        return ProposedIdea(
            key=self.key,
            theme=self.theme,
            difficulty=self.difficulty,
            title=self.title[locale],
            summary=self.summary[locale],
            steps=list(self.steps[locale]),
            requires=list(self.parts),
            build_id=self.id,
        )

    @classmethod
    def from_manifest(cls, entry: dict) -> "LibraryBuild":
        files = entry["files"]
        return cls(
            id=entry["id"],
            theme=entry["theme"],
            difficulty=entry["difficulty"],
            title=entry["title"],
            summary=entry["summary"],
            steps=entry["steps"],
            parts=[PartCount.model_validate(line) for line in entry["parts"]],
            versions={
                "preview.webp": files["preview"]["sha256"][:12],
                "model.ldr": files["model"]["sha256"][:12],
            },
        )


class BuildLibrary:
    """The builds in a directory written by `scripts/builds.py`; empty if it has none."""

    @classmethod
    def empty(cls) -> "BuildLibrary":
        library = cls.__new__(cls)
        library.directory = Path()
        library.builds = {}
        return library

    def __init__(self, directory: Path):
        self.directory = directory
        manifest = directory / "manifest.json"
        if not manifest.is_file():
            log.warning("No build library at %s: ideas get no previews.", directory)
            self.builds: dict[str, LibraryBuild] = {}
            return
        entries = json.loads(manifest.read_text())["builds"]
        self.builds = {entry["id"]: LibraryBuild.from_manifest(entry) for entry in entries}

    def __len__(self) -> int:
        return len(self.builds)

    def __iter__(self):
        """The builds by id, an order that only changes when the library does."""
        return iter(sorted(self.builds.values(), key=lambda build: build.id))

    def get(self, build_id: str) -> LibraryBuild | None:
        return self.builds.get(build_id)

    def file(self, build_id: str, name: str) -> Path | None:
        """A build's preview or model, or None for anything else."""
        if build_id not in self.builds or name not in FILES:
            return None
        path = self.directory / build_id / name
        return path if path.is_file() else None

    def preview(self, build_id: str, base_url: str) -> Preview | None:
        """The build's picture and model as absolute, versioned URLs."""
        build = self.builds.get(build_id)
        if build is None:
            return None
        url = f"{base_url}/v1/builds/{build_id}"
        return Preview(
            image=f"{url}/preview.webp?v={build.versions['preview.webp']}",
            model=f"{url}/model.ldr?v={build.versions['model.ldr']}",
        )

    def with_previews(
        self,
        ideas: IdeaSet,
        base_url: str,
        built: Callable[[str], Preview | None] | None = None,
    ) -> IdeaSet:
        """The set with each idea's `preview`, as of now.

        Ideas are kept with their `buildId` only, so their URLs follow the base URL and the
        files' versions. A library build's comes from here; a built preview's (`gen_…`,
        ADR-0012) from `built`. An idea whose build is gone loses its `buildId`, since the
        contract sends the two together.
        """
        items = []
        for idea in ideas.items:
            preview = None
            if idea.build_id and idea.build_id.startswith("gen_"):
                preview = built(idea.build_id) if built else None
            elif idea.build_id:
                preview = self.preview(idea.build_id, base_url)
            build_id = idea.build_id if preview else None
            items.append(idea.model_copy(update={"build_id": build_id, "preview": preview}))
        return ideas.model_copy(update={"items": items})
