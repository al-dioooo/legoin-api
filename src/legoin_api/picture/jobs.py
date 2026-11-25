"""Build jobs: a bounded queue in the API process, one set of builds at a time, each set in a
worker process of its own (ADR-0013).

A job is the set of builds one request for ideas needs: one build per slot of the set that no
library build fills, each from the scan's photo and its pieces. The worker builds them at the
same time and reports each as it ends, so each idea is ready as soon as its build is. The rules:

- **Admission.** `submit` answers `pending`, or why the set can't be built: builds are `off`,
  the account has started its sets for the day (`limit`), the daily budget can't take every
  build's whole cost cap on top of what's been spent and what running and queued sets may still
  spend (`budget`), or too many sets are waiting (`busy`). It never raises: the caller writes
  the ideas as text instead.
- **One at a time.** A single dispatcher thread runs sets in order, through a `Runner`: in
  production a worker process (`runner.ProcessRunner`), which is killed on cancel, past the
  deadline, or past its memory limit.
- **Spending** is recorded as the worker reports each call. A worker stopped during calls is
  charged their worst cases.
- **Counting.** A set counts toward its account's daily limit when accepted, and is given back
  if it's cancelled while queued.
- **Results.** Each build the worker reports goes to the `sink` at once (`scan_builds.ScanBuilds`),
  which makes it the idea it was for, if that idea's set is still the scan's. A build that
  fails, or never comes, goes to the sink as failed, which writes that idea as text. A
  cancelled set reports nothing: its ideas are gone.
- **Files.** The photo waits in the job's scratch folder, and the worker writes each build's
  preview and model there. Only a reported build moves them into `PictureFiles`; the folder is
  deleted when the job ends.
- Ended jobs are forgotten after `keep_s`. Jobs live in memory, as scan jobs do.
"""

import logging
import shutil
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from legoin_api.ids import new_id, utc_now
from legoin_api.picture.files import PictureFiles
from legoin_api.picture.grid import Piece
from legoin_api.picture.store import PictureStore, Spend, StoredIdeaBuild
from legoin_api.schemas import PartCount

log = logging.getLogger(__name__)

KEEP_ENDED_JOBS_S = 600.0
DEADLINE_GRACE_S = 60.0
"""A worker may run this long past its design time, to check, render and start up."""
SHUTDOWN_WAIT_S = 5.0

Refusal = Literal["off", "limit", "budget", "busy"]


def utc_day() -> str:
    return datetime.now(UTC).date().isoformat()


@dataclass(frozen=True)
class Limits:
    model: str
    effort: str
    daily_budget: float
    daily_sets: int
    """Sets each account may start a day."""
    build_cost: float
    """Dollars one build may spend; a set reserves this per build."""
    build_seconds: float
    build_calls: int
    max_pieces: int
    queue_size: int
    """Sets waiting or running."""
    max_tokens: int = 4000
    stagger_s: float = 0.0
    """How long each build after the first waits to start, so it can read the first one's
    cached prefix (the photo and the pieces) instead of writing its own."""

    def set_cost(self, builds: int) -> float:
        return builds * self.build_cost

    def deadline_s(self, builds: int) -> float:
        """From leaving the queue to the end: the builds run at the same time, after their
        stagger, then the grace for the rest."""
        return self.build_seconds + self.stagger_s * max(0, builds - 1) + DEADLINE_GRACE_S


@dataclass
class Slot:
    """One build of a set: the idea it becomes, and what to build."""

    idea_id: str
    theme: str
    kind: str
    avoid: list[str] = field(default_factory=list)
    state: Literal["building", "built", "failed"] = "building"
    in_flight: float = 0.0
    """The worst case of the call this build is making, if any."""


@dataclass
class BuildJob:
    id: str
    account_id: str
    scan_id: str
    locale: str
    folder: Path
    pieces: list[PartCount]
    slots: list[Slot]
    status: Literal["queued", "running", "ended"] = "queued"
    ended_at: float | None = None
    cancelled: bool = False
    cost: float = 0.0
    stop: threading.Event = field(default_factory=threading.Event)

    @property
    def idea_ids(self) -> frozenset[str]:
        return frozenset(slot.idea_id for slot in self.slots)

    @property
    def in_flight(self) -> float:
        return sum(slot.in_flight for slot in self.slots)


class BuildSink(Protocol):
    """Where builds go: the ideas they were made for (`scan_builds.ScanBuilds`)."""

    def built(self, job: BuildJob, slot: Slot, build: StoredIdeaBuild, result: dict) -> bool:
        """Makes a build its idea. False when the idea is no longer in its scan's set: the
        build is then deleted."""
        ...

    def failed(self, job: BuildJob, slot: Slot) -> None:
        """No build will come for this idea."""
        ...


class Runner(Protocol):
    def run(self, spec: dict, emit: Callable[[dict], None], stop: threading.Event) -> None:
        """Runs one job until it ends or `stop` is set, passing its events to `emit`."""
        ...


class BuildJobs:
    def __init__(
        self,
        store: PictureStore,
        files: PictureFiles,
        scratch: Path,
        limits: Limits,
        runner: Runner | None,
        *,
        keep_s: float = KEEP_ENDED_JOBS_S,
        clock: Callable[[], float] = time.monotonic,
        today: Callable[[], str] = utc_day,
    ):
        self.store = store
        self.files = files
        self.scratch = scratch
        self.limits = limits
        self.runner = runner
        """None when builds can't run here."""
        self.sink: BuildSink | None = None
        self.keep_s = keep_s
        self.clock = clock
        self.today = today
        self._jobs: dict[str, BuildJob] = {}
        self._lock = threading.Lock()
        self._waiting: deque[str] = deque()
        self._wake = threading.Condition(self._lock)
        self._closing = False
        # A crash or a restart leaves folders of jobs that will never end.
        shutil.rmtree(scratch, ignore_errors=True)
        scratch.mkdir(parents=True, exist_ok=True)
        self._dispatcher = threading.Thread(target=self._dispatch, name="builds", daemon=True)
        if runner is not None:
            self._dispatcher.start()

    @property
    def enabled(self) -> bool:
        return self.runner is not None

    # Admission

    def refusal(self, account_id: str, builds: int) -> Refusal | None:
        """Why a set of `builds` can't be accepted now, or None if it can."""
        with self._lock:
            return self._refusal(account_id, builds)

    def _refusal(self, account_id: str, builds: int) -> Refusal | None:
        if self._closing or not self.enabled:
            return "off"
        if self.store.jobs_counted(account_id, self.today()) >= self.limits.daily_sets:
            return "limit"
        if not self._budget_takes(self.limits.set_cost(builds)):
            return "budget"
        active = sum(job.status != "ended" for job in self._jobs.values())
        if active >= self.limits.queue_size:
            return "busy"
        return None

    def _budget_takes(self, cost: float) -> bool:
        """Whether today's spending, what running and queued sets may still spend, and `cost`
        fit under the daily budget. Under the lock."""
        reserved = sum(
            max(0.0, self.limits.set_cost(len(job.slots)) - job.cost)
            for job in self._jobs.values()
            if job.status != "ended"
        )
        spent = self.store.spent(self.today())
        return spent + reserved + cost <= self.limits.daily_budget + 1e-9

    def submit(
        self,
        account_id: str,
        scan_id: str,
        locale: str,
        photo: Path,
        pieces: list[PartCount],
        slots: list[Slot],
    ) -> Literal["pending"] | Refusal:
        """Queues a set of builds, or says why not. Never raises."""
        with self._lock:
            self._forget_ended()
            if refusal := self._refusal(account_id, len(slots)):
                return refusal
            job_id = new_id("bjob")
            job = BuildJob(
                job_id, account_id, scan_id, locale, self.scratch / job_id, list(pieces), slots
            )
            try:
                job.folder.mkdir(parents=True)
                shutil.copyfile(photo, job.folder / "photo")
            except OSError:
                log.exception("Couldn't queue build job %s", job_id)
                shutil.rmtree(job.folder, ignore_errors=True)
                return "busy"
            self.store.insert_job(job.id, account_id, self.today(), utc_now())
            self._jobs[job.id] = job
            self._waiting.append(job.id)
            self._wake.notify()
            log.info("Build job %s queued for scan %s: %d builds", job.id, scan_id, len(slots))
            return "pending"

    def cancel(self, account_id: str, scan_id: str, keep: frozenset[str] = frozenset()) -> None:
        """Cancels the scan's sets, but one whose ideas are in `keep`: its set was replaced, or
        the scan's ideas or the scan are gone. A queued set doesn't count; a running one is
        stopped. A cancelled set reports nothing to the sink."""
        with self._lock:
            stale = [
                job
                for job in self._jobs.values()
                if job.account_id == account_id
                and job.scan_id == scan_id
                and job.status != "ended"
                and not job.idea_ids & keep
            ]
            for job in stale:
                del self._jobs[job.id]
                job.cancelled = True
                job.stop.set()
            queued = [job for job in stale if job.status == "queued"]
        for job in queued:
            # The dispatcher skips it; nothing ran, so it doesn't count.
            self.store.end_job(job.id, "cancelled", None, counted=False)
            shutil.rmtree(job.folder, ignore_errors=True)

    def delete_build(self, build_id: str) -> None:
        build = self.store.delete_idea_build(build_id)
        if build:
            for name in build.files:
                self.files.delete(name)

    def forget_builds(
        self, account_id: str, scan_id: str, keep: frozenset[str] = frozenset()
    ) -> None:
        """Deletes a scan's builds with their files, but those of the ideas in `keep`."""
        for build in self.store.delete_idea_builds(account_id, scan_id, keep):
            for name in build.files:
                self.files.delete(name)

    def shutdown(self, timeout_s: float = SHUTDOWN_WAIT_S) -> None:
        """Stops the running set, drops the queue, and waits for the dispatcher."""
        with self._lock:
            self._closing = True
            for job in self._jobs.values():
                job.stop.set()
            self._wake.notify_all()
        if self._dispatcher.is_alive():
            self._dispatcher.join(timeout_s)

    # The dispatcher

    def _next(self) -> BuildJob | None:
        """The next set to run; None once shutting down."""
        with self._lock:
            while not self._closing:
                while self._waiting:
                    job = self._jobs.get(self._waiting.popleft())
                    if job is not None and not job.cancelled:
                        job.status = "running"
                        return job
                self._wake.wait()
            return None

    def _spec(self, job: BuildJob) -> dict:
        limits = self.limits
        return {
            "jobId": job.id,
            "out": str(job.folder / "out"),
            "photo": str(job.folder / "photo"),
            "locale": job.locale,
            "pieces": [line.wire() for line in job.pieces],
            "builds": [
                {"theme": slot.theme, "kind": slot.kind, "avoid": slot.avoid} for slot in job.slots
            ],
            "model": limits.model,
            "effort": limits.effort,
            "maxCalls": limits.build_calls,
            "maxCost": limits.build_cost,
            "maxSeconds": limits.build_seconds,
            "maxPieces": limits.max_pieces,
            "maxTokens": limits.max_tokens,
            "staggerS": limits.stagger_s,
            "deadlineS": limits.deadline_s(len(job.slots)),
        }

    def _dispatch(self) -> None:
        while (job := self._next()) is not None:
            (job.folder / "out").mkdir(exist_ok=True)
            try:
                assert self.runner is not None
                self.runner.run(
                    self._spec(job), lambda event, job=job: self._on_event(job, event), job.stop
                )
            except Exception:
                log.exception("Build job %s: the runner failed", job.id)
            try:
                self._finish(job)
            except Exception:
                log.exception("Build job %s couldn't be finished", job.id)
            finally:
                shutil.rmtree(job.folder, ignore_errors=True)
                with self._lock:
                    job.status, job.ended_at = "ended", self.clock()

    def _slot(self, job: BuildJob, event: dict) -> Slot | None:
        index = event.get("build")
        if isinstance(index, int) and 0 <= index < len(job.slots):
            return job.slots[index]
        return None

    def _on_event(self, job: BuildJob, event: dict) -> None:
        kind = event.get("type")
        slot = self._slot(job, event)
        if kind == "calling" and slot:
            slot.in_flight = float(event.get("worst", 0.0))
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
                if slot:
                    slot.in_flight = 0.0
        elif kind == "built" and slot and slot.state == "building":
            self._save(job, slot, event.get("result") or {})
        elif kind == "failed":
            # Without a build's index, the whole set failed: every build still running.
            failing = [slot] if slot else [s for s in job.slots if s.state == "building"]
            log.info("Build job %s: %d builds failed: %s", job.id, len(failing), event.get("code"))
            for each in failing:
                self._fail(job, each)

    def _save(self, job: BuildJob, slot: Slot, result: dict) -> None:
        """Keeps a build and hands it to its idea; if the idea's set was replaced meanwhile, or
        the set was cancelled, the build goes."""
        index = job.slots.index(slot)
        out = job.folder / "out" / str(index)
        try:
            build = StoredIdeaBuild(
                id=new_id("gen"),
                account_id=job.account_id,
                scan_id=job.scan_id,
                idea_id=slot.idea_id,
                created_at=utc_now(),
                preview_file=self.files.adopt(out / "preview.webp", "webp"),
                model_file=self.files.adopt(out / "model.ldr", "ldr"),
                pieces=[Piece(**piece) for piece in result["pieces"]],
            )
            self.store.insert_idea_build(build)
        except Exception:
            log.exception("Build job %s: build %d couldn't be saved", job.id, index)
            self._fail(job, slot)
            return
        slot.state = "built"
        applied = not job.cancelled and self.sink is not None
        if applied and self.sink is not None:
            applied = self.sink.built(job, slot, build, result)
        if not applied:
            self.delete_build(build.id)
        log.info("Build job %s: build %d for %s %s", job.id, index, slot.idea_id,
                 "applied" if applied else "dropped")  # fmt: skip

    def _fail(self, job: BuildJob, slot: Slot) -> None:
        if slot.state != "building":
            return
        slot.state = "failed"
        if not job.cancelled and self.sink is not None:
            try:
                self.sink.failed(job, slot)
            except Exception:
                log.exception("Build job %s: couldn't hand on a failed build", job.id)

    def _finish(self, job: BuildJob) -> None:
        for slot in job.slots:
            if slot.in_flight > 0:
                # Stopped during a call, which may still be billed: count its worst case.
                spend = Spend(model=self.limits.model, cost=slot.in_flight, estimated=True)
                self.store.record_spend(job.id, self.today(), utc_now(), spend)
                with self._lock:
                    job.cost += slot.in_flight
                    slot.in_flight = 0.0
        if job.cancelled:
            self.store.end_job(job.id, "cancelled", None, counted=True)
            return
        # A build that never came, as when the worker was killed, is a failed one.
        for slot in job.slots:
            self._fail(job, slot)
        built = sum(slot.state == "built" for slot in job.slots)
        code = None if built else "no_build"
        self.store.end_job(job.id, "done" if built else "failed", code, counted=True)
        log.info("Build job %s ended: %d of %d built, $%.3f", job.id, built, len(job.slots),
                 job.cost)  # fmt: skip

    # Under the lock

    def _forget_ended(self) -> None:
        now = self.clock()
        for job_id in [
            job.id
            for job in self._jobs.values()
            if job.ended_at is not None and now - job.ended_at > self.keep_s
        ]:
            del self._jobs[job_id]
