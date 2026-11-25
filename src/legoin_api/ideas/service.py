"""Writing a set of ideas, with the contract's guarantees whatever the generator does.

- three ideas, one per theme slot (one of each theme for `surprise`);
- only catalogue parts and colors: a build that needs anything else is dropped, and the
  generator is asked for another;
- `used` and `missing` always come from `cek_inventori`, run here on each build's final parts
  list against the scan's inventory at the time of the request, never from the generator;
- idea ids unique within the scan, never reused.
"""

from legoin_api.catalogue import COLOR_IDS, PART_IDS
from legoin_api.errors import ApiError
from legoin_api.ideas.generator import IdeaGenerator, IdeaRequest, ProposedIdea, ShownIdea
from legoin_api.ids import new_id
from legoin_api.inventory import cek_inventori, to_inventory
from legoin_api.schemas import Idea, IdeaSet, IdeasRequest, IdeaTheme, PartCount
from legoin_api.store import StoredScan

IDEA_THEMES: list[IdeaTheme] = ["vehicles", "animals", "buildings"]
IDEAS_PER_SET = 3
MAX_ROUNDS = 3
"""How many times the generator is asked to replace builds that were dropped."""


def acceptable(proposal: ProposedIdea, theme: IdeaTheme) -> bool:
    return (
        proposal.theme == theme
        and bool(proposal.title.strip() and proposal.summary.strip() and proposal.steps)
        and bool(proposal.requires)
        and all(
            line.part in PART_IDS and line.color in COLOR_IDS and line.quantity >= 1
            for line in proposal.requires
        )
    )


async def write_ideas(
    generator: IdeaGenerator, scan: StoredScan, body: IdeasRequest, written: list[ShownIdea]
) -> tuple[IdeaSet, list[str], list[PartCount]]:
    """The new set, each idea's generator key, and the inventory the ideas were checked against."""
    inventory = to_inventory(scan.detections)
    if not inventory:
        raise ApiError("no_pieces", f"Scan {scan.id} has no pieces to build with.")
    themes = IDEA_THEMES if body.theme == "surprise" else [body.theme] * IDEAS_PER_SET
    by_id = {idea.id: idea for idea in written}
    # Ids the backend doesn't know are ignored.
    excluded = [by_id[idea_id] for idea_id in body.exclude if idea_id in by_id]
    taken = set(by_id)

    ideas: list[Idea | None] = [None] * len(themes)
    keys: list[str] = [""] * len(themes)
    for _ in range(MAX_ROUNDS):
        wanted = [slot for slot, idea in enumerate(ideas) if idea is None]
        if not wanted:
            break
        proposals = await generator.propose(
            IdeaRequest(
                themes=[themes[slot] for slot in wanted],
                locale=body.locale,
                inventory=inventory,
                excluded=excluded,
                shown=written,
            )
        )
        for slot, proposal in zip(wanted, proposals, strict=False):
            if not acceptable(proposal, themes[slot]):
                continue
            while (idea_id := new_id("idea", 6)) in taken:
                pass
            taken.add(idea_id)
            used, missing = cek_inventori(proposal.requires, inventory)
            ideas[slot] = Idea(
                id=idea_id,
                theme=proposal.theme,
                difficulty=proposal.difficulty,
                title=proposal.title,
                summary=proposal.summary,
                steps=proposal.steps,
                used=used,
                missing=missing,
                build_id=proposal.build_id,
            )
            keys[slot] = proposal.key
    if any(idea is None for idea in ideas):
        raise ApiError("unavailable", "Couldn't write three ideas from the catalogue's parts.")
    idea_set = IdeaSet(theme=body.theme, locale=body.locale, items=[i for i in ideas if i])
    return idea_set, keys, inventory
