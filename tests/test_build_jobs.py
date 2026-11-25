"""The queue of build sets (ADR-0013), with a fake worker and a sink that records what it hears."""

import threading
import time
from pathlib import Path

import pytest
from picture_fakes import CAR, PIECES, jpeg

from legoin_api.picture.files import PictureFiles
from legoin_api.picture.jobs import BuildJobs, Limits, Slot
from legoin_api.picture.store import PictureStore, Spend

LIMITS = Limits(
    model="claude-opus-5-5",
    effort="medium",
    daily_budget=3.0,
    daily_sets=2,
    build_cost=0.35,
    build_seconds=150,
    build_calls=12,
    max_pieces=40,
    queue_size=3,
    stagger_s=5.0,
)


def result(title="Little race car"):
    return {
        "title": title,
        "summary": "A red car.",
        "steps": ["One.", "Two."],
        "parts": [{"part": "brick-2x4", "color": "red", "quantity": 1}],
        "pieces": [{"id": f"p{i + 1}", **piece} for i, piece in enumerate(CAR)],
        "difficulty": "easy",
        "plain": False,
    }


def builds(spec, emit, stop, cost=0.1, fail=()):
    """Each build spends `cost` in one call, then writes its files and is built, or fails if
    its index is in `fail`."""
    for index, _ in enumerate(spec["builds"]):
        emit({"type": "calling", "build": index, "worst": 0.2})
        emit({"type": "called", "build": index, "model": "claude-opus-5-5", "cost": cost,
              "usage": {"output": 3000}})  # fmt: skip
        if index in fail:
            emit({"type": "failed", "build": index, "code": "no_valid_build", "message": "No."})
            continue
        out = Path(spec["out"]) / str(index)
        out.mkdir(parents=True)
        (out / "preview.webp").write_bytes(b"webp")
        (out / "model.ldr").write_text("0 build\n")
        emit({"type": "built", "build": index, "result": result(f"Build {index}")})


class Runner:
    """Runs each set with a function; `hold` keeps sets running until released."""

    def __init__(self, job=builds, hold=False):
        self.job = job
        self.release = threading.Event()
        if not hold:
            self.release.set()
        self.specs: list[dict] = []
        self.stopped: list[str] = []

    def run(self, spec, emit, stop):
        self.specs.append(spec)
        while not self.release.wait(0.01):
            if stop.is_set():
                self.stopped.append(spec["jobId"])
                return
        if stop.is_set():
            self.stopped.append(spec["jobId"])
            return
        self.job(spec, emit, stop)


class Sink:
    def __init__(self, applies=True):
        self.applies = applies
        self.built: list[tuple[str, str, dict]] = []
        self.failed: list[str] = []
        self.done = threading.Event()

    def built_(self, job, slot, build, result):
        self.built.append((slot.idea_id, build.id, result))
        return self.applies

    def failed_(self, job, slot):
        self.failed.append(slot.idea_id)


@pytest.fixture
def make(tmp_path):
    made = []

    def make(runner=None, limits=LIMITS, today="2026-11-25", applies=True):
        store = PictureStore(tmp_path / "picture.db")
        files = PictureFiles(tmp_path / "files")
        jobs = BuildJobs(store, files, tmp_path / "scratch", limits, runner, today=lambda: today)
        sink = Sink(applies)
        jobs.sink = type("S", (), {"built": sink.built_, "failed": sink.failed_})()
        jobs.recorded = sink
        made.append(jobs)
        return jobs

    yield make
    for jobs in made:
        jobs.shutdown()


@pytest.fixture
def photo(tmp_path):
    path = tmp_path / "photo.jpg"
    path.write_bytes(jpeg())
    return path


def slots(*ids):
    return [Slot(idea_id, "vehicles", f"kind {idea_id}", ["Seen"]) for idea_id in ids]


def submit(jobs, photo, ids=("idea_a", "idea_b"), account="acc_a", scan="scn_1"):
    return jobs.submit(account, scan, "en", photo, PIECES, slots(*ids))


def until(check, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.01)
    raise AssertionError("never happened")


def ended(jobs):
    return all(job.status == "ended" for job in jobs._jobs.values())


def test_each_build_goes_to_its_idea_with_its_files_and_spending(make, photo):
    jobs = make(Runner())

    assert submit(jobs, photo) == "pending"
    until(lambda: len(jobs.recorded.built) == 2 and ended(jobs))

    (idea_a, build_a, result_a), (idea_b, _, _) = jobs.recorded.built
    assert (idea_a, idea_b) == ("idea_a", "idea_b")
    assert result_a["title"] == "Build 0"
    stored = jobs.store.get_idea_build(build_a)
    assert stored.scan_id == "scn_1" and len(stored.pieces) == 6
    assert sorted(p.name.rsplit(".")[1] for p in jobs.files.directory.iterdir()) == [
        "ldr", "ldr", "webp", "webp",
    ]  # fmt: skip
    assert jobs.store.spent("2026-11-25") == pytest.approx(0.2)
    assert not list((jobs.scratch).iterdir())


def test_the_spec_carries_the_photo_pieces_builds_and_limits(make, photo):
    runner = Runner()
    jobs = make(runner)

    submit(jobs, photo)
    until(lambda: runner.specs)

    spec = runner.specs[0]
    assert spec["builds"] == [
        {"theme": "vehicles", "kind": "kind idea_a", "avoid": ["Seen"]},
        {"theme": "vehicles", "kind": "kind idea_b", "avoid": ["Seen"]},
    ]
    assert spec["pieces"][0] == {"part": "plate-2x4", "color": "black", "quantity": 1}
    assert Path(spec["photo"]).name == "photo"
    assert (spec["model"], spec["effort"], spec["locale"]) == ("claude-opus-5-5", "medium", "en")
    assert (spec["maxCost"], spec["maxCalls"], spec["maxSeconds"]) == (0.35, 12, 150)
    assert (spec["maxPieces"], spec["maxTokens"], spec["staggerS"]) == (40, 4000, 5.0)
    # The builds run together: one build's time, the stagger, and the grace.
    assert spec["deadlineS"] == 150 + 5 + 60


def test_a_failed_build_goes_to_its_idea_as_failed(make, photo):
    jobs = make(Runner(lambda spec, emit, stop: builds(spec, emit, stop, fail={1})))

    submit(jobs, photo)
    until(lambda: ended(jobs) and jobs.recorded.failed)

    assert [idea for idea, _, _ in jobs.recorded.built] == ["idea_a"]
    assert jobs.recorded.failed == ["idea_b"]


def test_a_worker_that_stops_early_fails_the_builds_it_didnt_end(make, photo):
    def crash(spec, emit, stop):
        emit({"type": "calling", "build": 0, "worst": 0.2})

    jobs = make(Runner(crash))

    submit(jobs, photo)
    until(lambda: ended(jobs) and len(jobs.recorded.failed) == 2)

    # The call it was making when it stopped is charged at its worst.
    assert jobs.store.spent("2026-11-25") == pytest.approx(0.2)


def test_a_set_wide_failure_fails_every_build_still_running(make, photo):
    def timeout(spec, emit, stop):
        builds({**spec, "builds": spec["builds"][:1]}, emit, stop)
        emit({"type": "failed", "code": "timeout", "message": "Too long."})

    jobs = make(Runner(timeout))

    submit(jobs, photo)
    until(lambda: ended(jobs))

    assert [idea for idea, _, _ in jobs.recorded.built] == ["idea_a"]
    assert jobs.recorded.failed == ["idea_b"]


def test_a_build_its_idea_no_longer_wants_is_deleted(make, photo):
    jobs = make(Runner(), applies=False)

    submit(jobs, photo)
    until(lambda: ended(jobs) and len(jobs.recorded.built) == 2)

    assert jobs.store.get_idea_build(jobs.recorded.built[0][1]) is None
    assert list(jobs.files.directory.iterdir()) == []


@pytest.mark.parametrize(
    ("limits", "refusal"),
    [
        ({"daily_sets": 0}, "limit"),
        # Two builds reserve $0.70; $2.50 is spent already.
        ({}, "budget"),
    ],
)
def test_a_set_is_refused_past_the_limit_or_the_budget(make, photo, limits, refusal):
    from dataclasses import replace

    jobs = make(Runner(), replace(LIMITS, **limits))
    if refusal == "budget":
        jobs.store.insert_job("bjob_old", "acc_b", "2026-11-25", "t")
        jobs.store.record_spend("bjob_old", "2026-11-25", "t", Spend(model="m", cost=2.5))

    assert jobs.refusal("acc_a", 2) == refusal
    assert submit(jobs, photo) == refusal
    assert jobs.recorded.built == jobs.recorded.failed == []


def test_without_a_runner_builds_are_off(make, photo):
    jobs = make(None)

    assert not jobs.enabled
    assert submit(jobs, photo) == "off"


def test_an_account_has_a_daily_number_of_sets(make, photo):
    jobs = make(Runner())

    assert submit(jobs, photo, account="acc_a") == "pending"
    assert submit(jobs, photo, ["idea_c"], account="acc_a") == "pending"
    assert submit(jobs, photo, ["idea_d"], account="acc_a") == "limit"
    assert jobs.refusal("acc_b", 1) is None
    # The next day starts over.
    assert make(Runner(), today="2026-11-26").refusal("acc_a", 1) is None


def test_queued_and_running_sets_reserve_their_whole_caps(make, photo):
    from dataclasses import replace

    jobs = make(Runner(hold=True), replace(LIMITS, daily_budget=1.5, daily_sets=9))

    assert submit(jobs, photo, ["a", "b"], scan="scn_1") == "pending"  # $0.70 reserved
    assert submit(jobs, photo, ["c", "d"], scan="scn_2") == "pending"  # $1.40
    assert submit(jobs, photo, ["e"], scan="scn_3") == "budget"


def test_too_many_sets_waiting_is_busy(make, photo):
    from dataclasses import replace

    jobs = make(Runner(hold=True), replace(LIMITS, queue_size=2, daily_sets=9))

    assert submit(jobs, photo, ["a"], scan="scn_1") == "pending"
    assert submit(jobs, photo, ["b"], scan="scn_2") == "pending"
    assert submit(jobs, photo, ["c"], scan="scn_3") == "busy"


def test_cancelling_a_queued_set_gives_it_back(make, photo):
    from dataclasses import replace

    runner = Runner(hold=True)
    jobs = make(runner, replace(LIMITS, daily_sets=2))
    submit(jobs, photo, ["a"], scan="scn_1")
    until(lambda: runner.specs)
    submit(jobs, photo, ["b"], scan="scn_2")

    jobs.cancel("acc_a", "scn_2")

    assert jobs.store.jobs_counted("acc_a", "2026-11-25") == 1
    assert len(list(jobs.scratch.iterdir())) == 1
    runner.release.set()
    until(lambda: ended(jobs))
    assert len(runner.specs) == 1


def test_cancelling_a_running_set_stops_it_and_tells_nobody(make, photo):
    runner = Runner(hold=True)
    jobs = make(runner)
    submit(jobs, photo)
    until(lambda: runner.specs)

    jobs.cancel("acc_a", "scn_1")

    until(lambda: runner.stopped == [runner.specs[0]["jobId"]])
    assert jobs.recorded.built == jobs.recorded.failed == []


def test_a_new_set_cancels_only_the_scans_other_sets(make, photo):
    from dataclasses import replace

    runner = Runner(hold=True)
    jobs = make(runner, replace(LIMITS, daily_sets=9))
    submit(jobs, photo, ["old"], scan="scn_1")
    submit(jobs, photo, ["new"], scan="scn_1")
    submit(jobs, photo, ["other"], scan="scn_2")

    jobs.cancel("acc_a", "scn_1", keep=frozenset({"new"}))
    runner.release.set()
    until(lambda: ended(jobs) and len(jobs.recorded.built) == 2)

    assert [idea for idea, _, _ in jobs.recorded.built] == ["new", "other"]


def test_forgetting_a_scans_builds_deletes_them_but_those_kept(make, photo):
    jobs = make(Runner())
    submit(jobs, photo)
    until(lambda: ended(jobs) and len(jobs.recorded.built) == 2)

    jobs.forget_builds("acc_a", "scn_1", keep=frozenset({"idea_b"}))

    assert jobs.store.get_idea_build(jobs.recorded.built[0][1]) is None
    assert jobs.store.get_idea_build(jobs.recorded.built[1][1]) is not None
    assert len(list(jobs.files.directory.iterdir())) == 2


def test_leftover_scratch_folders_are_cleared_on_start(make, tmp_path):
    (tmp_path / "scratch" / "bjob_old").mkdir(parents=True)

    make(None)

    assert list((tmp_path / "scratch").iterdir()) == []
