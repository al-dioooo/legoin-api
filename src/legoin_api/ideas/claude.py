"""Ideas written by Claude, through Anthropic's Messages API (ADR-0009).

One request for ideas is a short conversation:

1. Claude gets the cached system prompt and the request (`prompt.py`), and answers in the
   structured-output shape `IDEAS_SCHEMA`. It may call `cek_inventori`, a strict tool, for
   at most `MAX_TOOL_ROUNDS` rounds; the last request then offers no tools.
2. Each build is checked: its slot's theme, a title, a summary, steps, and only catalogue
   parts and colors with quantities of 1 or more. Builds that fail get one repair request.
3. Slots still empty are filled from the templates.

A timeout, an API error, a refusal, a cut-off answer or output that isn't the ideas JSON
gives the whole set from the templates instead, and the reason is logged. The service around
it still runs `cek_inventori` on every final build, as with any generator.

The API key is read by the SDK from `ANTHROPIC_API_KEY` and never touched or logged here.
"""

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field

import anthropic
from pydantic import BaseModel, ValidationError

from legoin_api.catalogue import COLOR_IDS, PART_IDS
from legoin_api.ideas.generator import (
    CEK_INVENTORI_TOOL,
    IdeaGenerator,
    IdeaRequest,
    ProposedIdea,
    TemplateIdeaGenerator,
    run_cek_inventori_tool,
)
from legoin_api.ideas.prompt import IDEAS_SCHEMA, SYSTEM_PROMPT, repair_prompt, user_prompt
from legoin_api.inventory import merge
from legoin_api.schemas import IdeaTheme, PartCount

log = logging.getLogger(__name__)

REQUEST_TIMEOUT_S = 20.0
"""The SDK's timeout for one API request. With one retry, the overall budget still bounds it."""

MAX_RETRIES = 1
MAX_TOKENS = 8000
"""Room for adaptive thinking and three builds; an answer cut off here falls back."""

MAX_TOOL_ROUNDS = 3
MAX_STEPS = 8
MAX_QUANTITY = 50

FALLBACK_BETA = "server-side-fallback-2026-07-01"
"""Server-side fallbacks: a request Claude declines is re-run on the model Anthropic
recommends for that kind of refusal, inside the same call."""

CEK_INVENTORI = {**CEK_INVENTORI_TOOL, "strict": True}


class ClaudePart(BaseModel):
    part: str
    color: str
    quantity: int


class ClaudeIdea(BaseModel):
    theme: str
    difficulty: str
    title: str
    summary: str
    steps: list[str]
    parts: list[ClaudePart]


class ClaudeIdeas(BaseModel):
    ideas: list[ClaudeIdea]


class Unusable(Exception):
    """Claude gave no usable answer; the reason is logged and the templates answer."""


@dataclass
class Usage:
    """What one request for ideas cost, summed over its API requests."""

    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    models: set[str] = field(default_factory=set)

    def add(self, response) -> None:
        usage = response.usage
        self.requests += 1
        self.input_tokens += usage.input_tokens or 0
        self.output_tokens += usage.output_tokens or 0
        self.cache_read_tokens += usage.cache_read_input_tokens or 0
        self.cache_write_tokens += usage.cache_creation_input_tokens or 0
        self.models.add(response.model)


def problems(idea: ClaudeIdea, theme: IdeaTheme) -> list[str]:
    """Why a build can't be used in its slot; empty if it can."""
    found = []
    if idea.theme != theme:
        found.append(f"its theme must be {theme}")
    if not (idea.title.strip() and idea.summary.strip()):
        found.append("it needs a title and a summary")
    if not 1 <= len(idea.steps) <= MAX_STEPS or not all(step.strip() for step in idea.steps):
        found.append(f"it needs 1 to {MAX_STEPS} steps")
    if not idea.parts:
        found.append("it lists no parts")
    for line in idea.parts:
        if line.part not in PART_IDS:
            found.append(f"part {line.part!r} isn't in the catalogue")
        if line.color not in COLOR_IDS:
            found.append(f"color {line.color!r} isn't in the catalogue")
        if not 1 <= line.quantity <= MAX_QUANTITY:
            found.append(f"{line.part} {line.color} needs a quantity from 1 to {MAX_QUANTITY}")
    return found


def to_proposal(idea: ClaudeIdea, theme: IdeaTheme) -> ProposedIdea:
    slug = re.sub(r"[^a-z0-9]+", "-", idea.title.casefold()).strip("-")
    return ProposedIdea(
        key=f"claude:{slug}",
        theme=theme,
        difficulty=idea.difficulty,
        title=idea.title.strip(),
        summary=idea.summary.strip(),
        steps=[step.strip() for step in idea.steps],
        requires=merge(PartCount.model_validate(line.model_dump()) for line in idea.parts),
    )


def tool_result(block, request: IdeaRequest) -> dict:
    """Answers one `tool_use` block; a bad call gets an error result Claude can act on."""
    result: dict = {"type": "tool_result", "tool_use_id": block.id}
    if block.name != CEK_INVENTORI["name"]:
        return result | {"content": f"There's no tool {block.name}.", "is_error": True}
    try:
        answer = run_cek_inventori_tool(block.input, request.inventory)
    except (ValueError, TypeError, KeyError) as error:
        message = f"Each line needs a catalogue part, a color and a quantity of 1 or more: {error}"
        return result | {"content": message, "is_error": True}
    return result | {"content": json.dumps(answer, sort_keys=True)}


class AnthropicIdeaGenerator:
    """Claude writes the ideas; the templates answer whenever it can't."""

    def __init__(
        self,
        client: anthropic.AsyncAnthropic,
        *,
        model: str,
        effort: str,
        budget_s: float,
        templates: IdeaGenerator | None = None,
    ):
        self.client = client
        self.model = model
        self.effort = effort
        self.budget_s = budget_s
        """How long Claude may take in all, leaving the templates time within the deadline."""
        self.templates = templates or TemplateIdeaGenerator()

    async def aclose(self) -> None:
        await self.client.close()

    async def propose(self, request: IdeaRequest) -> list[ProposedIdea]:
        usage = Usage()
        started = time.perf_counter()
        try:
            async with asyncio.timeout(self.budget_s):
                ideas = await self._write(request, usage)
        except TimeoutError:
            return await self._from_templates(request, f"no answer in {self.budget_s:g} s")
        except anthropic.APIStatusError as error:
            reason = f"{type(error).__name__} {error.status_code}, request {error.request_id}"
            return await self._from_templates(request, reason)
        except anthropic.APIError as error:
            return await self._from_templates(request, type(error).__name__)
        except Unusable as error:
            return await self._from_templates(request, str(error))
        log.info(
            "Claude wrote %d ideas in %.1f s: %d requests, %s, %d input, %d cache read, "
            "%d cache write, %d output tokens",
            sum(idea is not None for idea in ideas),
            time.perf_counter() - started,
            usage.requests,
            "/".join(sorted(usage.models)),
            usage.input_tokens,
            usage.cache_read_tokens,
            usage.cache_write_tokens,
            usage.output_tokens,
        )
        missing = [slot for slot, idea in enumerate(ideas) if idea is None]
        if missing:
            log.warning("Builds %s from the templates: still invalid after a repair", missing)
            filled = await self.templates.propose(
                IdeaRequest(
                    themes=[request.themes[slot] for slot in missing],
                    locale=request.locale,
                    inventory=request.inventory,
                    excluded=request.excluded,
                    shown=request.shown,
                )
            )
            for slot, idea in zip(missing, filled, strict=True):
                ideas[slot] = idea
        return [idea for idea in ideas if idea is not None]

    async def _from_templates(self, request: IdeaRequest, reason: str) -> list[ProposedIdea]:
        log.warning("Ideas from the templates instead of Claude: %s", reason)
        return await self.templates.propose(request)

    async def _write(self, request: IdeaRequest, usage: Usage) -> list[ProposedIdea | None]:
        messages: list[dict] = [{"role": "user", "content": user_prompt(request)}]
        answer = await self._converse(messages, request, usage, tool_rounds=MAX_TOOL_ROUNDS)
        ideas, found = self._check(answer, request.themes)
        if found:
            log.info("Asking Claude to repair builds %s: %s", sorted(found), found)
            messages.append({"role": "user", "content": repair_prompt(found)})
            answer = await self._converse(messages, request, usage, tool_rounds=0)
            repaired, _ = self._check(answer, request.themes)
            for slot in found:
                ideas[slot] = repaired[slot]
        return ideas

    def _check(
        self, answer: ClaudeIdeas, themes: list[IdeaTheme]
    ) -> tuple[list[ProposedIdea | None], dict[int, list[str]]]:
        ideas: list[ProposedIdea | None] = []
        found: dict[int, list[str]] = {}
        for slot, theme in enumerate(themes):
            if slot >= len(answer.ideas):
                ideas.append(None)
                found[slot] = ["it's missing from the answer"]
                continue
            if wrong := problems(answer.ideas[slot], theme):
                ideas.append(None)
                found[slot] = wrong
            else:
                ideas.append(to_proposal(answer.ideas[slot], theme))
        return ideas, found

    async def _converse(
        self, messages: list[dict], request: IdeaRequest, usage: Usage, *, tool_rounds: int
    ) -> ClaudeIdeas:
        """Runs the conversation until Claude answers, adding each turn to `messages`."""
        for turn in range(tool_rounds + 1):
            response = await self._create(messages, tools=turn < tool_rounds)
            usage.add(response)
            # The whole turn goes back, thinking blocks included, as the API expects.
            messages.append({"role": "assistant", "content": response.content})
            if response.stop_reason == "refusal":
                details = response.stop_details
                raise Unusable(f"refused ({details.category if details else 'no category'})")
            if response.stop_reason == "max_tokens":
                raise Unusable(f"answer cut off at {MAX_TOKENS} tokens")
            if response.stop_reason == "tool_use":
                calls = [block for block in response.content if block.type == "tool_use"]
                messages.append(
                    {"role": "user", "content": [tool_result(call, request) for call in calls]}
                )
                continue
            if response.stop_reason != "end_turn":
                raise Unusable(f"stopped with {response.stop_reason}")
            text = "".join(block.text for block in response.content if block.type == "text")
            try:
                return ClaudeIdeas.model_validate_json(text)
            except ValidationError as error:
                count = error.error_count()
                raise Unusable(f"the answer isn't the ideas JSON ({count} errors)") from error
        raise Unusable(f"still calling tools after {tool_rounds} rounds")

    async def _create(self, messages: list[dict], *, tools: bool):
        return await self.client.beta.messages.create(
            model=self.model,
            max_tokens=MAX_TOKENS,
            # The stable part, cached; tools render before it and never change either.
            system=[
                {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
            ],
            messages=messages,
            tools=[CEK_INVENTORI],
            # Forced tool use is refused on Sonnet 5.5 and Opus 5.5, so Claude decides; the last
            # request offers no tools, so it has to answer.
            tool_choice={"type": "auto"} if tools else {"type": "none"},
            thinking={"type": "adaptive"},
            output_config={
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": IDEAS_SCHEMA},
            },
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )


def anthropic_generator(model: str, effort: str, budget_s: float) -> AnthropicIdeaGenerator:
    """The generator on a real client, which reads `ANTHROPIC_API_KEY` from the environment."""
    client = anthropic.AsyncAnthropic(timeout=REQUEST_TIMEOUT_S, max_retries=MAX_RETRIES)
    return AnthropicIdeaGenerator(client, model=model, effort=effort, budget_s=budget_s)
