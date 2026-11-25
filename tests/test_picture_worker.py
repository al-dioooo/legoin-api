import sys
import threading
import time
from pathlib import Path

import anthropic
import httpx2 as httpx
import pytest
from picture_fakes import PIECES, FakeChecks, FakeClaude, FakeRenders, happy_script, jpeg, text

from legoin_api.picture import worker
from legoin_api.picture.agent import Outcome
from legoin_api.picture.grid import Piece
from legoin_api.picture.runner import ProcessRunner

FAKE_WORKER = (sys.executable, str(Path(__file__).parent / "fake_picture_worker.py"))


@pytest.fixture
def spec(tmp_path):
    (tmp_path / "out").mkdir()
    (tmp_path / "photo").write_bytes(jpeg())
    return {
        "jobId": "bjob_test",
        "photo": str(tmp_path / "photo"),
        "out": str(tmp_path / "out"),
        "locale": "en",
        "pieces": [line.wire() for line in PIECES],
        "builds": [
            {"theme": "vehicles", "kind": "a car", "avoid": []},
            {"theme": "vehicles", "kind": "a plane", "avoid": ["Jet"]},
        ],
        "model": "claude-opus-5-5",
        "effort": "medium",
        "maxCalls": 24,
        "maxCost": 0.5,
        "maxSeconds": 300,
        "maxPieces": 40,
        "maxTokens": 4000,
        "staggerS": 0,
    }


class PerBuild:
    """Claude for each build of a set, told apart by what the request asks to build."""

    def __init__(self, **scripts):
        self.scripts = scripts
        self.started: dict[str, float] = {}

    def __call__(self, **kwargs):
        brief = kwargs["messages"][0]["content"][2]["text"]
        for kind, claude in self.scripts.items():
            if f"Build a {kind}," in brief:
                self.started.setdefault(kind, time.monotonic())
                return claude(**kwargs)
        raise AssertionError(f"no script for {brief!r}")


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


def of(events, index):
    return [event for event in events if event.get("build") == index]


def test_each_build_reports_its_calls_and_ends_on_its_own(spec):
    car = FakeClaude(happy_script())
    plane = FakeClaude([([text("")], "refusal")])

    events, renders = run(spec, PerBuild(car=car, plane=plane))

    assert [e["type"] for e in of(events, 0)] == ["calling", "called", "calling", "called", "built"]
    assert [e["type"] for e in of(events, 1)] == ["calling", "called", "failed"]
    called = of(events, 0)[1]
    assert called["model"] == "claude-opus-5-5"
    assert called["usage"] == {"input": 10, "output": 500, "cacheRead": 4000, "cacheWrite": 1000}
    result = of(events, 0)[-1]["result"]
    assert result["title"] == "Little race car" and not result["plain"]
    assert result["difficulty"] == "easy"
    assert {line["part"] for line in result["parts"]} >= {"brick-2x4", "round-1x1"}
    assert len(result["pieces"]) == 6
    assert result["calls"] == 2 and result["cost"] > 0
    out = Path(spec["out"]) / "0"
    assert (out / "preview.webp").read_bytes() == b"RIFF-fake-webp"
    assert (out / "model.ldr").read_text() == "0 Little race car\n"
    assert renders.previews == 1
    assert of(events, 1)[-1] == {
        "type": "failed",
        "build": 1,
        "code": "refused",
        "message": "Claude declined the build.",
    }
    # Each build is asked for its own kind of thing, with the builds to avoid.
    assert "Jet" in plane.calls[0]["messages"][0]["content"][2]["text"]


def test_the_builds_after_the_first_wait_their_stagger(spec):
    claude = PerBuild(car=FakeClaude(happy_script()), plane=FakeClaude(happy_script()))

    run({**spec, "staggerS": 0.3}, claude)

    assert claude.started["plane"] - claude.started["car"] >= 0.25


@pytest.mark.parametrize(
    ("status", "code"), [(529, "unavailable"), (500, "unavailable"), (400, "internal")]
)
def test_errors_from_the_api_are_unavailable_or_internal(spec, status, code):
    def failing(**kwargs):
        request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        response = httpx.Response(status, request=request, json={"error": {"message": "no"}})
        raise anthropic.APIStatusError("no", response=response, body=None)

    events, _ = run(spec, failing)

    assert [of(events, i)[-1]["code"] for i in (0, 1)] == [code, code]


def test_a_build_that_fails_the_last_check_is_never_saved(spec, monkeypatch):
    # An agent that ends `done` with a build BrickLayer then finds a problem in.
    pieces = (Piece("p1", "brick-2x4", "red", 0, 0, 5, 0),)
    outcome = Outcome(status="done", code="finished", pieces=pieces, title="T", steps=["a", "b"])
    monkeypatch.setattr(worker.PictureAgent, "run_scan", lambda self, *args: outcome)

    events, renders = run(spec, FakeClaude([]), FakeChecks(bad={"p1"}))

    assert [of(events, i)[-1]["code"] for i in (0, 1)] == ["internal", "internal"]
    assert renders.previews == 0
    assert not list(Path(spec["out"]).rglob("preview.webp"))


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
        {"type": "calling", "build": 0, "worst": 0.1},
        {"type": "failed", "build": 0, "code": "refused", "message": "Declined."},
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
        "message": "The build worker used too much memory.",
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

    assert events == [{"type": "calling", "build": 0, "worst": 0.1}]


def test_the_worker_gets_few_threads_and_the_environment(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    env = ProcessRunner(360, 1024).environment()

    assert env["LP_NUM_THREADS"] == env["OMP_NUM_THREADS"] == "2"
    assert env["ANTHROPIC_API_KEY"] == "sk-test"


def test_the_real_worker_module_reports_a_bad_spec_as_internal(spec, tmp_path):
    """The real `python -m legoin_api.picture.worker`: a photo that isn't there fails the
    whole set cleanly, as one event without a build, without calling Claude."""
    events = []
    runner = ProcessRunner(60, 2048)
    runner.run({**spec, "photo": str(tmp_path / "missing.jpg")}, events.append, threading.Event())

    assert [(event["type"], event["code"], "build" in event) for event in events] == [
        ("failed", "internal", False)
    ]
