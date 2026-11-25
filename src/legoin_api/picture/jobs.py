"""Picture jobs: a bounded queue in the API process, one job at a time, each in a worker process
of its own (ADR-0012).

A job moves through `queued`, `designing`, `checking` and `rendering`, then ends `done` with a
saved build or `failed` with a code, and never changes after that. The rules:

- **Admission.** A job is accepted only if picture builds are on, the account is under its daily
  limit, the daily budget can take the job's whole cost cap on top of what's been spent and
  what running jobs may still spend, and the queue has room. Otherwise `submit` raises the
  contract's error.
- **One at a time.** A single dispatcher thread runs jobs in order, through a `Runner`: in
  production a worker process (`runner.ProcessRunner`), which is killed on cancel, past the
  deadline, or past its memory limit.
- **Spending** is recorded as the worker reports each call. A worker stopped during a call is
  charged that call's worst case.
- **Counting.** A job counts toward its account's daily limit when accepted, and is given back
  if it ends `unavailable`, `internal` or `picture_budget`, or is cancelled while queued.
- **Files.** The picture waits in the job's scratch folder, and the worker writes its preview
  and model there. Only a `done` job moves them into `PictureFiles`; every job's folder is
  deleted when it ends.
- Ended jobs are forgotten after `keep_s`. Jobs live in memory, as scan jobs do.

**Idea previews** (ADR-0012, "Idea previews") are jobs of the second kind, `idea`, in the same
queue and worker. `submit_idea` never raises: it answers `pending`, or `unavailable` when previews
are off, the account has used its previews for the day, the budget can't take one more, or too
many are waiting. Picture builds run first; previews run in the order asked. A preview's result
goes to `idea_sink`, which applies it to the idea if the idea is still in its scan's set; one
whose set was replaced is cancelled by `cancel_ideas`.
"""

import logging
import shutil
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, Protocol

from PIL import Image

from legoin_api.errors import ApiError
from legoin_api.ids import new_id, utc_now
from legoin_api.picture.files import PictureFiles, save_jpeg
from legoin_api.picture.grid import Piece
from legoin_api.picture.schemas import PICTURE_STAGES, PictureQuota, PictureStage
from legoin_api.picture.store import PictureStore, Spend, StoredIdeaBuild, StoredPictureBuild
from legoin_api.picture.worker import MESSAGES
from legoin_api.schemas import ErrorDetail, PartCount

log = logging.getLogger(__name__)

KEEP_ENDED_JOBS_S = 600.0
QUEUE_FULL_RETRY_S = 30
DEADLINE_GRACE_S = 60.0
"""A worker may run this long past its design time, to check, render and start up."""
GIVEN_BACK = {"unavailable", "internal", "picture_budget"}
"""Codes that don't count toward the account's daily limit: the fault isn't the person's."""
SHUTDOWN_WAIT_S = 5.0


def utc_day() -> str:
    return datetime.now(UTC).date().isoformat()


def next_midnight() -> datetime:
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return today + timedelta(days=1)


def seconds_to_midnight() -> int:
    return max(1, int((next_midnight() - datetime.now(UTC)).total_seconds()) + 1)


@dataclass(frozen=True)
class Limits:
    model: str
    effort: str
    daily_budget: float
    daily_limit: int
    job_cost: float
    job_seconds: float
    job_calls: int
    queue_size: int

    @property
    def deadline_s(self) -> float:
        """From leaving the queue to the end: the design time and the grace for the rest."""
        return self.job_seconds + DEADLINE_GRACE_S


@dataclass(frozen=True)
class PreviewLimits:
    """What an idea's preview may take. Previews share `Limits.daily_budget`."""

    model: str
    effort: str
    daily_limit: int
    job_cost: float
    job_seconds: float
    job_calls: int
    queue_size: int = 9
    """Previews waiting or running; past it, a new one is `unavailable`."""
    max_tokens: int = 4000

    @property
    def deadline_s(self) -> float:
        return self.job_seconds + DEADLINE_GRACE_S


Kind = Literal["picture", "idea"]


@dataclass
class PictureJob:
    id: str
    account_id: str
    locale: str
    folder: Path
    width: int
    height: int
    status: str = "running"
    stage: PictureStage = "queued"
    build_id: str | None = None
    error: ErrorDetail | None = None
    ended_at: float | None = None
    cancelled: bool = False
    cost: float = 0.0
    in_flight: float = 0.0
    """The worst case of the call the worker is making, if any."""
    result: dict | None = None
    failure: tuple[str, str] | None = None
    stop: threading.Event = field(default_factory=threading.Event)
    kind: Kind = "picture"
    idea: dict | None = None
    """For a preview: `scanId`, `ideaId` and the idea as written (`title`, `summary`, `steps`
    and `parts`)."""


class IdeaSink(Protocol):
    """Where previews go: the ideas they were made for (`previews.IdeaPreviews`)."""

    def built(self, job: PictureJob, build: StoredIdeaBuild, result: dict) -> bool:
        """Applies a built preview to its idea. False when the idea is no longer in its scan's
        set: the preview is then deleted."""
        ...

    def failed(self, job: PictureJob) -> None:
        """No preview will come for this idea."""
        ...


class Runner(Protocol):
    def run(self, spec: dict, emit: Callable[[dict], None], stop: threading.Event) -> None:
        """Runs one job until it ends or `stop` is set, passing its events to `emit`."""
        ...


class PictureJobs:
    def __init__(
        self,
        store: PictureStore,
        files: PictureFiles,
        scratch: Path,
        limits: Limits,
        runner: Runner | None,
        *,
        pictures: bool = True,
        previews: PreviewLimits | None = None,
        keep_s: float = KEEP_ENDED_JOBS_S,
        clock: Callable[[], float] = time.monotonic,
        today: Callable[[], str] = utc_day,
    ):
        self.store = store
        self.files = files
        self.scratch = scratch
        self.limits = limits
        self.runner = runner
        """None when neither picture builds nor previews can run here."""
        self.pictures = pictures
        """Whether picture builds are accepted; the runner may run previews only."""
        self.previews = previews
        """None when idea previews are off."""
        self.idea_sink: IdeaSink | None = None
        self.keep_s = keep_s
        self.clock = clock
        self.today = today
        self._jobs: dict[str, PictureJob] = {}
        self._lock = threading.Lock()
        # Picture builds before previews; each in the order they came.
        self._waiting: dict[Kind, deque[str]] = {"picture": deque(), "idea": deque()}
        self._wake = threading.Condition(self._lock)
        self._closing = False
        # A crash or a restart leaves folders of jobs that will never end.
        shutil.rmtree(scratch, ignore_errors=True)
        scratch.mkdir(parents=True, exist_ok=True)
        self._dispatcher = threading.Thread(target=self._dispatch, name="picture", daemon=True)
        if runner is not None:
            self._dispatcher.start()

    @property
    def enabled(self) -> bool:
        """Whether picture builds are accepted."""
        return self.runner is not None and self.pictures

    @property
    def previews_on(self) -> bool:
        return self.runner is not None and self.previews is not None

    def _cap(self, job: PictureJob) -> float:
        if job.kind == "idea" and self.previews:
            return self.previews.job_cost
        return self.limits.job_cost

    def _budget_takes(self, cost: float) -> bool:
        """Whether today's spending, what running and queued jobs may still spend, and `cost`
        fit under the daily budget. Under the lock."""
        reserved = sum(
            max(0.0, self._cap(job) - job.cost)
            for job in self._jobs.values()
            if job.status == "running"
        )
        spent = self.store.spent(self.today())
        return spent + reserved + cost <= self.limits.daily_budget + 1e-9

    # Admission

    def quota(self, account_id: str) -> PictureQuota:
        with self._lock:
            used = self.store.jobs_counted(account_id, self.today())
            reason = self._refusal(used)
        return PictureQuota(
            available=reason is None,
            reason=reason,
            used=used,
            limit=self.limits.daily_limit,
            resets_at=next_midnight().isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        )

    def _refusal(self, used: int) -> str | None:
        """Why a job can't be accepted now, as the quota's `reason`. Under the lock."""
        if not self.enabled:
            return "off"
        if used >= self.limits.daily_limit:
            return "limit"
        # Every running or queued job may still spend up to its cap.
        if not self._budget_takes(self.limits.job_cost):
            return "budget"
        return None

    def submit(self, account_id: str, image: Image.Image, locale: str) -> PictureJob:
        """Accepts a job, or raises the contract's error for why not."""
        with self._lock:
            self._forget_ended()
            if self._closing or not self.enabled:
                raise ApiError("unavailable", "Picture builds are off on this server.")
            reason = self._refusal(self.store.jobs_counted(account_id, self.today()))
            later = {"Retry-After": str(seconds_to_midnight())}
            if reason == "limit":
                raise ApiError(
                    "picture_limit",
                    f"This account has started {self.limits.daily_limit} picture builds today.",
                    headers=later,
                )
            if reason == "budget":
                raise ApiError(
                    "picture_budget", "Today's budget for picture builds is spent.", headers=later
                )
            active = sum(
                job.status == "running" and job.kind == "picture" for job in self._jobs.values()
            )
            if active >= self.limits.queue_size:
                raise ApiError(
                    "unavailable",
                    "Too many picture builds are waiting. Try again soon.",
                    headers={"Retry-After": str(QUEUE_FULL_RETRY_S)},
                )
            job_id = new_id("pjob")
            job = PictureJob(job_id, account_id, locale, self.scratch / job_id, *image.size)
            (job.folder / "out").mkdir(parents=True)
            save_jpeg(image, job.folder / "picture.jpg")
            self.store.insert_job(job.id, account_id, self.today(), utc_now())
            self._jobs[job.id] = job
            self._waiting["picture"].append(job.id)
            self._wake.notify()
            return self._view(job)

    def submit_idea(
        self, account_id: str, scan_id: str, idea_id: str, idea: dict, locale: str
    ) -> Literal["pending", "unavailable"]:
        """Queues a preview of an idea. Never raises: a preview that can't be made is
        `unavailable`, and the idea stays as text."""
        with self._lock:
            self._forget_ended()
            previews = self.previews
            if self._closing or not self.previews_on or previews is None:
                return "unavailable"
            used = self.store.jobs_counted(account_id, self.today(), "idea")
            waiting = sum(
                job.status == "running" and job.kind == "idea" for job in self._jobs.values()
            )
            if (
                used >= previews.daily_limit
                or waiting >= previews.queue_size
                or not self._budget_takes(previews.job_cost)
            ):
                return "unavailable"
            job_id = new_id("pjob")
            job = PictureJob(job_id, account_id, locale, self.scratch / job_id, 0, 0)
            job.kind = "idea"
            job.idea = {"scanId": scan_id, "ideaId": idea_id, **idea}
            (job.folder / "out").mkdir(parents=True)
            self.store.insert_job(job.id, account_id, self.today(), utc_now(), "idea")
            self._jobs[job.id] = job
            self._waiting["idea"].append(job.id)
            self._wake.notify()
            return "pending"

    def cancel_ideas(
        self, account_id: str, scan_id: str, keep: frozenset[str] = frozenset()
    ) -> None:
        """Cancels the scan's previews, but those of the ideas in `keep`: their set was
        replaced, or the scan is gone. A queued one doesn't count; a running one is stopped."""
        with self._lock:
            stale = [
                job
                for job in self._jobs.values()
                if job.kind == "idea"
                and job.account_id == account_id
                and job.idea
                and job.idea["scanId"] == scan_id
                and job.idea["ideaId"] not in keep
                and job.status == "running"
            ]
            for job in stale:
                del self._jobs[job.id]
                job.cancelled = True
                job.stop.set()
            waiting = [job for job in stale if job.stage == "queued"]
        for job in waiting:
            self.store.end_job(job.id, "cancelled", None, counted=False)
            shutil.rmtree(job.folder, ignore_errors=True)

    # Reading and cancelling

    def get(self, account_id: str, job_id: str) -> PictureJob | None:
        """A copy of the job as it is now, or None if this account has no such job."""
        with self._lock:
            self._forget_ended()
            job = self._jobs.get(job_id)
            if job is None or job.account_id != account_id:
                return None
            return self._view(job)

    def cancel(self, account_id: str, job_id: str) -> None:
        """Stops the job and saves nothing; if it had finished, deletes the build it saved."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.account_id != account_id:
                return
            del self._jobs[job_id]
            job.cancelled = True
            job.stop.set()
            waiting = job.status == "running" and job.stage == "queued"
            saved = job.build_id if job.status == "done" else None
        if waiting:
            # The dispatcher skips it; nothing ran, so it doesn't count.
            self.store.end_job(job.id, "cancelled", None, counted=False)
            shutil.rmtree(job.folder, ignore_errors=True)
        if saved:
            self.delete_build(account_id, saved)

    def delete_build(self, account_id: str, build_id: str) -> None:
        build = self.store.delete_build(account_id, build_id)
        if build:
            for name in build.files:
                self.files.delete(name)

    def shutdown(self, timeout_s: float = SHUTDOWN_WAIT_S) -> None:
        """Stops the running job, drops the queue, and waits for the dispatcher."""
        with self._lock:
            self._closing = True
            for job in self._jobs.values():
                job.stop.set()
            self._wake.notify_all()
        if self._dispatcher.is_alive():
            self._dispatcher.join(timeout_s)

    # The dispatcher

    def _next(self) -> PictureJob | None:
        """The next job to run, picture builds first; None once shutting down."""
        with self._lock:
            while not self._closing:
                for kind in ("picture", "idea"):
                    while self._waiting[kind]:
                        job = self._jobs.get(self._waiting[kind].popleft())
                        if job is not None and not job.cancelled:
                            return job
                self._wake.wait()
            return None

    def _spec(self, job: PictureJob) -> dict:
        spec = {"jobId": job.id, "kind": job.kind, "out": str(job.folder / "out")}
        spec["locale"] = job.locale
        if job.kind == "idea" and self.previews:
            limits = self.previews
            return spec | {
                "idea": job.idea,
                "model": limits.model,
                "effort": limits.effort,
                "maxCalls": limits.job_calls,
                "maxCost": limits.job_cost,
                "maxSeconds": limits.job_seconds,
                "maxTokens": limits.max_tokens,
                "deadlineS": limits.deadline_s,
            }
        return spec | {
            "picture": str(job.folder / "picture.jpg"),
            "model": self.limits.model,
            "effort": self.limits.effort,
            "maxCalls": self.limits.job_calls,
            "maxCost": self.limits.job_cost,
            "maxSeconds": self.limits.job_seconds,
            "deadlineS": self.limits.deadline_s,
        }

    def _dispatch(self) -> None:
        while (job := self._next()) is not None:
            spec = self._spec(job)
            try:
                self.runner.run(spec, lambda event, job=job: self._on_event(job, event), job.stop)
            except Exception:
                log.exception("Picture job %s: the runner failed", job.id)
            try:
                self._finish(job)
            except Exception:
                log.exception("Picture job %s couldn't be saved", job.id)
                self._end(job, "internal", MESSAGES["internal"])
            finally:
                shutil.rmtree(job.folder, ignore_errors=True)

    def _on_event(self, job: PictureJob, event: dict) -> None:
        kind = event.get("type")
        if kind == "stage" and event.get("stage") in PICTURE_STAGES:
            with self._lock:
                if PICTURE_STAGES.index(event["stage"]) > PICTURE_STAGES.index(job.stage):
                    job.stage = event["stage"]
        elif kind == "calling":
            job.in_flight = float(event.get("worst", 0.0))
        elif kind == "called":
            usage = event.get("usage", {})
            spend = Spend(
                model=str(event.get("model", "")),
                input_tokens=int(usage.get("input", 0)),
                output_tokens=int(usage.get("output", 0)),
                cache_read_tokens=int(usage.get("cacheRead", 0)),
                cache_write_tokens=int(usage.get("cacheWrite", 0)),
                cost=float(event.get("cost", 0.0)),
            )
            self.store.record_spend(job.id, self.today(), utc_now(), spend)
            with self._lock:
                job.cost += spend.cost
                job.in_flight = 0.0
        elif kind == "done":
            job.result = event.get("build")
        elif kind == "failed":
            code = str(event.get("code", "internal"))
            job.failure = (code, str(event.get("message") or MESSAGES.get(code, code)))

    def _finish(self, job: PictureJob) -> None:
        if job.in_flight > 0:
            # Stopped during a call, which may still be billed: count its worst case.
            model = (
                self.previews.model if job.kind == "idea" and self.previews else self.limits.model
            )
            spend = Spend(model=model, cost=job.in_flight, estimated=True)
            self.store.record_spend(job.id, self.today(), utc_now(), spend)
            with self._lock:
                job.cost += job.in_flight
                job.in_flight = 0.0
        if job.cancelled:
            self.store.end_job(job.id, "cancelled", None, counted=True)
            return
        if job.result is not None and job.failure is None:
            if job.kind == "idea":
                self._save_idea(job, job.result)
            else:
                self._save(job, job.result)
            return
        code, message = job.failure or ("internal", "The picture worker stopped unexpectedly.")
        self._end(job, code, message)
        if job.kind == "idea" and self.idea_sink:
            self.idea_sink.failed(job)

    def _save_idea(self, job: PictureJob, result: dict) -> None:
        """Keeps a preview and hands it to its idea; if the idea's set was replaced meanwhile,
        the preview goes."""
        assert job.idea is not None
        out = job.folder / "out"
        build = StoredIdeaBuild(
            id=new_id("gen"),
            account_id=job.account_id,
            scan_id=job.idea["scanId"],
            idea_id=job.idea["ideaId"],
            created_at=utc_now(),
            preview_file=self.files.adopt(out / "preview.webp", "webp"),
            model_file=self.files.adopt(out / "model.ldr", "ldr"),
            pieces=[Piece(**piece) for piece in result["pieces"]],
        )
        self.store.insert_idea_build(build)
        shutil.rmtree(job.folder, ignore_errors=True)
        applied = not job.cancelled and self.idea_sink is not None
        if applied and self.idea_sink is not None:
            applied = self.idea_sink.built(job, build, result)
        if not applied:
            self.delete_idea_build(build.id)
        with self._lock:
            job.status, job.build_id, job.ended_at = "done", build.id, self.clock()
        self.store.end_job(job.id, "done" if applied else "cancelled", None, counted=True)
        log.info("Idea preview %s for %s: %s ($%.3f)", job.id, build.idea_id,
                 "applied" if applied else "dropped", job.cost)  # fmt: skip

    def delete_idea_build(self, build_id: str) -> None:
        build = self.store.delete_idea_build(build_id)
        if build:
            for name in build.files:
                self.files.delete(name)

    def forget_idea_builds(
        self, account_id: str, scan_id: str, keep: frozenset[str] = frozenset()
    ) -> None:
        """Deletes a scan's previews with their files, but those of the ideas in `keep`."""
        for build in self.store.delete_idea_builds(account_id, scan_id, keep):
            for name in build.files:
                self.files.delete(name)

    def _save(self, job: PictureJob, result: dict) -> None:
        out = job.folder / "out"
        names = [
            self.files.adopt(job.folder / "picture.jpg", "jpg"),
            self.files.adopt(out / "preview.webp", "webp"),
            self.files.adopt(out / "model.ldr", "ldr"),
        ]
        build = StoredPictureBuild(
            id=new_id("pic"),
            account_id=job.account_id,
            created_at=utc_now(),
            locale=job.locale,
            title=result["title"],
            summary=result["summary"],
            steps=list(result["steps"]),
            parts=[PartCount.model_validate(line) for line in result["parts"]],
            pieces=[Piece(**piece) for piece in result["pieces"]],
            picture_file=names[0],
            picture_width=job.width,
            picture_height=job.height,
            preview_file=names[1],
            model_file=names[2],
            plain=bool(result.get("plain")),
            model=str(result.get("model", self.limits.model)),
            cost=round(job.cost, 6),
        )
        self.store.insert_build(build)
        # Before the job reads as done, so a done job has left nothing behind.
        shutil.rmtree(job.folder, ignore_errors=True)
        with self._lock:
            cancelled = job.cancelled
            if not cancelled:
                job.status, job.build_id, job.ended_at = "done", build.id, self.clock()
        if cancelled:
            # Cancelled while it was being saved: a cancel always means nothing is kept.
            self.delete_build(job.account_id, build.id)
            self.store.end_job(job.id, "cancelled", None, counted=True)
            return
        self.store.end_job(job.id, "done", None, counted=True)
        log.info("Picture job %s saved build %s ($%.3f)", job.id, build.id, job.cost)

    def _end(self, job: PictureJob, code: str, message: str) -> None:
        shutil.rmtree(job.folder, ignore_errors=True)
        with self._lock:
            job.status = "failed"
            job.error = ErrorDetail(code=code, message=message)
            job.ended_at = self.clock()
        self.store.end_job(job.id, "failed", code, counted=code not in GIVEN_BACK)
        log.info("Picture job %s failed: %s", job.id, code)

    # Under the lock

    def _view(self, job: PictureJob) -> PictureJob:
        return replace(job, result=None, stop=threading.Event())

    def _forget_ended(self) -> None:
        now = self.clock()
        for job_id in [
            job.id
            for job in self._jobs.values()
            if job.ended_at is not None and now - job.ended_at > self.keep_s
        ]:
            del self._jobs[job_id]
