"""What writes ideas: the `IdeaGenerator` interface, and the template generator.

A generator proposes builds, each with the full list of parts it needs. It doesn't decide
`used` and `missing`: the service runs `cek_inventori` on every proposal itself, so the
contract's guarantee holds whatever the generator does. An LLM provider plugs in here,
using `CEK_INVENTORI_TOOL` for the tool call the PRD asks the agent to make.
"""

from dataclasses import dataclass
from typing import Protocol

from legoin_api.catalogue import COLOR_IDS, PART_IDS
from legoin_api.ideas.templates import TEMPLATES
from legoin_api.inventory import cek_inventori
from legoin_api.schemas import Difficulty, IdeaTheme, Locale, PartCount


@dataclass(frozen=True)
class ProposedIdea:
    key: str
    """Stays the same for the same build, so the backend can tell what was already shown."""
    theme: IdeaTheme
    difficulty: Difficulty
    title: str
    summary: str
    steps: list[str]
    requires: list[PartCount]
    """Every part the build needs, owned or not."""


@dataclass(frozen=True)
class ShownIdea:
    """An idea already written for this scan."""

    id: str
    key: str
    theme: IdeaTheme
    title: str


@dataclass(frozen=True)
class IdeaRequest:
    themes: list[IdeaTheme]
    """One theme per idea wanted, in order: the same theme three times, or one of each."""
    locale: Locale
    inventory: list[PartCount]
    excluded: list[ShownIdea]
    """Ideas the person has seen ("Try other ideas"), oldest first. Write different ones."""
    shown: list[ShownIdea]
    """Every idea written for this scan so far, oldest first."""


class IdeasUnavailable(Exception):
    """The generator's provider is down or overloaded: the API answers `503 unavailable`."""


class IdeaGenerator(Protocol):
    async def propose(self, request: IdeaRequest) -> list[ProposedIdea]:
        """One idea per theme in `request.themes`, in the same order."""
        ...


CEK_INVENTORI_TOOL = {
    "name": "cek_inventori",
    "description": (
        "Checks a build's full parts list against the person's pieces, and answers which "
        "parts they own (used) and which they don't (missing). Call it for every build you "
        "propose, with every part the build needs. Only these part and color ids exist."
    ),
    "input_schema": {
        "type": "object",
        "required": ["parts"],
        "properties": {
            "parts": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "required": ["part", "color", "quantity"],
                    "properties": {
                        "part": {"type": "string", "enum": list(PART_IDS)},
                        "color": {"type": "string", "enum": list(COLOR_IDS)},
                        "quantity": {"type": "integer", "minimum": 1},
                    },
                },
            }
        },
    },
}
"""The tool an LLM agent calls, in the JSON Schema shape tool-calling APIs take."""


def run_cek_inventori_tool(arguments: dict, inventory: list[PartCount]) -> dict:
    """Answers the agent's tool call. The service still checks the final list itself."""
    required = [PartCount.model_validate(line) for line in arguments["parts"]]
    used, missing = cek_inventori(required, inventory)
    return {"used": [line.wire() for line in used], "missing": [line.wire() for line in missing]}


@dataclass(frozen=True)
class Template:
    id: str
    theme: IdeaTheme
    difficulty: Difficulty
    title: dict[Locale, str]
    summary: dict[Locale, str]
    steps: dict[Locale, list[str]]
    requires: list[PartCount]

    @classmethod
    def from_dict(cls, data: dict) -> "Template":
        return cls(**data | {"requires": [PartCount(**line) for line in data["requires"]]})

    def propose(self, locale: Locale) -> ProposedIdea:
        return ProposedIdea(
            key=f"template:{self.id}",
            theme=self.theme,
            difficulty=self.difficulty,
            title=self.title[locale],
            summary=self.summary[locale],
            steps=list(self.steps[locale]),
            requires=list(self.requires),
        )


class TemplateIdeaGenerator:
    """Picks from legoin-web's templates; needs no provider and no key.

    For each theme it prefers, in order: templates not excluded; templates not yet shown for
    this scan, then the ones shown longest ago; templates that use more of the person's
    pieces; fewer missing pieces; the templates' own order. Only six exist per theme, so once
    every one is excluded it offers the ones shown longest ago again.
    """

    def __init__(self, templates: list[dict] = TEMPLATES):
        self.templates = [Template.from_dict(t) for t in templates]

    async def propose(self, request: IdeaRequest) -> list[ProposedIdea]:
        excluded = {idea.key for idea in request.excluded}
        last_shown = {idea.key: order for order, idea in enumerate(request.shown)}
        chosen: set[str] = set()

        def rank(item: tuple[int, Template]):
            order, template = item
            key = f"template:{template.id}"
            used, missing = cek_inventori(template.requires, request.inventory)
            return (
                key in excluded,
                key in last_shown,
                last_shown.get(key, -1),
                -sum(line.quantity for line in used),
                sum(line.quantity for line in missing),
                order,
            )

        ideas = []
        for theme in request.themes:
            candidates = [
                (order, t)
                for order, t in enumerate(self.templates)
                if t.theme == theme and t.id not in chosen
            ]
            _, best = min(candidates, key=rank)
            chosen.add(best.id)
            ideas.append(best.propose(request.locale))
        return ideas
