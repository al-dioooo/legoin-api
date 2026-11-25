"""Runs a picture job in a worker process of its own, and watches it (ADR-0012).

`python -m legoin_api.picture.worker` reads the job's spec on stdin and reports events as JSON
lines on stdout. The runner passes them on, and kills the worker when the job is cancelled,
past its deadline, or past its resident memory limit, which it reads from /proc twice a second.
The worker sets its own address-space limit, but that can only be a backstop: llvmpipe reserves
about 5 GB of address space it never uses.
"""

import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
from collections.abc import Callable

log = logging.getLogger(__name__)


class ProcessRunner:
    """Runs each job in `python -m legoin_api.picture.worker`, and watches it: it's killed when
    the job is cancelled, past its deadline, or past its resident memory limit."""

    POLL_S = 0.5
    COMMAND = (sys.executable, "-m", "legoin_api.picture.worker")

    def __init__(
        self,
        deadline_s: float,
        memory_mb: int,
        threads: int = 2,
        command: tuple[str, ...] = COMMAND,
    ):
        self.deadline_s = deadline_s
        self.memory_mb = memory_mb
        self.threads = threads
        self.command = command

    def environment(self) -> dict[str, str]:
        env = dict(os.environ)
        threads = str(self.threads)
        # llvmpipe, NumPy's BLAS and wgpu's thread pools: a few cores, not all of them.
        for name in (
            "LP_NUM_THREADS",
            "OMP_NUM_THREADS",
            "OPENBLAS_NUM_THREADS",
            "RAYON_NUM_THREADS",
        ):
            env[name] = threads
        env["PYTHONUNBUFFERED"] = "1"
        return env

    def run(self, spec: dict, emit: Callable[[dict], None], stop: threading.Event) -> None:
        process = subprocess.Popen(
            list(self.command),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            env=self.environment(),
            text=True,
        )
        lines: queue.Queue[str | None] = queue.Queue()

        def read() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                lines.put(line)
            lines.put(None)

        threading.Thread(target=read, name=f"picture-{spec['jobId']}", daemon=True).start()
        assert process.stdin is not None
        process.stdin.write(json.dumps(spec))
        process.stdin.close()
        deadline_s = float(spec.get("deadlineS", self.deadline_s))
        deadline = time.monotonic() + deadline_s
        try:
            while True:
                try:
                    line = lines.get(timeout=self.POLL_S)
                except queue.Empty:
                    line = ""
                if line is None:
                    break
                if line.strip():
                    try:
                        emit(json.loads(line))
                    except json.JSONDecodeError:
                        log.warning("Picture job %s: not an event: %.200s", spec["jobId"], line)
                if stop.is_set():
                    break
                if time.monotonic() > deadline:
                    log.warning("Picture job %s ran past %.0f s", spec["jobId"], deadline_s)
                    emit(
                        {
                            "type": "failed",
                            "code": "timeout",
                            "message": "The picture build took too long.",
                        }
                    )
                    break
                if (resident := resident_mb(process.pid)) > self.memory_mb:
                    log.warning("Picture job %s used %d MB", spec["jobId"], resident)
                    emit(
                        {
                            "type": "failed",
                            "code": "internal",
                            "message": "The picture worker used too much memory.",
                        }
                    )
                    break
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)


def resident_mb(pid: int) -> int:
    """A process's resident memory in MB, from /proc; 0 where there's no /proc."""
    try:
        with open(f"/proc/{pid}/status") as status:
            for line in status:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) // 1024
    except OSError:
        pass
    return 0
