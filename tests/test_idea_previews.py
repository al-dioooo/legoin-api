"""Previews of ideas through the routes (ADR-0012, "Idea previews"; contract section 13), with a
fake Claude for ideas and a fake runner for the previews."""

import time
from pathlib import Path

import pytest
from conftest import AUTH, FAST_HASHER
from fastapi.testclient import TestClient
from picture_fakes import CAR
from test_picture_jobs import Runner

from legoin_api.config import IdeaPreviewMode
from legoin_api.ideas.generator import ProposedIdea
from legoin_api.main import create_app
from legoin_api.schemas import PartCount

SCAN = "scn_4kd2p9"


class Ideas:
    """Writes the same three ideas every time, as Claude would, without library picks unless
    `library` names a slot to answer with the race car."""

    def __init__(self, library: int | None = None):
        self.library = library

    async def propose(self, request):
        ideas = []
        for slot, theme in enumerate(request.themes):
            if slot == self.library:
                ideas.append(
                    ProposedIdea(
                        key="template:race-car",
                        theme=theme,
                        difficulty="easy",
                        title="Little race car",
                        summary="Red body.",
                        steps=["One.", "Two."],
                        requires=[PartCount(part="brick-2x4", color="red", quantity=1)],
                        build_id="race-car",
                    )
                )
                continue
            ideas.append(
                ProposedIdea(
                    key=f"claude:idea-{slot}",
                    theme=theme,
                    difficulty="easy",
                    title=f"Idea {slot}",
                    summary="As written.",
                    steps=["Written one.", "Written two."],
                    requires=[
                        PartCount(part="brick-2x4", color="red", quantity=1),
                        PartCount(part="slope-2x2", color="green", quantity=1),
                    ],
                )
            )
        return ideas


def built(spec, emit, stop):
    """A preview built with two red bricks and a green slope: one more red brick than listed."""
    emit({"type": "called", "model": "claude-opus-5-5", "cost": 0.04, "usage": {}})
    out = Path(spec["out"])
    (out / "preview.webp").write_bytes(b"webp")
    (out / "model.ldr").write_text("0 preview\n")
    parts = [
        {"part": "brick-2x4", "color": "red", "quantity": 2},
        {"part": "slope-2x2", "color": "green", "quantity": 1},
    ]
    pieces = [{"id": f"p{i + 1}", **piece} for i, piece in enumerate(CAR[:3])]
    emit(
        {
            "type": "done",
            "build": {
                "title": spec["idea"]["title"],
                "summary": "As built.",
                "steps": ["Built one.", "Built two.", "Built three."],
                "parts": parts,
                "pieces": pieces,
                "plain": False,
            },
        }
    )


@pytest.fixture
def make(settings):
    made = []

    def make(runner=None, mode="all", ideas=None, **limits):
        options = {f"idea_preview_{key}": value for key, value in limits.items()}
        options["idea_previews"] = IdeaPreviewMode(mode)
        app = create_app(
            settings.model_copy(update=options),
            password_hasher=FAST_HASHER,
            picture_runner=runner or Runner(built),
            idea_generator=ideas or Ideas(),
        )
        client = TestClient(app)
        client.__enter__()
        made.append(client)
        return client

    yield make
    for client in made:
        client.__exit__(None, None, None)


def seeded(client, example):
    """The contract's scan in this app's store."""
    from legoin_api.schemas import Detection
    from legoin_api.store import StoredScan

    scan = example("scan-job-done")["scan"]
    photos = client.app.state.photos
    name = photos.new_name()
    (photos.directory / name).write_bytes(b"\xff\xd8\xff\xd9")
    client.app.state.store.insert_scan(
        StoredScan(
            id=scan["id"],
            account_id="acc_test",
            created_at=scan["createdAt"],
            photo_file=name,
            photo_width=1200,
            photo_height=800,
            detections=[Detection.model_validate(d) for d in scan["detections"]],
        )
    )


def ask(client, theme="vehicles"):
    answer = client.post(
        f"/v1/scans/{SCAN}/ideas", json={"theme": theme, "locale": "en"}, headers=AUTH
    )
    assert answer.status_code == 200, answer.text
    return answer.json()["items"]


def scan_ideas(client):
    return client.get(f"/v1/scans/{SCAN}", headers=AUTH).json().get("ideas", {}).get("items", [])


def until(client, check, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        items = scan_ideas(client)
        if check(items):
            return items
        time.sleep(0.02)
    raise AssertionError(f"never happened: {scan_ideas(client)}")


def test_ideas_answer_pending_and_become_their_builds(make, example):
    runner = Runner(built, hold=True)
    client = make(runner)
    seeded(client, example)

    answered = ask(client)

    assert [idea["previewStatus"] for idea in answered] == ["pending"] * 3
    assert all("preview" not in idea for idea in answered)
    runner.release.set()
    ready = until(client, lambda items: all(i["previewStatus"] == "ready" for i in items))
    idea = ready[0]
    assert idea["title"] == "Idea 0"
    assert (idea["summary"], idea["steps"]) == (
        "As built.",
        ["Built one.", "Built two.", "Built three."],
    )
    assert idea["buildId"].startswith("gen_")
    assert idea["preview"]["image"].startswith("http://testserver/v1/picture-files/")
    assert client.get(idea["preview"]["image"]).status_code == 200
    # The build has two red bricks; the scan has one, so one is missing now.
    assert idea["used"] == [
        {"part": "brick-2x4", "color": "red", "quantity": 1},
        {"part": "slope-2x2", "color": "green", "quantity": 1},
    ]
    assert idea["missing"] == [{"part": "brick-2x4", "color": "red", "quantity": 1}]
    stored = client.get(f"/v1/scans/{SCAN}/ideas/{idea['id']}", headers=AUTH).json()
    assert stored["idea"] == idea


def test_the_preview_is_built_from_the_ideas_own_parts(make, example):
    runner = Runner(built)
    client = make(runner)
    seeded(client, example)
    ask(client, "surprise")
    until(client, lambda items: all(i["previewStatus"] == "ready" for i in items))

    spec = runner.specs[0]
    assert spec["kind"] == "idea"
    assert spec["idea"]["parts"] == [
        {"part": "brick-2x4", "color": "red", "quantity": 1},
        {"part": "slope-2x2", "color": "green", "quantity": 1},
    ]
    assert spec["locale"] == "en"


def test_a_library_idea_keeps_its_preview_and_gets_no_status(make, example):
    client = make(ideas=Ideas(library=1))
    seeded(client, example)

    answered = ask(client)

    assert "previewStatus" not in answered[1]
    assert answered[1]["buildId"] == "race-car"
    assert answered[1]["preview"]["image"].endswith(answered[1]["preview"]["image"].split("/")[-1])


def test_on_open_builds_only_the_idea_that_is_opened(make, example):
    runner = Runner(built)
    client = make(runner, mode="on_open")
    seeded(client, example)

    answered = ask(client)
    assert [idea["previewStatus"] for idea in answered] == ["on_open"] * 3
    opened = client.get(f"/v1/scans/{SCAN}/ideas/{answered[1]['id']}", headers=AUTH).json()

    assert opened["idea"]["previewStatus"] in ("pending", "ready")
    items = until(client, lambda items: items[1]["previewStatus"] == "ready")
    assert [item["previewStatus"] for item in items] == ["on_open", "ready", "on_open"]
    assert len(runner.specs) == 1


def test_with_previews_off_ideas_are_as_before(make, example):
    client = make(mode="off")
    seeded(client, example)

    answered = ask(client)

    assert all("previewStatus" not in idea for idea in answered)
    assert client.get("/health").json()["ideaPreviews"] == "off"


def test_new_ideas_cancel_the_old_sets_previews(make, example):
    runner = Runner(built, hold=True)
    client = make(runner)
    seeded(client, example)
    first = ask(client)
    deadline = time.monotonic() + 5
    while len(runner.specs) < 1 and time.monotonic() < deadline:
        time.sleep(0.01)

    second = ask(client)
    runner.release.set()
    until(client, lambda items: all(i["previewStatus"] == "ready" for i in items))

    old = {idea["id"] for idea in first}
    assert runner.stopped == [runner.specs[0]["jobId"]]
    assert {spec["idea"]["ideaId"] for spec in runner.specs[1:]} == {i["id"] for i in second}
    assert not old & {spec["idea"]["ideaId"] for spec in runner.specs[1:]}
    # Only the new set's three previews are kept.
    files = list(client.app.state.picture_files.directory.iterdir())
    assert len(files) == 6


def test_a_failed_preview_leaves_the_idea_as_text(make, example):
    def fails(spec, emit, stop):
        emit({"type": "failed", "code": "no_valid_build", "message": "No."})

    client = make(Runner(fails))
    seeded(client, example)
    ask(client)

    items = until(client, lambda items: all(i["previewStatus"] == "unavailable" for i in items))
    assert items[0]["steps"] == ["Written one.", "Written two."]
    assert "buildId" not in items[0]


def test_past_the_daily_limit_a_preview_is_unavailable_at_once(make, example):
    client = make(Runner(built, hold=True), daily_limit=2)
    seeded(client, example)

    answered = ask(client)

    assert [idea["previewStatus"] for idea in answered] == ["pending", "pending", "unavailable"]


def test_deleting_the_scan_or_correcting_it_drops_its_previews(make, example):
    client = make()
    seeded(client, example)
    ask(client)
    until(client, lambda items: all(i["previewStatus"] == "ready" for i in items))
    files = client.app.state.picture_files.directory
    assert len(list(files.iterdir())) == 6

    change = {"changes": [{"type": "remove", "id": "det_a1"}]}
    assert (
        client.patch(f"/v1/scans/{SCAN}/detections", json=change, headers=AUTH).status_code == 200
    )
    assert list(files.iterdir()) == []

    ask(client)
    until(client, lambda items: all(i["previewStatus"] == "ready" for i in items))
    client.delete(f"/v1/scans/{SCAN}", headers=AUTH)
    assert list(files.iterdir()) == []


def test_previews_never_fail_the_request_for_ideas(make, example, monkeypatch):
    client = make()
    seeded(client, example)

    def broken(*args):
        raise RuntimeError("the queue is on fire")

    monkeypatch.setattr(client.app.state.idea_previews, "after_ideas", broken)

    answered = ask(client)

    assert len(answered) == 3
