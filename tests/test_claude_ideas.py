"""The Claude idea generator, against a fake client: no key, no network."""

import asyncio
import json
from types import SimpleNamespace

import anthropic
import httpx2
import pytest
from conftest import DEVICE
from fastapi.testclient import TestClient

from legoin_api.ideas.claude import (
    FALLBACK_BETA,
    MAX_TOOL_ROUNDS,
    AnthropicIdeaGenerator,
)
from legoin_api.ideas.generator import IdeaRequest, ShownIdea
from legoin_api.ideas.prompt import IDEAS_SCHEMA, SYSTEM_PROMPT
from legoin_api.main import create_app
from legoin_api.schemas import PartCount

HEADERS = {"Legoin-Device-Id": DEVICE}

INVENTORY = [
    PartCount(part="brick-2x4", color="red", quantity=1),
    PartCount(part="slope-2x2", color="green", quantity=1),
    PartCount(part="brick-2x2", color="black", quantity=1),
    PartCount(part="plate-1x2", color="blue", quantity=1),
]


def idea(theme="vehicles", title="Red racer", parts=None, **changes) -> dict:
    return {
        "theme": theme,
        "difficulty": "easy",
        "title": title,
        "summary": "A small red car.",
        "steps": ["Put the slope on the brick.", "Add the plate at the back."],
        "parts": parts
        or [
            {"part": "brick-2x4", "color": "red", "quantity": 1},
            {"part": "slope-2x2", "color": "green", "quantity": 1},
            {"part": "round-1x1", "color": "black", "quantity": 4},
        ],
    } | changes


def usage(**counts) -> SimpleNamespace:
    base = {
        "input_tokens": 300,
        "output_tokens": 500,
        "cache_read_input_tokens": 1500,
        "cache_creation_input_tokens": 0,
    }
    return SimpleNamespace(**(base | counts))


def message(stop_reason: str, *blocks, stop_details=None) -> SimpleNamespace:
    return SimpleNamespace(
        stop_reason=stop_reason,
        stop_details=stop_details,
        content=[SimpleNamespace(type="thinking", thinking=""), *blocks],
        model="claude-opus-5-5",
        usage=usage(),
    )


def answer(*ideas: dict) -> SimpleNamespace:
    text = json.dumps({"ideas": list(ideas)})
    return message("end_turn", SimpleNamespace(type="text", text=text))


def tool_calls(*inputs: dict, name: str = "cek_inventori") -> SimpleNamespace:
    blocks = [
        SimpleNamespace(type="tool_use", id=f"toolu_{n}", name=name, input=data)
        for n, data in enumerate(inputs)
    ]
    return message("tool_use", *blocks)


class FakeMessages:
    """Answers each `create` with the next scripted reply: a message, an error, or a wait."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        # The generator keeps adding to the same list; keep each call's view of it.
        self.calls.append(kwargs | {"messages": list(kwargs["messages"])})
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        if isinstance(reply, float):
            await asyncio.sleep(reply)
            raise AssertionError("the budget should have run out")
        return reply


class FakeClient:
    def __init__(self, *replies):
        self.beta = SimpleNamespace(messages=FakeMessages(replies))
        self.closed = False

    async def close(self):
        self.closed = True

    @property
    def calls(self) -> list[dict]:
        return self.beta.messages.calls


def generator(client, budget_s=5.0, effort="low") -> AnthropicIdeaGenerator:
    return AnthropicIdeaGenerator(client, model="claude-opus-5-5", effort=effort, budget_s=budget_s)


def request(themes=("vehicles",) * 3, locale="en", excluded=()) -> IdeaRequest:
    return IdeaRequest(
        themes=list(themes),
        locale=locale,
        inventory=INVENTORY,
        excluded=list(excluded),
        shown=list(excluded),
    )


def propose(client, req=None, **options):
    return asyncio.run(generator(client, **options).propose(req or request()))


THREE = [idea(title="Red racer"), idea(title="Green truck"), idea(title="Blue boat")]


# The request


def test_claude_writes_one_idea_per_slot_in_order():
    client = FakeClient(answer(*THREE))

    ideas = propose(client)

    assert [i.title for i in ideas] == ["Red racer", "Green truck", "Blue boat"]
    assert ideas[0].key == "claude:red-racer"
    assert [line.wire() for line in ideas[0].requires] == [
        {"part": "round-1x1", "color": "black", "quantity": 4},
        {"part": "brick-2x4", "color": "red", "quantity": 1},
        {"part": "slope-2x2", "color": "green", "quantity": 1},
    ]


def test_the_request_uses_the_settings_adr_0009_records():
    client = FakeClient(answer(*THREE))

    propose(client, effort="medium")

    call = client.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["thinking"] == {"type": "adaptive"}
    assert call["output_config"] == {
        "effort": "medium",
        "format": {"type": "json_schema", "schema": IDEAS_SCHEMA},
    }
    assert call["betas"] == [FALLBACK_BETA] == ["server-side-fallback-2026-07-01"]
    assert call["fallbacks"] == "default"
    assert call["tool_choice"] == {"type": "auto"}
    [tool] = call["tools"]
    assert (tool["name"], tool["strict"]) == ("cek_inventori", True)
    assert call["max_tokens"] >= 4000


def test_the_stable_prompt_comes_first_and_is_cached():
    english = FakeClient(answer(*THREE))
    indonesian = FakeClient(answer(idea(title="Mobil"), idea(theme="animals", title="Bebek")))
    shown = [ShownIdea("idea_1", "template:race-car", "vehicles", "Mobil balap kecil")]

    propose(english)
    propose(indonesian, request(locale="id", themes=["vehicles", "animals"], excluded=shown))

    first, second = english.calls[0], indonesian.calls[0]
    # Byte for byte the same, whatever the request, so the cache hits.
    assert first["system"] == second["system"]
    assert first["system"] == [
        {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
    ]
    assert first["tools"] == second["tools"]
    for part, names in [("brick-2x4", ["Brick 2×4"]), ("orange", ["Orange", "Oranye"])]:
        assert part in SYSTEM_PROMPT and all(name in SYSTEM_PROMPT for name in names)
    # Everything that changes comes after it, in the user message.
    text = second["messages"][0]["content"]
    assert "brick-2x4 red ×1" in text
    assert "1. vehicles\n2. animals" in text
    assert "Indonesian (id)" in text
    assert "Mobil balap kecil" in text
    assert "already seen" not in first["messages"][0]["content"]


# cek_inventori


def test_claude_checks_its_builds_with_cek_inventori_then_answers():
    client = FakeClient(
        tool_calls(
            {"parts": [{"part": "brick-2x4", "color": "red", "quantity": 2}]},
            {"parts": [{"part": "tile-1x2", "color": "white", "quantity": 0}]},
        ),
        answer(*THREE),
    )

    ideas = propose(client)

    assert len(ideas) == 3
    results = client.calls[1]["messages"][-1]["content"]
    assert results[0] == {
        "type": "tool_result",
        "tool_use_id": "toolu_0",
        "content": json.dumps(
            {
                "missing": [{"color": "red", "part": "brick-2x4", "quantity": 1}],
                "used": [{"color": "red", "part": "brick-2x4", "quantity": 1}],
            },
            sort_keys=True,
        ),
    }
    assert results[1]["is_error"] is True
    # The assistant turn goes back whole, thinking block included.
    turn = client.calls[1]["messages"][-2]
    assert turn["role"] == "assistant"
    assert [block.type for block in turn["content"]] == ["thinking", "tool_use", "tool_use"]


def test_tool_rounds_are_capped_then_claude_must_answer():
    call = {"parts": [{"part": "brick-2x4", "color": "red", "quantity": 1}]}
    client = FakeClient(*[tool_calls(call)] * MAX_TOOL_ROUNDS, answer(*THREE))

    ideas = propose(client)

    assert len(ideas) == 3
    assert len(client.calls) == MAX_TOOL_ROUNDS + 1
    assert [c["tool_choice"] for c in client.calls] == [{"type": "auto"}] * MAX_TOOL_ROUNDS + [
        {"type": "none"}
    ]


# Repairs and the templates


def test_a_build_that_breaks_the_rules_gets_one_repair():
    bad = idea(title="Odd", parts=[{"part": "brick-2x4", "color": "red", "quantity": 0}])
    client = FakeClient(
        answer(THREE[0], bad, idea(theme="animals", title="Wrong theme")),
        answer(THREE[0], THREE[1], THREE[2]),
    )

    ideas = propose(client)

    assert [i.title for i in ideas] == ["Red racer", "Green truck", "Blue boat"]
    repair = client.calls[1]["messages"][-1]["content"]
    assert "Build 2: brick-2x4 red needs a quantity from 1 to 50" in repair
    assert "Build 3: its theme must be vehicles" in repair
    assert client.calls[1]["tool_choice"] == {"type": "none"}


def test_builds_still_wrong_after_the_repair_come_from_the_templates():
    bad = idea(title="", steps=[])
    client = FakeClient(answer(THREE[0], bad), answer(THREE[0], bad))

    ideas = propose(client)

    assert len(client.calls) == 2
    assert ideas[0].title == "Red racer"
    assert all(i.key.startswith("template:") for i in ideas[1:])
    assert len(ideas) == 3 and len({i.title for i in ideas}) == 3


@pytest.mark.parametrize(
    ("reply", "reason"),
    [
        (message("refusal", stop_details=SimpleNamespace(category="cyber")), "refused (cyber)"),
        (message("max_tokens"), "answer cut off"),
        (message("end_turn", SimpleNamespace(type="text", text="Here you go!")), "ideas JSON"),
        (
            anthropic.APIConnectionError(request=httpx2.Request("POST", "https://x")),
            "APIConnectionError",
        ),
        (
            anthropic.APITimeoutError(request=httpx2.Request("POST", "https://x")),
            "APITimeoutError",
        ),
        (
            anthropic.InternalServerError(
                "Overloaded",
                response=httpx2.Response(
                    529,
                    request=httpx2.Request("POST", "https://x"),
                    headers={"request-id": "req_1"},
                ),
                body=None,
            ),
            "InternalServerError 529, request req_1",
        ),
    ],
)
def test_no_usable_answer_falls_back_to_the_templates_and_says_why(caplog, reply, reason):
    client = FakeClient(reply)

    ideas = propose(client)

    assert len(ideas) == 3
    assert all(i.key.startswith("template:") for i in ideas)
    assert "Ideas from the templates instead of Claude" in caplog.text
    assert reason in caplog.text


def test_a_slow_answer_falls_back_within_the_budget(caplog):
    client = FakeClient(10.0)

    ideas = propose(client, budget_s=0.05)

    assert all(i.key.startswith("template:") for i in ideas)
    assert "no answer in 0.05 s" in caplog.text


# Through the API


@pytest.fixture
def api(settings, example):
    """A client on the corrected scan, whose ideas come from `FakeClient`s."""
    from legoin_api.schemas import Detection
    from legoin_api.store import StoredScan

    def make(fake: FakeClient) -> TestClient:
        app = create_app(settings, idea_generator=generator(fake))
        client = TestClient(app).__enter__()
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
            )
        )
        return client

    return make


def test_the_service_still_writes_used_and_missing_itself(api):
    client = api(FakeClient(answer(*THREE)))

    response = client.post(
        "/v1/scans/scn_4kd2p9/ideas", headers=HEADERS, json={"theme": "vehicles", "locale": "en"}
    )

    assert response.status_code == 200
    first = response.json()["items"][0]
    assert first["title"] == "Red racer"
    assert first["used"] == [
        {"part": "brick-2x4", "color": "red", "quantity": 1},
        {"part": "slope-2x2", "color": "green", "quantity": 1},
    ]
    assert first["missing"] == [{"part": "round-1x1", "color": "black", "quantity": 4}]
    assert set(first) == {
        "id",
        "theme",
        "difficulty",
        "title",
        "summary",
        "steps",
        "used",
        "missing",
    }
    client.__exit__(None, None, None)


def test_shutting_down_closes_the_client(api):
    fake = FakeClient()
    client = api(fake)

    client.__exit__(None, None, None)

    assert fake.closed
