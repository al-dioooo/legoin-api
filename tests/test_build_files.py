"""The library's files, served without a header (ADR-0010)."""

import hashlib
import json
from pathlib import Path

import pytest

from legoin_api.builds import BuildLibrary
from legoin_api.schemas import Idea, IdeaSet

BUILDS = Path(__file__).parent.parent / "builds"
MANIFEST = {
    entry["id"]: entry for entry in json.loads((BUILDS / "manifest.json").read_text())["builds"]
}


def test_a_preview_loads_without_a_header_and_is_cached_for_good(client):
    response = client.get("/v1/builds/race-car/preview.webp?v=whatever")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/webp"
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert response.headers["x-content-type-options"] == "nosniff"
    digest = hashlib.sha256(response.content).hexdigest()
    assert digest == MANIFEST["race-car"]["files"]["preview"]["sha256"]


def test_a_model_downloads_as_an_ldraw_file(client):
    response = client.get("/v1/builds/rubber-duck/model.ldr")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/x-ldraw"
    assert (
        response.headers["content-disposition"] == 'attachment; filename="legoin-rubber-duck.ldr"'
    )
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert response.text.startswith("0 Rubber duck\n")


@pytest.mark.parametrize(
    "path",
    [
        "/v1/builds/no-such-build/preview.webp",
        "/v1/builds/race-car/build.py",
        "/v1/builds/race-car/../manifest.json",
        "/v1/builds/race-car/%2E%2E%2Fmanifest.json",
        "/v1/builds/manifest.json/preview.webp",
    ],
)
def test_only_a_builds_preview_and_model_are_served(client, path):
    response = client.get(path)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_an_idea_whose_build_left_the_library_loses_its_build_id(tmp_path):
    library = BuildLibrary(BUILDS)
    ideas = IdeaSet(
        theme="vehicles",
        locale="en",
        items=[
            Idea(
                id=f"idea_{n}",
                theme="vehicles",
                difficulty="easy",
                title="A build",
                summary="One.",
                steps=["Build it."],
                used=[],
                missing=[{"part": "brick-1x1", "color": "red", "quantity": 1}],
                build_id=build_id,
            )
            for n, build_id in enumerate(("race-car", "gone", None))
        ],
    )

    items = library.with_previews(ideas, "https://api.example").wire()["items"]
    empty = BuildLibrary(tmp_path).with_previews(ideas, "https://api.example").wire()["items"]

    assert items[0]["buildId"] == "race-car" and "preview" in items[0]
    assert "buildId" not in items[1] and "preview" not in items[1]
    assert "buildId" not in items[2] and "preview" not in items[2]
    assert all("buildId" not in item and "preview" not in item for item in empty)
