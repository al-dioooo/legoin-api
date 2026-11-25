import json
import sys
import threading
import time
from pathlib import Path

import anthropic
import httpx2 as httpx
import pytest
from picture_fakes import FakeChecks, FakeClaude, FakeRenders, happy_script, jpeg, tool

from legoin_api.picture import worker
from legoin_api.picture.agent import Outcome
from legoin_api.picture.grid import Piece
from legoin_api.picture.runner import ProcessRunner

FAKE_WORKER = (sys.executable, str(Path(__file__).parent / "fake_picture_worker.py"))


@pytest.fixture
def spec(tmp_path):
    (tmp_path / "out").mkdir()
    (tmp_path / "picture.jpg").write_bytes(jpeg())
    return {
        "jobId": "pjob_test",
        "picture": str(tmp_path / "picture.jpg"),
        "out": str(tmp_path / "out"),
        "locale": "en",
        "model": "claude-opus-5-5",
        "effort": "medium",
        "maxCalls": 24,
        "maxCost": 0.5,
        "maxSeconds": 300,
    }


def run(spec, claude, checks=None):
    events = []
    renders = FakeRenders()
    worker.run(
        spec,
        events.append,
        create=claude,
        checks=checks or FakeChecks(),
        renders=renders,
        export=lambda pieces, title: f"0 {title}\n",
    )
    return events, renders


def test_a_job_reports_its_stages_calls_and_build(spec):
    events, renders = run(spec, FakeClaude(happy_script()))

    kinds = [event["type"] if event["type"] != "stage" else event["stage"] for event in events]
    assert kinds == [
        "designing", "calling", "called", "calling", "called", "checking", "rendering", "done",
    ]  # fmt: skip
    called = events[2]
    assert called["model"] == "claude-opus-5-5"
    assert called["usage"] == {"input": 10, "output": 500, "cacheRead": 4000, "cacheWrite": 1000}
    build = events[-1]["build"]
    assert build["title"] == "Little race car" and not build["plain"]
    assert {line["part"] for line in build["parts"]} >= {"brick-2x4", "round-1x1"}
    assert len(build["pieces"]) == 6
    out = Path(spec["out"])
    assert (out / "preview.webp").read_bytes() == b"RIFF-fake-webp"
    assert (out / "model.ldr").read_text() == "0 Little race car\n"
    assert renders.previews == 1


def test_a_failed_outcome_is_reported_with_its_code(spec):
    events, _ = run(spec, FakeClaude([[tool("reject_picture", {"reason": "no_subject"})]]))

    assert events[-1] == {
        "type": "failed",
        "code": "picture_unusable",
        "message": "There's no clear subject in the picture to build.",
    }


@pytest.mark.parametrize(
    ("status", "code"), [(529, "unavailable"), (500, "unavailable"), (400, "internal")]
)
def test_errors_from_the_api_are_unavailable_or_internal(spec, status, code):
    def failing(**kwargs):
        request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        response = httpx.Response(status, request=request, json={"error": {"message": "no"}})
        raise anthropic.APIStatusError("no", response=response, body=None)

    events, _ = run(spec, failing)

    assert events[-1]["code"] == code


def test_a_build_that_fails_the_last_check_is_never_saved(spec, monkeypatch):
    # An agent that ends `done` with a build BrickLayer then finds a problem in.
    pieces = (Piece("p1", "brick-2x4", "red", 0, 0, 5, 0),)
    outcome = Outcome(status="done", code="finished", pieces=pieces, title="T", steps=["a", "b"])
    monkeypatch.setattr(worker.PictureAgent, "run", lambda self, picture, locale: outcome)

    events, renders = run(spec, FakeClaude([]), FakeChecks(bad={"p1"}))

    assert events[-1]["code"] == "internal"
    assert renders.previews == 0
    assert not (Path(spec["out"]) / "preview.webp").exists()


def process(spec, behaviour, *, deadline_s=20.0, memory_mb=1024, stop=None):
    events = []
    runner = ProcessRunner(deadline_s, memory_mb, command=FAKE_WORKER)
    runner.POLL_S = 0.05
    started = time.monotonic()
    runner.run({**spec, "behaviour": behaviour}, events.append, stop or threading.Event())
    return events, time.monotonic() - started


def test_the_runner_passes_the_workers_events_and_skips_other_lines(spec):
    events, _ = process(spec, "done")

    assert events == [
        {"type": "stage", "stage": "designing"},
        {"type": "failed", "code": "picture_unusable", "message": "Nothing to build."},
    ]


def test_the_runner_kills_a_worker_past_its_deadline(spec):
    events, took = process(spec, "hang", deadline_s=0.5)

    assert events[-1]["code"] == "timeout"
    assert took < 5


def test_the_runner_kills_a_worker_past_its_memory(spec):
    events, took = process(spec, "memory", memory_mb=100)

    assert events[-1] == {
        "type": "failed",
        "code": "internal",
        "message": "The picture worker used too much memory.",
    }
    assert took < 10


def test_the_runner_kills_a_worker_when_the_job_is_cancelled(spec):
    stop = threading.Event()
    threading.Timer(0.3, stop.set).start()

    events, took = process(spec, "hang", stop=stop)

    assert events[-1] == {"type": "calling", "worst": 0.2}
    assert took < 5


def test_the_runner_ends_when_the_worker_dies(spec):
    events, _ = process(spec, "crash")

    assert events == [{"type": "stage", "stage": "designing"}]


def test_the_worker_gets_few_threads_and_the_environment(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    env = ProcessRunner(360, 1024).environment()

    assert env["LP_NUM_THREADS"] == env["OMP_NUM_THREADS"] == "2"
    assert env["ANTHROPIC_API_KEY"] == "sk-test"


def test_the_real_worker_module_reports_a_bad_spec_as_internal(spec, tmp_path):
    """The real `python -m legoin_api.picture.worker`: a picture that isn't there fails
    cleanly, as an event, without calling Claude."""
    events = []
    runner = ProcessRunner(60, 2048)
    runner.run({**spec, "picture": str(tmp_path / "missing.jpg")}, events.append, threading.Event())

    assert events[0] == {"type": "stage", "stage": "designing"}
    assert events[-1]["type"] == "failed"
    assert events[-1]["code"] == "internal"
    assert json.dumps(events)  # every line was an event


def test_an_idea_spec_builds_the_ideas_preview(spec):
    from picture_fakes import CAR

    idea = {
        "scanId": "scn_1",
        "ideaId": "idea_a",
        "title": "Little race car",
        "summary": "Red.",
        "steps": ["One.", "Two."],
        "parts": [{"part": p["part"], "color": p["color"], "quantity": 1} for p in CAR],
    }
    claude = FakeClaude(happy_script())
    events, renders = run({**spec, "kind": "idea", "idea": idea, "maxTokens": 4000}, claude)

    assert claude.calls[0]["max_tokens"] == 4000
    assert claude.calls[0]["messages"][0]["content"][0]["type"] == "text"
    build = events[-1]["build"]
    assert events[-1]["type"] == "done"
    assert build["title"] == "Little race car"
    assert len(build["pieces"]) == 6
    assert renders.previews == 1
