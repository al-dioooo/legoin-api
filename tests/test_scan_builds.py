"""Ideas built from the scan, through the routes (ADR-0013; contract section 6), with a fake
worker for the builds and a fake generator for ideas written as text."""

import time
from pathlib import Path

import pytest
from conftest import AUTH, FAST_HASHER
from fastapi.testclient import TestClient
from test_build_jobs import Runner, builds

from legoin_api.builds import BuildLibrary
from legoin_api.ideas.generator import ProposedIdea
from legoin_api.main import create_app
from legoin_api.picture.prompt import ANY_KIND, KINDS
from legoin_api.schemas import PartCount

SCAN = "scn_4kd2p9"


class Ideas:
    """Writes ideas as text, as Claude would, one per theme asked for."""

    def __init__(self):
        self.requests = []

    async def propose(self, request):
        self.requests.append(request)
        return [
            ProposedIdea(
                key=f"claude:idea-{slot}",
                theme=theme,
                difficulty="easy",
                title=f"Idea {slot}",
                summary="As written.",
                steps=["Written one.", "Written two."],
                requires=[PartCount(part="brick-2x4", color="red", quantity=1)],
            )
            for slot, theme in enumerate(request.themes)
        ]


@pytest.fixture
def make(settings):
    made = []

    def make(runner=None, ideas=None, off=False, **options):
        app = create_app(
            settings.model_copy(update={f"scan_build_{k}": v for k, v in options.items()}),
            password_hasher=FAST_HASHER,
            build_runner=None if off else runner or Runner(),
            idea_generator=ideas or Ideas(),
        )
        client = TestClient(app)
        client.__enter__()
        made.append(client)
        return client

    yield make
    for client in made:
        client.__exit__(None, None, None)


def seeded(client, example, detections=None):
    """The contract's scan, or the same scan with these pieces, in this app's store."""
    from legoin_api.schemas import Detection
    from legoin_api.store import StoredScan

    scan = example("scan-job-done")["scan"]
    photos = client.app.state.photos
    name = photos.new_name()
    (photos.directory / name).write_bytes(b"\xff\xd8\xff\xd9 the scan's photo")
    client.app.state.store.insert_scan(
        StoredScan(
            id=scan["id"],
            account_id="acc_test",
            created_at=scan["createdAt"],
            photo_file=name,
            photo_width=1200,
            photo_height=800,
            detections=[Detection.model_validate(d) for d in detections or scan["detections"]],
        )
    )


def ask(client, theme="vehicles", exclude=()):
    body = {"theme": theme, "locale": "en", "exclude": list(exclude)}
    answer = client.post(f"/v1/scans/{SCAN}/ideas", json=body, headers=AUTH)
    assert answer.status_code == 200, answer.text
    return answer.json()


def kept(client):
    return client.get(f"/v1/scans/{SCAN}", headers=AUTH).json().get("ideas", {})


def until(client, check, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ideas = kept(client)
        if check(ideas):
            return ideas
        time.sleep(0.02)
    raise AssertionError(f"never happened: {kept(client)}")


def settled(ideas):
    return "building" not in ideas and len(ideas.get("items", [])) == 3


def test_ideas_answer_at_once_as_building_and_become_their_builds(make, example):
    runner = Runner(hold=True)
    client = make(runner)
    seeded(client, example)

    answered = ask(client)

    assert answered["items"] == []
    assert [b["theme"] for b in answered["building"]] == ["vehicles"] * 3
    ids = [b["id"] for b in answered["building"]]
    runner.release.set()
    ideas = until(client, settled)
    assert [idea["id"] for idea in ideas["items"]] == ids
    idea = ideas["items"][0]
    assert (idea["title"], idea["summary"], idea["steps"]) == (
        "Build 0",
        "A red car.",
        ["One.", "Two."],
    )
    assert idea["difficulty"] == "easy"
    assert idea["buildId"].startswith("gen_")
    assert idea["preview"]["image"].startswith("http://testserver/v1/picture-files/")
    assert client.get(idea["preview"]["image"]).status_code == 200
    model = client.get(idea["preview"]["model"])
    assert model.headers["content-disposition"] == 'attachment; filename="legoin-build.ldr"'
    # Built from the scan's own pieces: nothing missing.
    assert idea["used"] == [{"part": "brick-2x4", "color": "red", "quantity": 1}]
    assert idea["missing"] == []
    stored = client.get(f"/v1/scans/{SCAN}/ideas/{idea['id']}", headers=AUTH).json()
    assert stored["idea"] == idea


def test_an_idea_still_building_cant_be_opened(make, example):
    client = make(Runner(hold=True))
    seeded(client, example)
    building = ask(client)["building"][0]

    answer = client.get(f"/v1/scans/{SCAN}/ideas/{building['id']}", headers=AUTH)

    assert answer.status_code == 404


def test_the_set_is_built_from_the_scans_photo_and_pieces(make, example):
    runner = Runner(hold=True)
    client = make(runner)
    seeded(client, example)
    photo = next(client.app.state.photos.directory.iterdir()).read_bytes()
    seen = []
    runner.job = lambda spec, emit, stop: seen.append(Path(spec["photo"]).read_bytes())

    ask(client, "surprise")
    runner.release.set()
    deadline = time.monotonic() + 5
    while not seen and time.monotonic() < deadline:
        time.sleep(0.01)

    spec = runner.specs[0]
    assert seen == [photo]
    assert spec["pieces"] == [
        {"part": "brick-2x2", "color": "dark-gray", "quantity": 1},
        {"part": "brick-2x4", "color": "red", "quantity": 1},
        {"part": "slope-2x2", "color": "green", "quantity": 1},
    ]
    assert [(b["theme"], b["kind"]) for b in spec["builds"]] == [
        ("vehicles", ANY_KIND["vehicles"]),
        ("animals", ANY_KIND["animals"]),
        ("buildings", ANY_KIND["buildings"]),
    ]
    assert (spec["locale"], spec["model"]) == ("en", "claude-opus-5-5")


def test_a_themes_three_builds_are_three_kinds_of_thing(make, example):
    runner = Runner()
    client = make(runner)
    seeded(client, example)

    ask(client, "animals")
    until(client, settled)

    assert [b["kind"] for b in runner.specs[0]["builds"]] == list(KINDS["animals"])


def test_a_library_build_that_fits_the_pieces_fills_its_slot(make, example):
    race_car = BuildLibrary(client_builds_dir()).get("race-car")
    detections = [
        {"id": f"det_{n}", "part": line.part, "color": line.color, "box": None,
         "confidence": None, "edited": True}
        for n, line in enumerate(race_car.parts)
        for _ in range(line.quantity)
    ]  # fmt: skip
    runner = Runner()
    client = make(runner)
    seeded(client, example, detections)

    answered = ask(client)

    assert answered["items"][0]["buildId"] == "race-car"
    assert answered["items"][0]["missing"] == []
    assert len(answered["building"]) == 2
    until(client, settled)
    assert len(runner.specs[0]["builds"]) == 2


def client_builds_dir():
    from legoin_api.config import Settings

    return Settings().builds_dir


@pytest.mark.parametrize("options", [{"off": True}, {"daily_sets": 0}, {"daily_budget_usd": 0.5}])
def test_when_builds_cant_take_the_set_ideas_are_written_as_text(make, example, options):
    ideas = Ideas()
    client = make(ideas=ideas, **options)
    seeded(client, example)

    answered = ask(client)

    assert "building" not in answered
    assert [idea["title"] for idea in answered["items"]] == ["Idea 0", "Idea 1", "Idea 2"]
    assert len(ideas.requests) == 1


def test_a_build_that_fails_is_written_as_text_in_its_place(make, example):
    ideas = Ideas()
    client = make(Runner(lambda spec, emit, stop: builds(spec, emit, stop, fail={1})), ideas)
    seeded(client, example)

    building = ask(client)["building"]
    done = until(client, settled)

    by_id = {idea["id"]: idea for idea in done["items"]}
    written = by_id[building[1]["id"]]
    assert written["title"] == "Idea 0" and "buildId" not in written
    assert by_id[building[0]["id"]]["buildId"].startswith("gen_")
    assert ideas.requests[0].themes == ["vehicles"]


def test_new_ideas_cancel_the_old_sets_builds(make, example):
    runner = Runner(hold=True)
    client = make(runner, daily_sets=9)
    seeded(client, example)
    first = ask(client)
    deadline = time.monotonic() + 5
    while not runner.specs and time.monotonic() < deadline:
        time.sleep(0.01)

    second = ask(client, exclude=[b["id"] for b in first["building"]])
    deadline = time.monotonic() + 5
    while not runner.stopped and time.monotonic() < deadline:
        time.sleep(0.01)
    runner.release.set()
    done = until(client, settled)

    assert runner.stopped == [runner.specs[0]["jobId"]]
    assert {idea["id"] for idea in done["items"]} == {b["id"] for b in second["building"]}
    assert len(list(client.app.state.picture_files.directory.iterdir())) == 6


def test_other_ideas_avoid_the_builds_already_seen(make, example):
    runner = Runner()
    client = make(runner, daily_sets=9)
    seeded(client, example)
    first = ask(client)
    until(client, settled)

    ask(client, exclude=[b["id"] for b in first["building"]])
    until(client, lambda ideas: len(runner.specs) == 2 and settled(ideas))

    assert runner.specs[1]["builds"][0]["avoid"] == ["Build 0", "Build 1", "Build 2"]


def test_a_correction_or_deleting_the_scan_drops_its_builds(make, example):
    client = make(daily_sets=9)
    seeded(client, example)
    ask(client)
    until(client, settled)
    files = client.app.state.picture_files.directory
    assert len(list(files.iterdir())) == 6

    change = {"changes": [{"type": "remove", "id": "det_a1"}]}
    assert (
        client.patch(f"/v1/scans/{SCAN}/detections", json=change, headers=AUTH).status_code == 200
    )
    assert list(files.iterdir()) == []

    ask(client)
    until(client, settled)
    client.delete(f"/v1/scans/{SCAN}", headers=AUTH)
    assert list(files.iterdir()) == []


def test_builds_never_fail_the_request_for_ideas(make, example, monkeypatch):
    client = make()
    seeded(client, example)

    def broken(*args):
        raise RuntimeError("the queue is on fire")

    monkeypatch.setattr(client.app.state.scan_builds, "after_ideas", broken)

    assert len(ask(client)["building"]) == 3


def test_health_says_whether_builds_run(make):
    assert make().get("/health").json()["scanBuilds"] == "anthropic"
    assert make(off=True).get("/health").json()["scanBuilds"] == "off"
