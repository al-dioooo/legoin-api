"""Scan jobs, run by an in-process worker and served by polling (contract section 4).

A job moves through `detecting`, `classifying` and `coloring`, then ends `done` with a saved
scan or `failed` with an error, and never changes after that. Jobs live in memory, so the
service runs as one process (ADR-0008). The rules:

- A job waiting for the worker reports `detecting`.
- Cancelling stops it at the next stage and saves nothing; if it had finished, the scan it
  saved is deleted.
- A job still running `timeout_s` after its upload fails with `timeout`, and whatever it
  finds later is thrown away.
- The photo is written only when the scan is saved, so a failed or cancelled job leaves
  nothing behind.
- Ended jobs are forgotten after `keep_s`.
- At most `queue_size` jobs wait or run at once, each holding its decoded photo. An upload
  takes its place before the photo is decoded, so the memory they hold stays bounded.
"""

import logging
import math
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, replace
from typing import Literal

from PIL import Image

from legoin_api.ids import new_id, utc_now
from legoin_api.photos import PhotoStore
from legoin_api.pipeline import Found, Pipeline
from legoin_api.quality import MESSAGES, photo_problem
from legoin_api.schemas import Box, Detection, ErrorDetail, ScanStage
from legoin_api.store import Store, StoredScan

log = logging.getLogger(__name__)

KEEP_ENDED_JOBS_S = 600.0
"""The contract keeps an ended job for at least 10 minutes."""

SHUTDOWN_WAIT_S = 5.0
"""How long shutting down waits for a running job to stop; a stage takes well under 1 s."""


@dataclass
class Job:
    id: str
    device_id: str
    deadline: float
    image: Image.Image | None
    status: Literal["running", "done", "failed"] = "running"
    stage: ScanStage = "detecting"
    scan_id: str | None = None
    error: ErrorDetail | None = None
    ended_at: float | None = None
    cancelled: bool = False


class QueueFull(Exception):
    """Every place in the queue is taken: the API answers `503 unavailable`."""


class _Stop(Exception):
    """The job was cancelled or ran out of time; stop without saving."""


def to_box(found: Found, width: int, height: int) -> Box:
    """A detector box in pixels, as the contract's fractions of the photo."""

    def fraction(value: float, size: int) -> float:
        return round(min(max(value / size, 0.0), 1.0), 4)

    x, y = fraction(found.x1, width), fraction(found.y1, height)
    right, bottom = fraction(found.x2, width), fraction(found.y2, height)
    return Box(x=x, y=y, width=round(right - x, 4), height=round(bottom - y, 4))


def floor4(value: float) -> float:
    """Rounds down, so a confidence never crosses the web app's 0.6 flag by rounding."""
    return math.floor(value * 10_000) / 10_000


class JobManager:
    def __init__(
        self,
        pipeline: Pipeline,
        store: Store,
        photos: PhotoStore,
        *,
        timeout_s: float = 30.0,
        keep_s: float = KEEP_ENDED_JOBS_S,
        workers: int = 1,
        queue_size: int = 8,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.pipeline = pipeline
        self.store = store
        self.photos = photos
        self.timeout_s = timeout_s
        self.keep_s = keep_s
        self.queue_size = queue_size
        self.clock = clock
        self._queued = 0
        self._closing = False
        self._running_futures: set[Future] = set()
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="scan")

    def shutdown(self, timeout_s: float = SHUTDOWN_WAIT_S) -> None:
        """Cancels waiting jobs and waits for running ones, which stop at their next stage and
        save nothing. Call it before closing the store, so no job writes to a closed one."""
        with self._lock:
            self._closing = True
            futures = set(self._running_futures)
        self._executor.shutdown(wait=False, cancel_futures=True)
        # `wait` never counts a future cancelled that way as done, so leave those out.
        running = {future for future in futures if not future.cancelled()}
        _, still_running = wait(running, timeout=timeout_s)
        if still_running:
            log.warning("Shutting down with %d scan job(s) still running", len(still_running))

    def reserve(self) -> None:
        """Takes a place in the queue for a photo about to be read, or raises `QueueFull`.

        `submit(..., reserved=True)` hands the place to its job, which gives it back when it
        ends. If no job comes of it, `release` gives it back.
        """
        with self._lock:
            if self._queued >= self.queue_size:
                raise QueueFull
            self._queued += 1

    def release(self) -> None:
        with self._lock:
            self._queued -= 1

    def submit(self, device_id: str, image: Image.Image, *, reserved: bool = False) -> Job:
        """Queues a job. Without `reserved`, it takes its own place or raises `QueueFull`;
        with it, a place taken by `reserve`, which stays the caller's if this raises."""
        if not reserved:
            self.reserve()
        job = Job(new_id("job"), device_id, self.clock() + self.timeout_s, image)
        with self._lock:
            self._forget_ended()
            self._jobs[job.id] = job
            view = replace(job, image=None)
        try:
            future = self._executor.submit(self._run, job)
        except BaseException:
            with self._lock:
                self._jobs.pop(job.id, None)
            if not reserved:
                self.release()
            raise
        with self._lock:
            self._running_futures.add(future)
        future.add_done_callback(self._forget_future)
        return view

    def _forget_future(self, future: Future) -> None:
        with self._lock:
            self._running_futures.discard(future)

    def get(self, device_id: str, job_id: str) -> Job | None:
        """A copy of the job as it is now, or None if this device has no such job."""
        with self._lock:
            self._forget_ended()
            job = self._jobs.get(job_id)
            if job is None or job.device_id != device_id:
                return None
            self._check_deadline(job)
            return replace(job, image=None)

    def cancel(self, device_id: str, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.device_id != device_id:
                return
            job.cancelled = True
            del self._jobs[job_id]
            saved = job.scan_id if job.status == "done" else None
        if saved:
            scan = self.store.delete_scan(device_id, saved)
            if scan:
                self.photos.delete(scan.photo_file)

    # The worker

    def _run(self, job: Job) -> None:
        image = job.image
        assert image is not None
        try:
            found = self._stage(job, "detecting", lambda: self.pipeline.detect(image))
            if not found:
                code = photo_problem(image)
                self._end(job, error=ErrorDetail(code=code, message=MESSAGES[code]))
                return
            parts = self._stage(job, "classifying", lambda: self.pipeline.classify(image, found))
            colors = self._stage(job, "coloring", lambda: self.pipeline.color(image, found))
            self._save(job, image, found, parts, colors)
        except _Stop:
            pass
        except Exception:
            log.exception("Scan job %s failed", job.id)
            self._end(job, error=ErrorDetail(code="internal", message="The scan failed."))
        finally:
            job.image = None
            self.release()

    def _stage(self, job: Job, stage: ScanStage, step: Callable):
        with self._lock:
            if not self._running(job):
                raise _Stop
            job.stage = stage
        started = time.perf_counter()
        result = step()
        log.info("Scan job %s: %s took %.3f s", job.id, stage, time.perf_counter() - started)
        return result

    def _save(self, job: Job, image: Image.Image, found, parts, colors) -> None:
        detections = []
        taken: set[str] = set()
        for piece, part, color in zip(found, parts, colors, strict=True):
            while (detection_id := new_id("det", 6)) in taken:
                pass
            taken.add(detection_id)
            detections.append(
                Detection(
                    id=detection_id,
                    part=part.value,
                    color=color.value,
                    box=to_box(piece, image.width, image.height),
                    # The contract's safe default: the lowest of the three stages' scores.
                    confidence=floor4(min(piece.score, part.confidence, color.confidence)),
                )
            )
        photo = self.photos.save(image)
        kept = False
        try:
            with self._lock:
                if self._running(job):
                    scan = StoredScan(
                        id=new_id("scn", 12),
                        device_id=job.device_id,
                        created_at=utc_now(),
                        photo_file=photo,
                        photo_width=image.width,
                        photo_height=image.height,
                        detections=detections,
                    )
                    self.store.insert_scan(scan)
                    job.status, job.scan_id, job.ended_at = "done", scan.id, self.clock()
                    kept = True
        finally:
            # Not kept, or the insert failed (a full disk, a closed store): no orphaned photo.
            if not kept:
                self.photos.delete(photo)

    def _end(self, job: Job, *, error: ErrorDetail) -> None:
        with self._lock:
            if self._running(job):
                job.status, job.error, job.ended_at = "failed", error, self.clock()

    # Under the lock

    def _running(self, job: Job) -> bool:
        return not self._closing and not job.cancelled and not self._check_deadline(job)

    def _check_deadline(self, job: Job) -> bool:
        """Ends a job that ran out of time. True once the job has ended, for any reason."""
        if job.status == "running" and self.clock() >= job.deadline:
            job.status = "failed"
            job.error = ErrorDetail(code="timeout", message="The scan took too long.")
            job.ended_at = self.clock()
        return job.status != "running"

    def _forget_ended(self) -> None:
        for job in self._jobs.values():
            self._check_deadline(job)
        now = self.clock()
        for job_id in [
            job.id
            for job in self._jobs.values()
            if job.ended_at is not None and now - job.ended_at > self.keep_s
        ]:
            del self._jobs[job_id]
