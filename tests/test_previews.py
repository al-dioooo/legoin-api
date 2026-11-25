"""Previews of the library's builds on ideas (ADR-0010)."""

import json
from pathlib import Path

import pytest
from conftest import DEVICE
from fastapi.testclient import TestClient

from legoin_api.ideas.templates import TEMPLATES
from legoin_api.main import create_app
from legoin_api.schemas import Detection
from legoin_api.store import StoredScan

HEADERS = {"Legoin-Device-Id": DEVICE}
BUILDS = Path(__file__).parent.parent / "builds"
MANIFEST = {
    entry["id"]: entry for entry in json.loads((BUILDS / "manifest.json").read_text())["builds"]
}
BY_TITLE = {t["title"][locale]: t["id"] for t in TEMPLATES for locale in ("en", "id")}


def version(build_id: str, kind: str) -> str:
    return MANIFEST[build_id]["files"][kind]["sha256"][:12]


@pytest.fixture
def corrected(seed, example):
    return seed(example("patch-detections-response"))


def ask(client, theme="vehicles", locale="en"):
    body = {"theme": theme, "locale": locale}
    return client.post("/v1/scans/scn_4kd2p9/ideas", headers=HEADERS, json=body)


def test_template_ideas_carry_their_build_and_its_preview(client, corrected):
    ideas = ask(client, theme="surprise").json()["items"]

    for idea in ideas:
        build_id = BY_TITLE[idea["title"]]
        url = f"http://testserver/v1/builds/{build_id}"
        assert idea["buildId"] == build_id
        assert idea["preview"] == {
            "image": f"{url}/preview.webp?v={version(build_id, 'preview')}",
            "model": f"{url}/model.ldr?v={version(build_id, 'model')}",
        }


def test_previews_use_the_public_url_when_set(settings, example):
    app = create_app(settings.model_copy(update={"public_url": "https://api.legoin.example/"}))
    scan = example("patch-detections-response")
    with TestClient(app) as client:
        client.app.state.store.insert_scan(
            StoredScan(
                id=scan["id"],
                device_id=DEVICE,
                created_at=scan["createdAt"],
                photo_file=client.app.state.photos.new_name(),
                photo_width=scan["photo"]["width"],
                photo_height=scan["photo"]["height"],
                detections=[Detection.model_validate(d) for d in scan["detections"]],
            )
        )
        idea = ask(client, locale="id").json()["items"][0]

    # The contract's example race car, with the files as they are now.
    expected = example("ideas-response")["items"][0]
    assert idea["title"] == expected["title"]
    url = "https://api.legoin.example/v1/builds/race-car"
    assert idea["preview"] == {
        "image": f"{url}/preview.webp?v={version('race-car', 'preview')}",
        "model": f"{url}/model.ldr?v={version('race-car', 'model')}",
    }
    assert idea["steps"] == expected["steps"]


def test_the_kept_set_and_one_idea_read_back_carry_the_same_previews(client, corrected):
    written = ask(client).json()

    scan = client.get("/v1/scans/scn_4kd2p9", headers=HEADERS).json()
    one = client.get(f"/v1/scans/scn_4kd2p9/ideas/{written['items'][1]['id']}", headers=HEADERS)

    assert scan["ideas"] == written
    assert one.json() == {"idea": written["items"][1], "set": written}


def test_ideas_are_kept_with_their_build_id_only(client, corrected):
    ask(client)

    kept = client.app.state.store.get_scan(DEVICE, "scn_4kd2p9").ideas

    assert all(idea.build_id and idea.preview is None for idea in kept.items)
