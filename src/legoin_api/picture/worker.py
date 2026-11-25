"""The picture worker: one process per job, started by the dispatcher in `jobs.py` with

    python -m legoin_api.picture.worker

It reads the job's spec as JSON on stdin, designs the build (of a picture, or of an idea's
preview when the spec's `kind` is `idea`), checks it and renders it, and reports as JSON lines
on stdout, one event a line:

- `{"type": "stage", "stage": "designing" | "checking" | "rendering"}`;
- `{"type": "calling", "worst": 0.31}` before each Claude call, with the most it could cost;
- `{"type": "called", "model": ..., "cost": ..., "usage": {...}}` after it;
- `{"type": "done", "build": {...}}`, with the preview and model in the spec's `out` folder;
- `{"type": "failed", "code": ..., "message": ...}`.

The worker writes no database and keeps nothing: the dispatcher records spending and saves the
build. It lowers its own priority and caps its address space; the dispatcher also watches its
memory and its time, and kills it past either. Logs go to stderr, never the key.
"""

import json
import logging
import os
import resource
import sys
import time
from collections.abc import Callable
from pathlib import Path

from legoin_api.picture.agent import MAX_TOKENS, Caps, IdeaBrief, Outcome, PictureAgent
from legoin_api.picture.grid import parts_of
from legoin_api.schemas import PartCount

log = logging.getLogger("legoin_api.picture.worker")

ADDRESS_SPACE = 8 << 30
"""A backstop only: llvmpipe reserves about 5 GB of address space it never touches, so the
real limit is on resident memory, which the dispatcher watches."""
NICE = 10
REQUEST_TIMEOUT_S = 120.0
MESSAGES = {
    "picture_unusable": "There's no clear subject in the picture to build.",
    "picture_refused": "The picture was declined, or isn't suitable to build.",
    "no_valid_build": "A limit on the job ended it before a valid build.",
    "unavailable": "The model provider couldn't be reached or was overloaded.",
    "internal": "The picture build failed.",
}

Emit = Callable[[dict], None]


def limit_self() -> None:
    """Lowers this process's priority and caps its address space."""
    try:
        os.nice(NICE)
    except OSError:
        log.warning("Couldn't lower the picture worker's priority")
    _, hard = resource.getrlimit(resource.RLIMIT_AS)
    cap = ADDRESS_SPACE if hard == resource.RLIM_INFINITY else min(ADDRESS_SPACE, hard)
    resource.setrlimit(resource.RLIMIT_AS, (cap, hard))


def failed(emit: Emit, code: str, message: str | None = None) -> None:
    emit({"type": "failed", "code": code, "message": message or MESSAGES[code]})


def run(spec: dict, emit: Emit, *, create=None, checks=None, renders=None, export=None) -> None:
    """Runs one job. Tests pass fakes for `create` (Claude), `checks`, `renders` and `export`
    (the `.ldr`)."""
    import anthropic

    emit({"type": "stage", "stage": "designing"})
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

    def called(response, cost: float) -> None:
        usage = response.usage
        emit(
            {
                "type": "called",
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
        before_call=lambda worst: emit({"type": "calling", "worst": worst}),
        on_call=called,
    )
    try:
        if spec.get("kind") == "idea":
            idea = spec["idea"]
            brief = IdeaBrief(
                title=idea["title"],
                summary=idea["summary"],
                steps=list(idea["steps"]),
                parts=[PartCount.model_validate(line) for line in idea["parts"]],
            )
            outcome = agent.run_idea(brief, spec["locale"])
        else:
            outcome = agent.run(Path(spec["picture"]).read_bytes(), spec["locale"])
    except (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.InternalServerError):
        log.exception("Claude couldn't be reached for picture job %s", spec["jobId"])
        failed(emit, "unavailable")
        return
    except anthropic.APIStatusError as error:
        log.error("Claude answered %s, request %s", error.status_code, error.request_id)
        failed(emit, "unavailable" if error.status_code >= 500 else "internal")
        return
    log.info(
        "Picture job %s: %s %s after %d calls, $%.3f, %.1f s, %s",
        spec["jobId"], outcome.status, outcome.code, outcome.calls, outcome.cost,
        outcome.seconds, dict(outcome.tokens),
    )  # fmt: skip
    if outcome.status == "failed":
        failed(emit, outcome.code)
        return
    finish(spec, emit, outcome, checks, renders, export)


def finish(spec: dict, emit: Emit, outcome: Outcome, checks, renders, export) -> None:
    """Checks the finished build once more, then renders it and writes its model."""
    emit({"type": "stage", "stage": "checking"})
    if problems := checks.problems(outcome.pieces):
        # The agent only ends with a build that passed; this would be a bug.
        log.error("Picture job %s ended with an invalid build: %s", spec["jobId"], problems)
        failed(emit, "internal")
        return
    parts = parts_of(outcome.pieces)
    emit({"type": "stage", "stage": "rendering"})
    out = Path(spec["out"])
    started = time.monotonic()
    (out / "preview.webp").write_bytes(renders.preview(outcome.pieces, outcome.title))
    (out / "model.ldr").write_text(export(outcome.pieces, outcome.title))
    log.info("Picture job %s rendered in %.1f s", spec["jobId"], time.monotonic() - started)
    emit(
        {
            "type": "done",
            "build": {
                "title": outcome.title,
                "summary": outcome.summary,
                "steps": outcome.steps,
                "parts": [line.wire() for line in parts],
                "pieces": [piece.wire() for piece in outcome.pieces],
                "plain": outcome.written_by == "plain",
                "model": spec["model"],
                "calls": outcome.calls,
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
        failed(emit, "internal", "The picture worker ran out of memory.")
    except Exception:
        log.exception("Picture job %s failed", spec.get("jobId"))
        failed(emit, "internal")
    return 0


if __name__ == "__main__":
    sys.exit(main())
