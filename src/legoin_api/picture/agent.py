"""The build agent: Claude designs a build from a scan's pieces by calling strict tools, and this
code applies each call to a `Build` and checks it with BrickLayer. No model-written code runs.

One build is a conversation (ADR-0013). Claude gets the cached system prompt (`prompt.py`), the
scan's photo and its pieces, and what to build. It calls `place_pieces`, `remove_pieces`,
`validate`, `render`, and at last `finish`. `place_pieces` refuses any piece the scan didn't
find, or one more than it counted. `finish` only takes a build that passes BrickLayer's checks
and that Claude has looked at in a render since its last change, as BrickWright asks its
agents to.

**Caps.** Before every API call, the worst that call could cost (its input as large as the last
one plus 4,000 tokens, all of it written to the cache, and `MAX_TOKENS` of output) has to fit
under the job's cost cap, and the job has to be under its call and time caps. A cap that's hit
ends the build with the last valid one Claude made, under a plain title and plain steps, or
fails it with `no_valid_build`.

Claude and the BrickLayer checks and renders are passed in, so tests run with fakes.
"""

import base64
import io
import json
import logging
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from legoin_api.picture.grid import (
    MAX_STEPS,
    MIN_PIECES,
    Build,
    Piece,
    layer_steps,
    plain_text,
    size_of,
)
from legoin_api.picture.pricing import cost_of, worst_case
from legoin_api.picture.prompt import SYSTEM, TOOLS, VIEWS, BuildBrief, brief_text, pieces_text
from legoin_api.schemas import Locale, PartCount

log = logging.getLogger(__name__)

MAX_TOKENS = 4000
"""Per API call: room for a turn's thinking and a batch of pieces. A turn cut off here is told
to place smaller batches. It also bounds each call's worst case, and so the cap a build needs."""
PICTURE_MAX_SIDE = 1024
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TITLE = 60
NEAR_THE_END = 3
"""Calls left when `validate` starts telling Claude to finish."""


class Checks(Protocol):
    """BrickLayer's checks (`checks.py`), or a fake."""

    def problems(self, pieces: tuple[Piece, ...]) -> list[str]:
        """What's wrong with the build: collisions, floating pieces, more than one assembly.
        Empty when it's valid."""
        ...


class Renders(Protocol):
    """The renderer (`render.py`), or a fake."""

    def views(self, pieces: tuple[Piece, ...], views: list[str]) -> bytes:
        """The build from each named view, tiled with labels, as a PNG."""
        ...


@dataclass(frozen=True)
class Caps:
    max_calls: int = 24
    max_cost: float = 0.50
    """Dollars."""
    max_seconds: float = 300.0


Code = Literal["finished", "refused", "no_valid_build", "max_calls", "max_cost", "max_seconds"]


@dataclass
class Outcome:
    """How a build ended. `done` carries a build; `failed` carries a code."""

    status: Literal["done", "failed"]
    code: Code
    pieces: tuple[Piece, ...] = ()
    title: str = ""
    summary: str = ""
    steps: list[str] = field(default_factory=list)
    written_by: Literal["claude", "plain", ""] = ""
    """Who wrote the title, summary and steps: Claude, or the plain ones after a cap."""
    calls: int = 0
    cost: float = 0.0
    seconds: float = 0.0
    tokens: Counter = field(default_factory=Counter)
    tools: Counter = field(default_factory=Counter)
    models: set[str] = field(default_factory=set)


@dataclass
class _Turn:
    """What one turn of tool calls changed."""

    results: list[dict] = field(default_factory=list)
    ended: Outcome | None = None
    rendered: tuple[Piece, ...] | None = None


def picture_block(data: bytes) -> dict:
    """The picture as an image block: upright, at most `PICTURE_MAX_SIDE`, re-encoded as a JPEG
    with no metadata."""
    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(data)) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
    image.thumbnail((PICTURE_MAX_SIDE, PICTURE_MAX_SIDE))
    out = io.BytesIO()
    image.save(out, "JPEG", quality=88)
    source = {"type": "base64", "media_type": "image/jpeg", "data": _b64(out.getvalue())}
    return {"type": "image", "source": source}


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def difficulty_of(pieces: int) -> str:
    """An idea's difficulty, from its piece count: like the library's builds, which are easy
    up to 8 pieces."""
    if pieces <= 8:
        return "easy"
    return "medium" if pieces <= 16 else "hard"


class PictureAgent:
    def __init__(
        self,
        create: Callable[..., Any],
        checks: Checks,
        renders: Renders,
        *,
        model: str,
        effort: str,
        caps: Caps,
        max_tokens: int = MAX_TOKENS,
        before_call: Callable[[float], None] | None = None,
        on_call: Callable[[Any, float], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.create = create
        """`client.beta.messages.create`, or a fake with its shape."""
        self.checks = checks
        self.renders = renders
        self.model = model
        self.effort = effort
        self.caps = caps
        self.max_tokens = max_tokens
        """Per call. An idea's preview takes short turns, so it asks for less, which keeps
        each call's worst case, and so its cap, small."""
        self.before_call = before_call
        """Hears the worst a call could cost, just before it's made."""
        self.on_call = on_call
        """Hears each response and its cost, e.g. to record spend; may raise to stop."""
        self.clock = clock

    def run_scan(
        self, photo: bytes, pieces: list[PartCount], brief: BuildBrief, locale: Locale
    ) -> Outcome:
        """Designs one build from a scan's pieces: the kind of thing `brief` asks for, from only
        those pieces, within their counts."""
        return _Run(
            self, scan_content(photo, pieces, brief, locale), locale, build_for(pieces, brief)
        ).run()


def scan_content(
    photo: bytes, pieces: list[PartCount], brief: BuildBrief, locale: Locale
) -> list[dict]:
    """The first message of a build: the photo and the pieces, which every build of the scan
    shares and which end at a cache breakpoint, then what to build."""
    limit = build_for(pieces, brief).max_pieces
    return [
        picture_block(photo),
        {"type": "text", "text": pieces_text(pieces), "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": brief_text(brief, locale, limit)},
    ]


def build_for(pieces: list[PartCount], brief: BuildBrief) -> Build:
    total = sum(line.quantity for line in pieces)
    return Build(stock=pieces, max_pieces=min(brief.max_pieces, total))


class _Run:
    def __init__(
        self,
        agent: PictureAgent,
        content: list[dict],
        locale: Locale,
        build: Build,
    ):
        self.agent = agent
        self.locale = locale
        self.started = agent.clock()
        self.build = build
        self.outcome = Outcome(status="failed", code="no_valid_build")
        self.messages: list[dict] = [{"role": "user", "content": content}]
        self.last_input = 0
        self.worst = 0.0
        self.checked: tuple[tuple[Piece, ...] | None, bool] = (None, False)
        self.last_valid: tuple[Piece, ...] = ()
        self.seen: tuple[Piece, ...] | None = None
        """The build as Claude last saw it in a render, in an earlier turn."""

    def run(self) -> Outcome:
        while True:
            if (cap := self._cap_reached()) is not None:
                return self._end_with_last_valid(cap)
            response = self._call()
            stop = response.stop_reason
            if stop == "refusal":
                details = getattr(response, "stop_details", None)
                log.info("Claude declined (%s)", getattr(details, "category", None))
                return self._end("failed", "refused")
            calls = [block for block in response.content if block.type == "tool_use"]
            if stop == "max_tokens" or (stop == "tool_use" and not calls):
                self._say(
                    "Your last reply was cut off, so none of it took effect. Place smaller batches."
                )
                continue
            if not calls:
                self._say(
                    "Keep going with the tools: fix and validate the build, then call finish."
                )
                continue
            turn = self._run_tools(calls)
            if turn.ended is not None:
                return turn.ended
            self._remember_valid()
            # Claude sees this turn's render when it reads the results, so from the next turn on.
            if turn.rendered is not None:
                self.seen = turn.rendered
            self.messages.append({"role": "user", "content": turn.results})

    # The conversation

    def _cap_reached(self) -> Code | None:
        agent, outcome = self.agent, self.outcome
        if outcome.calls >= agent.caps.max_calls:
            return "max_calls"
        if agent.clock() - self.started >= agent.caps.max_seconds:
            return "max_seconds"
        self.worst = worst_case(agent.model, self.last_input + 4000, agent.max_tokens)
        if outcome.cost + self.worst > agent.caps.max_cost:
            return "max_cost"
        return None

    def _call(self):
        agent = self.agent
        if agent.before_call:
            agent.before_call(self.worst)
        response = agent.create(
            model=agent.model,
            max_tokens=agent.max_tokens,
            # The stable prefix, cached on its own; top-level caching follows the conversation.
            system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
            cache_control={"type": "ephemeral"},
            messages=self.messages,
            tools=TOOLS,
            # Forced tool use is refused on Sonnet 5.5 and Opus 5.5, so Claude decides.
            tool_choice={"type": "auto"},
            thinking={"type": "adaptive"},
            output_config={"effort": agent.effort},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )
        usage = response.usage
        cost = cost_of(usage, response.model, agent.model)
        outcome = self.outcome
        outcome.calls += 1
        outcome.cost += cost
        outcome.models.add(response.model)
        outcome.tokens.update(
            input=usage.input_tokens or 0,
            output=usage.output_tokens or 0,
            cacheRead=usage.cache_read_input_tokens or 0,
            cacheWrite=usage.cache_creation_input_tokens or 0,
        )
        self.last_input = (
            (usage.input_tokens or 0)
            + (usage.cache_read_input_tokens or 0)
            + (usage.cache_creation_input_tokens or 0)
        )
        if agent.on_call:
            agent.on_call(response, cost)
        # The whole turn goes back, thinking blocks included, as the API expects.
        self.messages.append({"role": "assistant", "content": response.content})
        return response

    def _say(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def _run_tools(self, calls) -> _Turn:
        turn = _Turn()
        for call in calls:
            self.outcome.tools[call.name] += 1
            content, is_error = self._tool(call.name, call.input or {}, turn)
            result = {"type": "tool_result", "tool_use_id": call.id, "content": content}
            if is_error:
                result["is_error"] = True
            turn.results.append(result)
            if turn.ended is not None:
                break
        return turn

    def _remember_valid(self) -> None:
        """Keeps the last valid build, for when a cap ends the job."""
        pieces = self.build.snapshot()
        if pieces != self.checked[0]:
            self.checked = (pieces, self._valid(pieces))
        if self.checked[1]:
            self.last_valid = pieces

    def _valid(self, pieces: tuple[Piece, ...]) -> bool:
        return len(pieces) >= MIN_PIECES and not self.agent.checks.problems(pieces)

    # The tools

    def _tool(self, name: str, args: dict, turn: _Turn) -> tuple[Any, bool]:
        build = self.build
        if name == "place_pieces":
            placed = build.place(list(args.get("pieces") or []))
            return json.dumps({"placed": placed, "pieces": len(build.pieces)}), False
        if name == "remove_pieces":
            removed = build.remove([str(i) for i in args.get("ids") or []])
            return json.dumps({"removed": removed, "pieces": len(build.pieces)}), False
        if name == "validate":
            return json.dumps(self._report()), False
        if name == "render":
            return self._render(args.get("view"), turn)
        if name == "finish":
            return self._finish(args, turn)
        return f"There's no tool {name!r}.", True

    def _report(self) -> dict:
        pieces = self.build.snapshot()
        problems = self.agent.checks.problems(pieces) if pieces else ["the build has no pieces"]
        if 0 < len(pieces) < MIN_PIECES:
            problems = [*problems, f"a build needs at least {MIN_PIECES} pieces"]
        self.checked = (pieces, not problems)
        left = self.agent.caps.max_calls - self.outcome.calls
        report = {
            "ok": not problems,
            "problems": problems,
            "pieces": len(pieces),
            "maxPieces": self.build.max_pieces,
            "size": size_of(pieces),
            "turnsLeft": left,
            "list": [piece.describe() for piece in pieces],
        }
        if self.build.stock is not None:
            report["left"] = self.build.left()
        if left <= NEAR_THE_END:
            report["note"] = "Almost out of turns: fix what's left and finish now."
        return report

    def _render(self, view: str | None, turn: _Turn) -> tuple[Any, bool]:
        pieces = self.build.snapshot()
        if not pieces:
            return "There's nothing to render yet.", True
        view = view if view in VIEWS else "all"
        names = ["front", "right", "top", "iso"] if view == "all" else [view]
        png = self.agent.renders.views(pieces, names)
        turn.rendered = pieces
        image = {"type": "base64", "media_type": "image/png", "data": _b64(png)}
        text = (
            f"The {', '.join(names)} view{'s' if len(names) > 1 else ''} of {len(pieces)} pieces."
        )
        return [{"type": "image", "source": image}, {"type": "text", "text": text}], False

    def _finish(self, args: dict, turn: _Turn) -> tuple[Any, bool]:
        pieces = self.build.snapshot()
        title = str(args.get("title") or "").strip()
        summary = str(args.get("summary") or "").strip()
        steps = [str(step).strip() for step in args.get("steps") or [] if str(step).strip()]
        problems = self._report()["problems"]
        if not 1 <= len(title) <= MAX_TITLE:
            problems.append(f"it needs a title of 1 to {MAX_TITLE} characters")
        if not summary:
            problems.append("it needs a summary")
        if not 2 <= len(steps) <= MAX_STEPS:
            problems.append(f"it needs 2 to {MAX_STEPS} steps")
        if not problems and self.seen != pieces:
            problems.append(
                "look at a render of the build as it is now first: call render, check it reads "
                "as what you meant, then finish in a later turn"
            )
        if problems:
            return json.dumps({"finished": False, "problems": problems}), True
        turn.ended = self._end("done", "finished", pieces, title, summary, steps, "claude")
        return json.dumps({"finished": True}), False

    # Endings

    def _end_with_last_valid(self, cap: Code) -> Outcome:
        log.info("A build hit %s after %d calls, $%.3f", cap, self.outcome.calls, self.outcome.cost)
        if not self.last_valid:
            return self._end("failed", "no_valid_build")
        title, summary = plain_text(self.last_valid, self.locale)
        steps = layer_steps(self.last_valid, self.locale)
        return self._end("done", cap, self.last_valid, title, summary, steps, "plain")

    def _end(
        self,
        status: Literal["done", "failed"],
        code: Code,
        pieces: tuple[Piece, ...] = (),
        title: str = "",
        summary: str = "",
        steps: list[str] | None = None,
        written_by: Literal["claude", "plain", ""] = "",
    ) -> Outcome:
        outcome = self.outcome
        outcome.status, outcome.code = status, code
        outcome.pieces, outcome.title, outcome.summary = pieces, title, summary
        outcome.steps, outcome.written_by = steps or [], written_by
        outcome.seconds = self.agent.clock() - self.started
        return outcome
