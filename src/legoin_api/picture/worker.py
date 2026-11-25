"""The build worker: one process per set of builds, started by the dispatcher in `jobs.py` with

    python -m legoin_api.picture.worker

It reads the set's spec as JSON on stdin: the scan's photo and pieces, and what each build is
(ADR-0013). It runs the builds at the same time, one conversation each, checks and renders
each as it finishes, and reports as JSON lines on stdout, one event a line, each naming its
build by its index in the spec's `builds`:

- `{"type": "calling", "build": 0, "worst": 0.31}` before each Claude call, with the most it
  could cost;
- `{"type": "called", "build": 0, "model": ..., "cost": ..., "usage": {...}}` after it;
- `{"type": "built", "build": 0, "result": {...}}`, with its preview and model in
  `<out>/<index>/`;
- `{"type": "failed", "build": 0, "code": ..., "message": ...}`; without `build`, every build
  still running failed.

The builds share one renderer and one set of BrickLayer checks, one call at a time. The worker
writes no database and keeps nothing: the dispatcher records spending and saves the builds. It
lowers its own priority and caps its address space; the dispatcher also watches its memory and
its time, and kills it past either. Logs go to stderr, never the key.
"""

import json
import logging
import os
import resource
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from legoin_api.picture.agent import MAX_TOKENS, Caps, PictureAgent, difficulty_of
from legoin_api.picture.grid import parts_of
from legoin_api.picture.prompt import BuildBrief
from legoin_api.schemas import PartCount

log = logging.getLogger("legoin_api.picture.worker")

ADDRESS_SPACE = 8 << 30
"""A backstop only: llvmpipe reserves about 5 GB of address space it never touches, so the
real limit is on resident memory, which the dispatcher watches."""
NICE = 10
REQUEST_TIMEOUT_S = 120.0
MESSAGES = {
    "refused": "Claude declined the build.",
    "no_valid_build": "A limit on the build ended it before a valid one.",
    "max_calls": "A limit on the build ended it before a valid one.",
    "max_cost": "A limit on the build ended it before a valid one.",
    "max_seconds": "A limit on the build ended it before a valid one.",
    "unavailable": "The model provider couldn't be reached or was overloaded.",
    "internal": "The build failed.",
}

Emit = Callable[[dict], None]


def limit_self() -> None:
    """Lowers this process's priority and caps its address space."""
    try:
        os.nice(NICE)
    except OSError:
        log.warning("Couldn't lower the build worker's priority")
    _, hard = resource.getrlimit(resource.RLIMIT_AS)
    cap = ADDRESS_SPACE if hard == resource.RLIM_INFINITY else min(ADDRESS_SPACE, hard)
    resource.setrlimit(resource.RLIMIT_AS, (cap, hard))


def failed(emit: Emit, code: str, index: int | None = None, message: str | None = None) -> None:
    event = {"type": "failed", "code": code, "message": message or MESSAGES.get(code, code)}
    if index is not None:
        event["build"] = index
    emit(event)


class Locked:
    """The renderer or the checks, one call at a time across the builds' threads."""

    def __init__(self, inner, lock: threading.Lock):
        self.inner, self.lock = inner, lock

    def views(self, pieces, views):
        with self.lock:
            return self.inner.views(pieces, views)

    def preview(self, pieces, title):
        with self.lock:
            return self.inner.preview(pieces, title)

    def problems(self, pieces):
        with self.lock:
            return self.inner.problems(pieces)


def run(spec: dict, emit: Emit, *, create=None, checks=None, renders=None, export=None) -> None:
    """Runs one set of builds. Tests pass fakes for `create` (Claude), `checks`, `renders` and
    `export` (the `.ldr`)."""
    import anthropic

    said = threading.Lock()

    def tell(event: dict) -> None:
        with said:
            emit(event)

    if create is None:
        client = anthropic.Anthropic(timeout=REQUEST_TIMEOUT_S, max_retries=1)
        create = client.beta.messages.create
    if checks is None:
        from legoin_api.picture.checks import BrickLayerChecks

        checks = BrickLayerChecks()
    if renders is None:
        from legoin_api.picture.render import Renderer

        renders = Renderer()
    if export is None:
        from legoin_api.picture.checks import ldraw as export
    shared = threading.Lock()
    checks, renders = Locked(checks, shared), Locked(renders, shared)

    photo = Path(spec["photo"]).read_bytes()
    pieces = [PartCount.model_validate(line) for line in spec["pieces"]]
    briefs = [
        BuildBrief(
            theme=build["theme"],
            kind=build["kind"],
            max_pieces=int(spec["maxPieces"]),
            avoid=list(build.get("avoid") or []),
        )
        for build in spec["builds"]
    ]
    stagger = float(spec.get("staggerS", 0.0))

    def one(index: int) -> None:
        time.sleep(index * stagger)
        try:
            build(spec, index, photo, pieces, briefs[index], tell, create, checks, renders, export)
        except (
            anthropic.APIConnectionError,
            anthropic.RateLimitError,
            anthropic.InternalServerError,
        ):
            log.exception("Claude couldn't be reached for build %d of job %s", index, spec["jobId"])
            failed(tell, "unavailable", index)
        except anthropic.APIStatusError as error:
            log.error("Claude answered %s, request %s", error.status_code, error.request_id)
            failed(tell, "unavailable" if error.status_code >= 500 else "internal", index)
        except MemoryError:
            failed(tell, "internal", index, "The build worker ran out of memory.")
        except Exception:
            log.exception("Build %d of job %s failed", index, spec["jobId"])
            failed(tell, "internal", index)

    with ThreadPoolExecutor(max_workers=len(briefs), thread_name_prefix="build") as pool:
        list(pool.map(one, range(len(briefs))))


def build(
    spec: dict,
    index: int,
    photo: bytes,
    pieces: list[PartCount],
    brief: BuildBrief,
    tell: Emit,
    create,
    checks,
    renders,
    export,
) -> None:
    """Designs one build, then checks it once more, renders it and writes its model."""

    def called(response, cost: float) -> None:
        usage = response.usage
        tell(
            {
                "type": "called",
                "build": index,
                "model": response.model,
                "cost": cost,
                "usage": {
                    "input": usage.input_tokens or 0,
                    "output": usage.output_tokens or 0,
                    "cacheRead": usage.cache_read_input_tokens or 0,
                    "cacheWrite": usage.cache_creation_input_tokens or 0,
                },
            }
        )

    agent = PictureAgent(
        create,
        checks,
        renders,
        model=spec["model"],
        effort=spec["effort"],
        caps=Caps(spec["maxCalls"], spec["maxCost"], spec["maxSeconds"]),
        max_tokens=int(spec.get("maxTokens", MAX_TOKENS)),
        before_call=lambda worst: tell({"type": "calling", "build": index, "worst": worst}),
        on_call=called,
    )
    outcome = agent.run_scan(photo, pieces, brief, spec["locale"])
    log.info(
        "Build %d of job %s: %s %s after %d calls, $%.3f, %.1f s, %s",
        index, spec["jobId"], outcome.status, outcome.code, outcome.calls, outcome.cost,
        outcome.seconds, dict(outcome.tokens),
    )  # fmt: skip
    if outcome.status == "failed":
        failed(tell, outcome.code, index)
        return
    if problems := checks.problems(outcome.pieces):
        # The agent only ends with a build that passed; this would be a bug.
        log.error("Build %d of job %s is invalid: %s", index, spec["jobId"], problems)
        failed(tell, "internal", index)
        return
    out = Path(spec["out"]) / str(index)
    out.mkdir(parents=True, exist_ok=True)
    (out / "preview.webp").write_bytes(renders.preview(outcome.pieces, outcome.title))
    (out / "model.ldr").write_text(export(outcome.pieces, outcome.title))
    tell(
        {
            "type": "built",
            "build": index,
            "result": {
                "title": outcome.title,
                "summary": outcome.summary,
                "steps": outcome.steps,
                "parts": [line.wire() for line in parts_of(outcome.pieces)],
                "pieces": [piece.wire() for piece in outcome.pieces],
                "difficulty": difficulty_of(len(outcome.pieces)),
                "plain": outcome.written_by == "plain",
                "model": spec["model"],
                "calls": outcome.calls,
                "cost": round(outcome.cost, 6),
                "seconds": round(outcome.seconds, 1),
            },
        }
    )


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, stream=sys.stderr, format="%(levelname)s:     %(name)s: %(message)s"
    )
    spec = json.loads(sys.stdin.read())
    limit_self()

    def emit(event: dict) -> None:
        sys.stdout.write(json.dumps(event) + "\n")
        sys.stdout.flush()

    try:
        run(spec, emit)
    except MemoryError:
        failed(emit, "internal", message="The build worker ran out of memory.")
    except Exception:
        log.exception("Build job %s failed", spec.get("jobId"))
        failed(emit, "internal")
    return 0


if __name__ == "__main__":
    sys.exit(main())
