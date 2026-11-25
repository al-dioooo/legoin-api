import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest
from picture_fakes import CAR
from PIL import Image

from legoin_api.errors import ApiError
from legoin_api.picture.files import PictureFiles
from legoin_api.picture.jobs import Limits, PictureJobs
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
