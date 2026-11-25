"""The library of checked builds in `builds/` (ADR-0010), without BrickLayer or LDraw.

`scripts/builds.py` validates each build with BrickWright, counts its parts and renders it;
these tests check what it wrote: the manifest against the catalogue, every file against its
hash and size limit, and each template against its build. A `build.py` edited without
running the script fails here, since its hash no longer matches.
"""

import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest
from PIL import Image

from legoin_api.catalogue import COLOR_IDS, PART_IDS
from legoin_api.ideas.templates import TEMPLATES

BUILDS = Path(__file__).parent.parent / "builds"
MANIFEST = json.loads((BUILDS / "manifest.json").read_text())
ENTRIES = MANIFEST["builds"]
BY_ID = {entry["id"]: entry for entry in ENTRIES}

MAX_PREVIEW_BYTES = 150 * 1024
MAX_MODEL_BYTES = 16 * 1024
PREVIEW_SIZE = (640, 640)


def counts(lines: list[dict]) -> Counter:
    return Counter({(line["part"], line["color"]): line["quantity"] for line in lines})


def test_every_build_directory_is_in_the_manifest_once():
    directories = sorted(path.parent.name for path in BUILDS.glob("*/build.py"))

    assert [entry["id"] for entry in ENTRIES] == directories
    assert len(BY_ID) == len(ENTRIES)


@pytest.mark.parametrize("entry", ENTRIES, ids=lambda entry: entry["id"])
def test_a_build_uses_3_to_15_catalogue_pieces(entry):
    pairs = [(line["part"], line["color"]) for line in entry["parts"]]

    assert len(pairs) == len(set(pairs)), "each part and color once"
    for line in entry["parts"]:
        assert line["part"] in PART_IDS
        assert line["color"] in COLOR_IDS
        assert line["quantity"] >= 1
    assert 3 <= sum(line["quantity"] for line in entry["parts"]) <= 15


@pytest.mark.parametrize("entry", ENTRIES, ids=lambda entry: entry["id"])
def test_a_build_is_written_in_english_and_indonesian(entry):
    assert entry["theme"] in ("vehicles", "animals", "buildings")
    assert entry["difficulty"] in ("easy", "medium", "hard")
    for locale in ("en", "id"):
        assert entry["title"][locale].strip()
        assert entry["summary"][locale].strip()
        assert 2 <= len(entry["steps"][locale]) <= 5
        assert all(step.strip() for step in entry["steps"][locale])
    assert len(entry["steps"]["en"]) == len(entry["steps"]["id"])


@pytest.mark.parametrize("entry", ENTRIES, ids=lambda entry: entry["id"])
def test_a_builds_files_match_the_manifest(entry):
    for kind, name in (("source", "build.py"), ("model", "model.ldr"), ("preview", "preview.webp")):
        file = entry["files"][kind]
        path = BUILDS / file["path"]
        assert file["path"] == f"{entry['id']}/{name}"
        assert path.is_file()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == file["sha256"], (
            f"{file['path']} changed since the manifest was written: run scripts/builds.py"
        )


@pytest.mark.parametrize("entry", ENTRIES, ids=lambda entry: entry["id"])
def test_a_preview_is_a_small_square_webp_and_the_model_is_ldraw(entry):
    preview = BUILDS / entry["files"]["preview"]["path"]
    model = BUILDS / entry["files"]["model"]["path"]

    assert preview.stat().st_size <= MAX_PREVIEW_BYTES
    with Image.open(preview) as image:
        assert (image.format, image.size, image.mode) == ("WEBP", PREVIEW_SIZE, "RGBA")
    assert model.stat().st_size <= MAX_MODEL_BYTES
    parts = [line.split() for line in model.read_text().splitlines() if line.startswith("1 ")]
    assert sum(line["quantity"] for line in entry["parts"]) == len(parts)


@pytest.mark.parametrize(
    "template",
    [template for template in TEMPLATES if template["id"] in BY_ID],
    ids=lambda template: template["id"],
)
def test_a_template_matches_its_build(template):
    build = BY_ID[template["id"]]

    for field in ("theme", "difficulty", "title", "summary", "steps"):
        assert template[field] == build[field], field
    assert counts(template["requires"]) == counts(build["parts"])
