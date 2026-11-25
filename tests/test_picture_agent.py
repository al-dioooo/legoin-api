import base64
import io
import json

import pytest
from picture_fakes import (
    CAR,
    FINISH,
    FakeChecks,
    FakeClaude,
    FakeRenders,
    happy_script,
    jpeg,
    text,
    tool,
    usage,
)
from PIL import Image

from legoin_api.picture.agent import MAX_TOKENS, PICTURE_MAX_SIDE, Caps, PictureAgent
from legoin_api.picture.prompt import SYSTEM, TOOLS


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def agent(claude, checks=None, renders=None, caps=None, **kwargs) -> PictureAgent:
    return PictureAgent(
        claude,
        checks or FakeChecks(),
        renders or FakeRenders(),
        model="claude-opus-5-5",
        effort="medium",
        caps=caps or Caps(),
        **kwargs,
    )


def results(claude, call: int) -> list[dict]:
    """The tool results sent with a call: the last user message's blocks."""
    return claude.calls[call]["messages"][-1]["content"]


def test_a_build_claude_finishes_is_done_with_its_own_text():
    claude = FakeClaude(happy_script())

    outcome = agent(claude).run(jpeg(), "en")

    assert (outcome.status, outcome.code, outcome.written_by) == ("done", "finished", "claude")
    assert [p.part for p in outcome.pieces] == [p["part"] for p in CAR]
    assert (outcome.title, outcome.summary, outcome.steps) == (
        FINISH["title"],
        FINISH["summary"],
        FINISH["steps"],
    )
    assert outcome.calls == 2
    assert outcome.tools == {"place_pieces": 1, "validate": 1, "render": 1, "finish": 1}


def test_each_request_is_cached_strict_and_set_as_the_adr_says():
    claude = FakeClaude(happy_script())
    agent(claude).run(jpeg(), "id")
    request = claude.calls[0]

    assert request["model"] == "claude-opus-5-5"
    assert request["max_tokens"] == MAX_TOKENS
    assert request["system"] == [
        {"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}
    ]
    assert request["cache_control"] == {"type": "ephemeral"}
    assert request["tools"] == TOOLS
    assert all(t["strict"] for t in request["tools"])
    assert request["tool_choice"] == {"type": "auto"}
    assert request["thinking"] == {"type": "adaptive"}
    assert request["output_config"] == {"effort": "medium"}
    assert request["fallbacks"] == "default"
    # The person types nothing: the picture, and one line naming the language.
    picture, line = request["messages"][0]["content"]
    assert line == {
        "type": "text",
        "text": "Design a LEGO build of the main subject of this picture. Write the title, "
        "summary and steps in Indonesian (Bahasa Indonesia).",
    }
    assert picture["source"]["media_type"] == "image/jpeg"


def test_the_picture_is_sent_small_and_without_metadata():
    claude = FakeClaude(happy_script())
    agent(claude).run(jpeg((3000, 2000), exif=True), "en")

    data = claude.calls[0]["messages"][0]["content"][0]["source"]["data"]
    with Image.open(io.BytesIO(base64.b64decode(data))) as sent:
        assert max(sent.size) == PICTURE_MAX_SIDE
        assert not sent.getexif()


def test_whole_turns_go_back_and_renders_come_back_as_images():
    claude = FakeClaude(happy_script())
    agent(claude).run(jpeg(), "en")

    second = claude.calls[1]["messages"]
    assert second[1]["role"] == "assistant"
    placed, report, render = results(claude, 1)
    assert json.loads(placed["content"])["placed"][0] == {"id": "p1"}
    report = json.loads(report["content"])
    assert report["ok"] and report["pieces"] == 6 and report["turnsLeft"] == 23
    assert report["list"][0] == "p1 plate-2x4 black at (0,0,0) r90"
    assert render["content"][0]["type"] == "image"
    assert render["content"][0]["source"]["media_type"] == "image/png"


def test_finish_needs_a_render_seen_in_an_earlier_turn():
    finish_now = [
        tool("place_pieces", {"pieces": CAR}, "t1"),
        tool("render", {"view": "front"}, "t2"),
        tool("finish", FINISH, "t3"),
    ]
    claude = FakeClaude([finish_now, [tool("finish", FINISH, "t4")]])

    outcome = agent(claude).run(jpeg(), "en")

    refused = results(claude, 1)[2]
    assert refused["is_error"] is True
    assert "look at a render" in refused["content"]
    assert outcome.status == "done" and outcome.calls == 2


def test_finish_refuses_an_invalid_build_or_missing_text():
    script = [
        [tool("place_pieces", {"pieces": CAR}), tool("render", {"view": "iso"})],
        [tool("finish", {**FINISH, "steps": ["Only one step."]})],
        [tool("remove_pieces", {"ids": ["p6"]}), tool("render", {"view": "iso"})],
        [tool("finish", FINISH)],
    ]
    claude = FakeClaude(script)

    outcome = agent(claude, checks=FakeChecks(bad={"p6"})).run(jpeg(), "en")

    problems = json.loads(results(claude, 2)[0]["content"])["problems"]
    assert problems == ["1 floating: p6", "it needs 2 to 8 steps"]
    assert outcome.status == "done"
    assert [p.id for p in outcome.pieces] == ["p1", "p2", "p3", "p4", "p5"]


def test_a_decline_ends_with_picture_refused():
    claude = FakeClaude([([text("")], "refusal")])

    outcome = agent(claude).run(jpeg(), "en")

    assert (outcome.status, outcome.code) == ("failed", "picture_refused")


@pytest.mark.parametrize(
    ("reason", "code"), [("no_subject", "picture_unusable"), ("unsuitable", "picture_refused")]
)
def test_rejecting_the_picture_ends_the_job(reason, code):
    claude = FakeClaude([[tool("reject_picture", {"reason": reason})]])

    outcome = agent(claude).run(jpeg(), "en")

    assert (outcome.status, outcome.code, outcome.pieces) == ("failed", code, ())


def test_a_reply_cut_off_or_without_tools_is_told_to_go_on():
    script = [
        ([tool("place_pieces", {"pieces": CAR})], "max_tokens"),
        [text("I think the car is done.")],
        *happy_script(),
    ]
    claude = FakeClaude(script)

    outcome = agent(claude).run(jpeg(), "en")

    assert "smaller batches" in claude.calls[1]["messages"][-1]["content"]
    assert "call finish" in claude.calls[2]["messages"][-1]["content"]
    assert outcome.status == "done" and outcome.calls == 4


def test_a_cap_ends_the_job_with_the_last_valid_build_and_plain_text():
    keep_going = [tool("validate")]
    script = [[tool("place_pieces", {"pieces": CAR})], keep_going, keep_going]
    claude = FakeClaude(script)

    outcome = agent(claude, caps=Caps(max_calls=3)).run(jpeg(), "id")

    assert (outcome.status, outcome.code, outcome.written_by) == ("done", "max_calls", "plain")
    assert len(outcome.pieces) == 6
    assert outcome.title == "Rakitan dari gambarmu"
    assert outcome.steps[0] == "Pasang 1 × plate 2×4 hitam."
    assert json.loads(results(claude, 2)[0]["content"])["note"].startswith("Almost out")


def test_a_cap_with_no_valid_build_fails():
    script = [[tool("place_pieces", {"pieces": CAR})], [tool("validate")]]
    claude = FakeClaude(script)

    outcome = agent(claude, checks=FakeChecks(bad={"p1"}), caps=Caps(max_calls=2)).run(jpeg(), "en")

    assert (outcome.status, outcome.code) == ("failed", "no_valid_build")


def test_the_time_cap_is_checked_before_every_call():
    clock = Clock()

    def slow(**kwargs):
        clock.now += 200
        return claude(**kwargs)

    claude = FakeClaude([[tool("place_pieces", {"pieces": CAR})], [tool("validate")]])

    outcome = agent(slow, caps=Caps(max_seconds=300), clock=clock).run(jpeg(), "en")

    assert (outcome.code, outcome.calls) == ("max_seconds", 2)


def test_the_cost_cap_holds_even_in_the_worst_case():
    spent = [0.0]
    room = []
    claude = FakeClaude([[tool("validate")]] * 10, spend=usage(output=3_000, write=6_000))

    outcome = agent(
        claude,
        caps=Caps(max_cost=0.50),
        # Before each call, what's spent so far plus that call's worst case.
        before_call=lambda worst: room.append(spent[0] + worst),
        on_call=lambda response, cost: spent.__setitem__(0, spent[0] + cost),
    ).run(jpeg(), "en")

    assert outcome.code == "no_valid_build"
    assert len(room) == outcome.calls == 3
    assert max(room) <= 0.50
    assert outcome.cost <= 0.50


def test_every_call_is_reported_with_its_cost():
    heard = []
    claude = FakeClaude(happy_script(), spend=usage(10, 1_000, 5_000, 2_000))

    outcome = agent(claude, on_call=lambda response, cost: heard.append(cost)).run(jpeg(), "en")

    each = (10 * 4 + 1_000 * 20 + 5_000 * 0.20 + 2_000 * 5) / 1e6
    assert heard == pytest.approx([each, each])
    assert outcome.cost == pytest.approx(2 * each)
    assert outcome.tokens == {
        "input": 20,
        "output": 2_000,
        "cacheRead": 10_000,
        "cacheWrite": 4_000,
    }


def test_a_bad_tool_or_nothing_to_render_is_an_error_claude_can_read():
    script = [
        [tool("render", {"view": "all"}), tool("delete_everything")],
        *happy_script(),
    ]
    claude = FakeClaude(script)

    agent(claude).run(jpeg(), "en")

    nothing, unknown = results(claude, 1)
    assert nothing["is_error"] and unknown["is_error"]
    assert "no tool 'delete_everything'" in unknown["content"]
