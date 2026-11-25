import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest
from picture_fakes import CAR
from PIL import Image

from legoin_api.errors import ApiError
from legoin_api.picture.files import PictureFiles
from legoin_api.picture.jobs import Limits, PictureJobs, PreviewLimits
from legoin_api.picture.store import PictureStore, Spend

LIMITS = Limits(
    model="claude-opus-5-5",
    effort="medium",
    daily_budget=3.0,
    daily_limit=2,
    job_cost=0.5,
    job_seconds=300,
    job_calls=24,
    queue_size=3,
)

BUILD = {
    "title": "Little race car",
    "summary": "A red car.",
    "steps": ["One.", "Two."],
    "parts": [{"part": "brick-2x4", "color": "red", "quantity": 1}],
    "pieces": [{"id": f"p{i + 1}", **piece} for i, piece in enumerate(CAR)],
    "plain": False,
    "model": "claude-opus-5-5",
}


def done(spec, emit, stop, cost=0.14):
    """A job that designs, spends `cost` in one call, writes its files and ends done."""
    emit({"type": "stage", "stage": "designing"})
    emit({"type": "calling", "worst": 0.3})
    emit({"type": "called", "model": "claude-opus-5-5", "cost": cost, "usage": {"output": 4000}})
    emit({"type": "stage", "stage": "checking"})
    emit({"type": "stage", "stage": "rendering"})
    out = Path(spec["out"])
    (out / "preview.webp").write_bytes(b"webp")
    (out / "model.ldr").write_text("0 Little race car\n")
    emit({"type": "done", "build": BUILD})


class Runner:
    """Runs each job with a function; `hold` keeps jobs designing until released."""

    def __init__(self, job=done, hold=False):
        self.job = job
        self.release = threading.Event()
        if not hold:
            self.release.set()
        self.specs: list[dict] = []
        self.stopped: list[str] = []

    def run(self, spec, emit, stop):
        self.specs.append(spec)
        emit({"type": "stage", "stage": "designing"})
        while not self.release.wait(0.01):
            if stop.is_set():
                self.stopped.append(spec["jobId"])
                return
        self.job(spec, emit, stop)


@pytest.fixture
def make(tmp_path):
    made = []

    def make(runner=None, limits=LIMITS, today="2026-11-25", **kwargs):
        store = PictureStore(tmp_path / "picture.db")
        files = PictureFiles(tmp_path / "files")
        jobs = PictureJobs(
            store, files, tmp_path / "scratch", limits, runner, today=lambda: today, **kwargs
        )
        made.append(jobs)
        return jobs

    yield make
    for jobs in made:
        jobs.shutdown()


def picture():
    return Image.new("RGB", (900, 600), "red")


def wait_for(jobs, account, job_id, status="done", timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = jobs.get(account, job_id)
        if job and job.status == status:
            return job
        time.sleep(0.01)
    raise AssertionError(f"job {job_id} never got to {status}: {jobs.get(account, job_id)}")


def test_a_job_saves_its_build_and_files_and_records_its_spending(make):
    jobs = make(Runner())

    accepted = jobs.submit("acc_a", picture(), "en")
    job = wait_for(jobs, "acc_a", accepted.id)

    assert accepted.stage == "queued"
    build = jobs.store.get_build("acc_a", job.build_id)
    assert (build.title, build.locale, build.picture_width, build.cost) == (
        "Little race car",
        "en",
        900,
        0.14,
    )
    for name in build.files:
        assert jobs.files.path(name).is_file()
    assert jobs.store.spent("2026-11-25") == pytest.approx(0.14)
    assert jobs.store.jobs_counted("acc_a", "2026-11-25") == 1
    # Nothing is left in the job's scratch folder.
    assert list(jobs.scratch.iterdir()) == []


def test_the_spec_carries_the_limits_and_the_language(make):
    runner = Runner()
    jobs = make(runner)
    wait_for(jobs, "acc_a", jobs.submit("acc_a", picture(), "id").id)

    spec = runner.specs[0]
    assert {k: spec[k] for k in ("locale", "model", "effort", "maxCalls", "maxCost")} == {
        "locale": "id",
        "model": "claude-opus-5-5",
        "effort": "medium",
        "maxCalls": 24,
        "maxCost": 0.5,
    }


def test_stages_only_move_forward(make):
    def backwards(spec, emit, stop):
        emit({"type": "stage", "stage": "rendering"})
        emit({"type": "stage", "stage": "designing"})
        emit({"type": "failed", "code": "internal", "message": "no"})

    runner = Runner(backwards, hold=True)
    jobs = make(runner)
    job = jobs.submit("acc_a", picture(), "en")
    deadline = time.monotonic() + 5
    while jobs.get("acc_a", job.id).stage != "designing" and time.monotonic() < deadline:
        time.sleep(0.01)
    assert jobs.get("acc_a", job.id).stage == "designing"
    runner.release.set()

    assert wait_for(jobs, "acc_a", job.id, "failed").stage == "rendering"


def test_with_picture_builds_off_nothing_is_taken(make):
    jobs = make(None)

    with pytest.raises(ApiError) as refused:
        jobs.submit("acc_a", picture(), "en")

    assert refused.value.detail.code == "unavailable"
    assert jobs.quota("acc_a").reason == "off"


def test_an_account_has_a_daily_limit(make):
    jobs = make(Runner())
    for _ in range(2):
        wait_for(jobs, "acc_a", jobs.submit("acc_a", picture(), "en").id)

    with pytest.raises(ApiError) as refused:
        jobs.submit("acc_a", picture(), "en")

    assert refused.value.status == 429
    assert refused.value.detail.code == "picture_limit"
    assert int(refused.value.headers["Retry-After"]) > 0
    quota = jobs.quota("acc_a")
    assert (quota.available, quota.reason, quota.used, quota.limit) == (False, "limit", 2, 2)
    assert quota.resets_at.endswith("T00:00:00.000Z")
    # Another account, and the same account on another day, still can.
    assert jobs.quota("acc_b").available


def test_the_daily_budget_is_reserved_per_job_and_survives_restarts(make, tmp_path):
    runner = Runner(hold=True)
    jobs = make(runner, limits=replace(LIMITS, daily_budget=1.2))
    jobs.store.record_spend("pjob_old", "2026-11-25", "t", Spend("claude-opus-5-5", cost=0.3))

    # $0.30 spent and this job's $0.50 fit under $1.20.
    jobs.submit("acc_a", picture(), "en")
    # With the first job's $0.50 reserved, another $0.50 doesn't.
    with pytest.raises(ApiError) as refused:
        jobs.submit("acc_b", picture(), "en")

    assert (refused.value.status, refused.value.detail.code) == (503, "picture_budget")
    assert jobs.quota("acc_b").reason == "budget"
    runner.release.set()
    jobs.shutdown()

    # A new process reads what was spent.
    again = make(Runner(), limits=replace(LIMITS, daily_budget=0.5))
    assert again.store.spent("2026-11-25") == pytest.approx(0.44)
    assert again.quota("acc_c").reason == "budget"


def test_a_full_queue_answers_503_with_retry_after(make):
    runner = Runner(hold=True)
    jobs = make(runner, limits=replace(LIMITS, daily_limit=9))
    for _ in range(3):
        jobs.submit("acc_a", picture(), "en")

    with pytest.raises(ApiError) as refused:
        jobs.submit("acc_a", picture(), "en")

    assert refused.value.detail.code == "unavailable"
    assert refused.value.headers == {"Retry-After": "30"}
    runner.release.set()


@pytest.mark.parametrize(
    ("code", "counted"),
    [
        ("picture_unusable", 1),
        ("no_valid_build", 1),
        ("timeout", 1),
        ("unavailable", 0),
        ("internal", 0),
    ],
)
def test_a_failed_job_saves_nothing_and_is_given_back_when_not_the_persons_fault(
    make, code, counted
):
    def fails(spec, emit, stop):
        emit({"type": "failed", "code": code, "message": "It failed."})

    jobs = make(Runner(fails))
    job = wait_for(jobs, "acc_a", jobs.submit("acc_a", picture(), "en").id, "failed")

    assert job.error.code == code
    assert jobs.store.list_builds("acc_a") == []
    assert jobs.store.jobs_counted("acc_a", "2026-11-25") == counted
    assert list(jobs.files.directory.iterdir()) == []


def test_a_worker_that_stops_without_an_ending_fails_internal(make):
    jobs = make(Runner(lambda spec, emit, stop: None))

    job = wait_for(jobs, "acc_a", jobs.submit("acc_a", picture(), "en").id, "failed")

    assert job.error.code == "internal"


def test_cancelling_a_queued_job_gives_it_back(make):
    runner = Runner(hold=True)
    jobs = make(runner)
    first = jobs.submit("acc_a", picture(), "en")
    second = jobs.submit("acc_a", picture(), "en")

    jobs.cancel("acc_a", second.id)
    runner.release.set()
    wait_for(jobs, "acc_a", first.id)

    assert jobs.get("acc_a", second.id) is None
    assert len(runner.specs) == 1
    assert jobs.store.jobs_counted("acc_a", "2026-11-25") == 1


def test_cancelling_a_running_job_stops_it_and_charges_a_call_in_flight(make):
    def stuck(spec, emit, stop):
        emit({"type": "calling", "worst": 0.31})
        stop.wait(5)

    runner = Runner(stuck)
    jobs = make(runner)
    job = jobs.submit("acc_a", picture(), "en")
    deadline = time.monotonic() + 5
    while jobs.get("acc_a", job.id).stage != "designing" and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(0.05)

    jobs.cancel("acc_a", job.id)
    deadline = time.monotonic() + 5
    while jobs.store.spent("2026-11-25") == 0 and time.monotonic() < deadline:
        time.sleep(0.01)

    assert jobs.store.spent("2026-11-25") == pytest.approx(0.31)
    assert jobs.store.jobs_counted("acc_a", "2026-11-25") == 1
    assert jobs.store.list_builds("acc_a") == []


def test_cancelling_a_done_job_deletes_its_build(make):
    jobs = make(Runner())
    job = wait_for(jobs, "acc_a", jobs.submit("acc_a", picture(), "en").id)
    files = jobs.store.get_build("acc_a", job.build_id).files

    jobs.cancel("acc_a", job.id)

    assert jobs.store.get_build("acc_a", job.build_id) is None
    assert all(not jobs.files.path(name).exists() for name in files)


def test_another_accounts_job_is_hidden_and_cant_be_cancelled(make):
    runner = Runner(hold=True)
    jobs = make(runner)
    job = jobs.submit("acc_a", picture(), "en")

    jobs.cancel("acc_b", job.id)

    assert jobs.get("acc_b", job.id) is None
    assert jobs.get("acc_a", job.id).status == "running"
    runner.release.set()


def test_ended_jobs_are_forgotten_after_a_while(make):
    now = [0.0]
    jobs = make(Runner(), clock=lambda: now[0], keep_s=600)
    job = wait_for(jobs, "acc_a", jobs.submit("acc_a", picture(), "en").id)

    now[0] = 601

    assert jobs.get("acc_a", job.id) is None
    assert jobs.store.get_build("acc_a", job.build_id) is not None


def test_leftover_scratch_folders_are_cleared_on_start(make, tmp_path):
    (tmp_path / "scratch" / "pjob_old").mkdir(parents=True)

    make(Runner())

    assert list((tmp_path / "scratch").iterdir()) == []


PREVIEWS = PreviewLimits(
    model="claude-opus-5-5",
    effort="medium",
    daily_limit=3,
    job_cost=0.25,
    job_seconds=120,
    job_calls=10,
    queue_size=4,
)
IDEA = {"title": "Little race car", "summary": "Red.", "steps": ["One.", "Two."], "parts": []}


class Sink:
    """Records what reaches the ideas; `keep` says whether an idea is still in its set."""

    def __init__(self, keep=True):
        self.keep = keep
        self.built_ideas: list[tuple[str, str]] = []
        self.failed_ideas: list[str] = []

    def built(self, job, build, result):
        self.built_ideas.append((job.idea["ideaId"], build.id))
        return self.keep

    def failed(self, job):
        self.failed_ideas.append(job.idea["ideaId"])


def sink_for(jobs, keep=True):
    jobs.idea_sink = Sink(keep)
    return jobs.idea_sink


def preview_done(spec, emit, stop):
    emit({"type": "called", "model": "claude-opus-5-5", "cost": 0.04, "usage": {}})
    out = Path(spec["out"])
    (out / "preview.webp").write_bytes(b"webp")
    (out / "model.ldr").write_text("0 Little race car\n")
    emit({"type": "done", "build": BUILD})


def wait_until(check, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.01)
    raise AssertionError("never happened")


def test_a_preview_reaches_its_idea_and_counts_on_its_own(make):
    jobs = make(Runner(preview_done), previews=PREVIEWS)
    sink = sink_for(jobs)

    assert jobs.submit_idea("acc_a", "scn_1", "idea_a", IDEA, "en") == "pending"
    wait_until(lambda: sink.built_ideas)

    idea_id, build_id = sink.built_ideas[0]
    assert idea_id == "idea_a"
    build = jobs.store.get_idea_build(build_id)
    assert build.scan_id == "scn_1" and build_id.startswith("gen_")
    assert all(jobs.files.path(name).is_file() for name in build.files)
    assert jobs.store.jobs_counted("acc_a", "2026-11-25", "idea") == 1
    assert jobs.store.jobs_counted("acc_a", "2026-11-25") == 0
    assert jobs.store.spent("2026-11-25") == pytest.approx(0.04)


def test_the_preview_spec_carries_the_idea_and_its_own_limits(make):
    runner = Runner(preview_done)
    jobs = make(runner, previews=PREVIEWS)
    sink_for(jobs)
    jobs.submit_idea("acc_a", "scn_1", "idea_a", IDEA, "id")
    wait_until(lambda: runner.specs)

    spec = runner.specs[0]
    assert spec["kind"] == "idea" and "picture" not in spec
    assert spec["idea"] == {"scanId": "scn_1", "ideaId": "idea_a", **IDEA}
    assert (spec["maxCost"], spec["maxTokens"], spec["deadlineS"]) == (0.25, 4000, 180)


def test_a_preview_whose_set_was_replaced_is_dropped(make):
    jobs = make(Runner(preview_done), previews=PREVIEWS)
    sink = sink_for(jobs, keep=False)

    jobs.submit_idea("acc_a", "scn_1", "idea_a", IDEA, "en")
    wait_until(lambda: sink.built_ideas)

    assert jobs.store.get_idea_build(sink.built_ideas[0][1]) is None
    assert list(jobs.files.directory.iterdir()) == []


def test_a_failed_preview_tells_its_idea(make):
    def fails(spec, emit, stop):
        emit({"type": "failed", "code": "no_valid_build", "message": "No."})

    jobs = make(Runner(fails), previews=PREVIEWS)
    sink = sink_for(jobs)
    jobs.submit_idea("acc_a", "scn_1", "idea_a", IDEA, "en")

    wait_until(lambda: sink.failed_ideas)
    assert sink.failed_ideas == ["idea_a"]


def test_previews_are_unavailable_when_off_over_the_limit_or_the_budget(make):
    assert make(Runner()).submit_idea("acc_a", "scn_1", "i", IDEA, "en") == "unavailable"

    runner = Runner(preview_done, hold=True)
    jobs = make(runner, previews=replace(PREVIEWS, daily_limit=2))
    answers = [jobs.submit_idea("acc_a", "scn_1", f"idea_{i}", IDEA, "en") for i in range(3)]
    assert answers == ["pending", "pending", "unavailable"]
    runner.release.set()

    tight = make(Runner(hold=True), previews=PREVIEWS, limits=replace(LIMITS, daily_budget=0.6))
    answers = [tight.submit_idea("acc_b", "scn_1", f"idea_{i}", IDEA, "en") for i in range(3)]
    # Each preview reserves $0.25 of the $0.60 budget while it waits.
    assert answers == ["pending", "pending", "unavailable"]


def test_picture_builds_run_before_previews(make):
    runner = Runner(hold=True)
    jobs = make(runner, previews=PREVIEWS)
    sink_for(jobs)
    jobs.submit("acc_a", picture(), "en")  # runs first, held
    jobs.submit_idea("acc_a", "scn_1", "idea_a", IDEA, "en")
    jobs.submit_idea("acc_a", "scn_1", "idea_b", IDEA, "en")
    jobs.submit("acc_b", picture(), "en")
    wait_until(lambda: len(runner.specs) == 1)

    runner.release.set()
    wait_until(lambda: len(runner.specs) == 4)

    kinds = [(spec["kind"], spec.get("idea", {}).get("ideaId")) for spec in runner.specs]
    assert kinds == [("picture", None), ("picture", None), ("idea", "idea_a"), ("idea", "idea_b")]


def test_cancelling_a_sets_previews_stops_them_and_keeps_the_others(make):
    runner = Runner(hold=True)
    jobs = make(runner, previews=PREVIEWS)
    sink = sink_for(jobs)
    for idea_id in ("idea_a", "idea_b", "idea_c"):
        jobs.submit_idea("acc_a", "scn_1", idea_id, IDEA, "en")
    wait_until(lambda: len(runner.specs) == 1)  # idea_a is running

    jobs.cancel_ideas("acc_a", "scn_1", keep=frozenset({"idea_c"}))
    runner.release.set()
    wait_until(lambda: len(runner.specs) == 2)
    time.sleep(0.05)

    assert [spec["idea"]["ideaId"] for spec in runner.specs] == ["idea_a", "idea_c"]
    assert runner.stopped == [runner.specs[0]["jobId"]]
    wait_until(lambda: sink.built_ideas)
    assert [idea for idea, _ in sink.built_ideas] == ["idea_c"]
    # The queued one that never ran doesn't count; the running one does.
    assert jobs.store.jobs_counted("acc_a", "2026-11-25", "idea") == 2
