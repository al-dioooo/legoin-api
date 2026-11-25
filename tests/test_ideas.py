import asyncio

import pytest
from conftest import DEVICE
from fastapi.testclient import TestClient

from legoin_api.ideas.generator import (
    CEK_INVENTORI_TOOL,
    IdeasUnavailable,
    ProposedIdea,
    TemplateIdeaGenerator,
    run_cek_inventori_tool,
)
from legoin_api.ideas.templates import TEMPLATES
from legoin_api.inventory import cek_inventori, merge
from legoin_api.main import create_app
from legoin_api.schemas import PartCount

HEADERS = {"Legoin-Device-Id": DEVICE}
TITLES = {locale: {t["title"][locale]: t for t in TEMPLATES} for locale in ("en", "id")}


def lines(items: list[dict]) -> list[PartCount]:
    return [PartCount.model_validate(item) for item in items]


@pytest.fixture
def corrected(seed, example):
    """The scan after the contract's correction: four pieces, as its ideas example says."""
    return seed(example("patch-detections-response"))


def ask(client, theme="vehicles", locale="en", exclude=(), scan="scn_4kd2p9"):
    body = {"theme": theme, "locale": locale, "exclude": list(exclude)}
    return client.post(f"/v1/scans/{scan}/ideas", headers=HEADERS, json=body)


# cek_inventori


def test_cek_inventori_gives_the_contracts_used_and_missing(example):
    inventory = lines(
        [
            {"part": "brick-2x4", "color": "red", "quantity": 1},
            {"part": "slope-2x2", "color": "green", "quantity": 1},
            {"part": "brick-2x2", "color": "black", "quantity": 1},
            {"part": "plate-1x2", "color": "blue", "quantity": 1},
        ]
    )

    for idea in example("ideas-response")["items"]:
        needed = lines(idea["used"] + idea["missing"])
        used, missing = cek_inventori(needed, inventory)
        assert [line.wire() for line in used] == idea["used"]
        assert [line.wire() for line in missing] == idea["missing"]


def test_cek_inventori_never_counts_a_missing_part_as_owned():
    inventory = lines([{"part": "brick-1x1", "color": "red", "quantity": 2}])
    needed = lines(
        [
            {"part": "brick-1x1", "color": "red", "quantity": 3},
            {"part": "brick-1x1", "color": "blue", "quantity": 1},
            {"part": "brick-1x1", "color": "red", "quantity": 1},
        ]
    )

    used, missing = cek_inventori(needed, inventory)

    assert [line.wire() for line in used] == [{"part": "brick-1x1", "color": "red", "quantity": 2}]
    assert [line.wire() for line in missing] == [
        {"part": "brick-1x1", "color": "red", "quantity": 2},
        {"part": "brick-1x1", "color": "blue", "quantity": 1},
    ]
    assert merge(used + missing) == merge(needed)


def test_the_tool_an_llm_calls_answers_like_cek_inventori():
    inventory = lines([{"part": "tile-1x2", "color": "white", "quantity": 1}])

    answer = run_cek_inventori_tool(
        {"parts": [{"part": "tile-1x2", "color": "white", "quantity": 2}]}, inventory
    )

    assert answer == {
        "used": [{"part": "tile-1x2", "color": "white", "quantity": 1}],
        "missing": [{"part": "tile-1x2", "color": "white", "quantity": 1}],
    }
    part = CEK_INVENTORI_TOOL["input_schema"]["properties"]["parts"]["items"]["properties"]["part"]
    assert len(part["enum"]) == 13


# The templates


def test_the_templates_are_legoin_webs_eighteen():
    generator = TemplateIdeaGenerator()

    assert len(generator.templates) == 18
    for theme in ("vehicles", "animals", "buildings"):
        assert sum(t.theme == theme for t in generator.templates) == 6
    for template in generator.templates:
        assert len(template.steps["en"]) == len(template.steps["id"]) >= 1
        assert template.title["en"] and template.title["id"]
    assert generator.templates[0].title == {"en": "Little race car", "id": "Mobil balap kecil"}


# POST /scans/{scanId}/ideas


def test_three_ideas_in_the_theme_and_language_asked_for(client, corrected, example):
    response = client.post(
        "/v1/scans/scn_4kd2p9/ideas", headers=HEADERS, json=example("ideas-request")
    )

    assert response.status_code == 200
    ideas = response.json()
    assert (ideas["theme"], ideas["locale"]) == ("vehicles", "id")
    assert len(ideas["items"]) == 3
    inventory = {(d.part, d.color) for d in corrected.detections}
    for idea in ideas["items"]:
        template = TITLES["id"][idea["title"]]
        assert idea["id"].startswith("idea_")
        assert idea["theme"] == "vehicles"
        assert idea["steps"] == template["steps"]["id"]
        assert merge(lines(idea["used"] + idea["missing"])) == merge(lines(template["requires"]))
        assert all((line["part"], line["color"]) in inventory for line in idea["used"])
    # The race car uses three of the four pieces, more than any other vehicle.
    assert ideas["items"][0]["title"] == "Mobil balap kecil"


def test_the_set_is_kept_on_the_scan(client, corrected):
    ideas = ask(client).json()

    scan = client.get("/v1/scans/scn_4kd2p9", headers=HEADERS).json()

    assert scan["ideas"] == ideas
    assert scan["lastTheme"] == "vehicles"


def test_surprise_gives_one_idea_from_each_theme(client, corrected):
    ideas = ask(client, theme="surprise").json()

    assert ideas["theme"] == "surprise"
    assert [idea["theme"] for idea in ideas["items"]] == ["vehicles", "animals", "buildings"]


def test_english_and_indonesian_give_the_same_builds_in_each_language(client, corrected):
    english = ask(client, locale="en").json()["items"]
    indonesian = ask(client, locale="id", exclude=[i["id"] for i in english]).json()["items"]

    assert all(idea["title"] in TITLES["en"] for idea in english)
    assert all(idea["title"] in TITLES["id"] for idea in indonesian)


def test_try_other_ideas_avoids_the_excluded_ones(client, corrected):
    first = ask(client).json()["items"]
    second = ask(client, exclude=[i["id"] for i in first]).json()["items"]

    titles = [i["title"] for i in first + second]
    assert len(set(titles)) == 6
    assert set(titles) == {t["title"]["en"] for t in TEMPLATES if t["theme"] == "vehicles"}


def test_once_every_template_is_excluded_the_oldest_come_back(client, corrected):
    first = ask(client).json()["items"]
    second = ask(client, exclude=[i["id"] for i in first]).json()["items"]
    third = ask(client, exclude=[i["id"] for i in first + second]).json()["items"]

    assert [i["title"] for i in third] == [i["title"] for i in first]
    ids = [i["id"] for i in first + second + third]
    assert len(set(ids)) == 9


def test_asking_again_gives_different_ideas(client, corrected):
    first = {i["title"] for i in ask(client).json()["items"]}
    second = {i["title"] for i in ask(client).json()["items"]}

    assert not first & second


def test_unknown_excluded_ids_are_ignored(client, corrected):
    response = ask(client, exclude=["idea_m2x9", "idea_p0d4"])

    assert response.status_code == 200


def test_a_scan_with_no_pieces_gets_409_no_pieces(client, seed, example):
    scan = example("scan-job-done")["scan"] | {"detections": []}
    seed(scan)

    response = ask(client)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "no_pieces"


def test_ideas_for_an_unknown_scan_are_not_found(client, corrected):
    assert ask(client, scan="scn_nope").status_code == 404
    other = {"Legoin-Device-Id": "someone-else"}
    body = {"theme": "vehicles", "locale": "en"}
    assert client.post("/v1/scans/scn_4kd2p9/ideas", headers=other, json=body).status_code == 404


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"theme": "robots", "locale": "en"}, "theme"),
        ({"theme": "animals", "locale": "fr"}, "locale"),
        ({"theme": "animals"}, "locale"),
        ({"theme": "animals", "locale": "en", "exclude": ["idea_a"] * 51}, "exclude"),
    ],
)
def test_an_invalid_request_names_its_field(client, corrected, body, field):
    response = client.post("/v1/scans/scn_4kd2p9/ideas", headers=HEADERS, json=body)

    assert response.status_code == 400
    assert response.json()["error"]["field"] == field


# GET /scans/{scanId}/ideas/{ideaId}


def test_an_idea_reads_back_with_its_set(client, corrected):
    ideas = ask(client, locale="id").json()
    idea = ideas["items"][1]

    response = client.get(f"/v1/scans/scn_4kd2p9/ideas/{idea['id']}", headers=HEADERS)

    assert response.status_code == 200
    assert response.json() == {"idea": idea, "set": ideas}


def test_a_replaced_idea_is_not_found(client, corrected):
    old = ask(client).json()["items"][0]["id"]
    ask(client)

    assert client.get(f"/v1/scans/scn_4kd2p9/ideas/{old}", headers=HEADERS).status_code == 404


def test_a_correction_that_changes_the_inventory_drops_the_ideas(client, corrected):
    idea = ask(client).json()["items"][0]["id"]

    client.patch(
        "/v1/scans/scn_4kd2p9/detections",
        headers=HEADERS,
        json={"changes": [{"type": "remove", "id": "det_a1"}]},
    )

    assert client.get(f"/v1/scans/scn_4kd2p9/ideas/{idea}", headers=HEADERS).status_code == 404
    assert client.get("/v1/scans/scn_4kd2p9", headers=HEADERS).json()["lastTheme"] == "vehicles"


def test_confirming_a_piece_keeps_the_idea(client, corrected):
    idea = ask(client).json()["items"][0]["id"]

    client.patch(
        "/v1/scans/scn_4kd2p9/detections",
        headers=HEADERS,
        json={"changes": [{"type": "edit", "id": "det_a1"}]},
    )

    assert client.get(f"/v1/scans/scn_4kd2p9/ideas/{idea}", headers=HEADERS).status_code == 200


def test_deleting_the_scan_deletes_its_ideas(client, corrected):
    idea = ask(client).json()["items"][0]["id"]

    client.delete("/v1/scans/scn_4kd2p9", headers=HEADERS)

    assert client.get(f"/v1/scans/scn_4kd2p9/ideas/{idea}", headers=HEADERS).status_code == 404
    assert client.app.state.store.written_ideas(DEVICE, "scn_4kd2p9") == []


# Generators that misbehave


class Scripted:
    """Answers each call with the next list of proposals, after an optional wait."""

    def __init__(self, *answers, wait: float = 0, error: Exception | None = None):
        self.answers = list(answers)
        self.calls = []
        self.wait = wait
        self.error = error

    async def propose(self, request):
        self.calls.append(request)
        await asyncio.sleep(self.wait)
        if self.error:
            raise self.error
        return self.answers.pop(0)


def proposal(key="k", theme="vehicles", requires=None) -> ProposedIdea:
    return ProposedIdea(
        key=key,
        theme=theme,
        difficulty="easy",
        title=f"Build {key}",
        summary="A build.",
        steps=["Do it."],
        requires=requires or lines([{"part": "brick-2x4", "color": "red", "quantity": 2}]),
    )


@pytest.fixture
def with_generator(settings, example):
    """A client whose ideas come from `generator`, with the corrected scan in it."""
    from legoin_api.schemas import Detection
    from legoin_api.store import StoredScan

    clients = []

    def make(generator, **changes):
        app = create_app(settings.model_copy(update=changes), idea_generator=generator)
        client = TestClient(app).__enter__()
        clients.append(client)
        scan = example("patch-detections-response")
        app.state.store.insert_scan(
            StoredScan(
                scan["id"],
                DEVICE,
                scan["createdAt"],
                app.state.photos.new_name(),
                1200,
                800,
                [Detection.model_validate(d) for d in scan["detections"]],
                last_theme="animals",
            )
        )
        return client

    yield make
    for client in clients:
        client.__exit__(None, None, None)


def test_the_backend_writes_used_and_missing_itself(with_generator):
    client = with_generator(Scripted([proposal("a"), proposal("b"), proposal("c")]))

    idea = ask(client).json()["items"][0]

    assert idea["used"] == [{"part": "brick-2x4", "color": "red", "quantity": 1}]
    assert idea["missing"] == [{"part": "brick-2x4", "color": "red", "quantity": 1}]


def test_a_build_outside_the_catalogue_is_replaced(with_generator):
    outside = PartCount.model_construct(part="brick-9x9", color="red", quantity=1)
    generator = Scripted(
        [proposal("a"), proposal("odd", requires=[outside]), proposal("c", theme="animals")],
        [proposal("b"), proposal("d")],
    )
    client = with_generator(generator)

    ideas = ask(client).json()["items"]

    assert [i["title"] for i in ideas] == ["Build a", "Build b", "Build d"]
    assert generator.calls[1].themes == ["vehicles", "vehicles"]


def test_a_slow_generator_times_out_and_changes_nothing(with_generator):
    client = with_generator(Scripted([], wait=1), ideas_timeout_s=0.05)

    response = ask(client)

    assert response.status_code == 504
    assert response.json()["error"]["code"] == "timeout"
    scan = client.get("/v1/scans/scn_4kd2p9", headers=HEADERS).json()
    assert scan["lastTheme"] == "animals"
    assert "ideas" not in scan


def test_an_unavailable_provider_is_503(with_generator):
    client = with_generator(Scripted(error=IdeasUnavailable()))

    response = ask(client)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "unavailable"
    assert response.headers["retry-after"] == "10"
