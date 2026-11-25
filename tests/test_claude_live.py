"""One real request for ideas to Claude. CI has no key, so it's skipped there.

Run it with the key in the environment, never on the command line:

    uv run --env-file ~/credentials/legoin/anthropic.env pytest tests/test_claude_live.py
"""

import asyncio
import os

import pytest

from legoin_api.catalogue import COLOR_IDS, PART_IDS
from legoin_api.config import Settings
from legoin_api.ideas.claude import anthropic_generator
from legoin_api.ideas.generator import IdeaRequest
from legoin_api.schemas import PartCount

pytestmark = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"), reason="needs ANTHROPIC_API_KEY"
)

# The contract's corrected scan: four pieces.
INVENTORY = [
    PartCount(part="brick-2x2", color="black", quantity=1),
    PartCount(part="brick-2x4", color="red", quantity=1),
    PartCount(part="plate-1x2", color="blue", quantity=1),
    PartCount(part="slope-2x2", color="green", quantity=1),
]


def test_claude_writes_three_indonesian_animals_from_the_catalogue(caplog):
    settings = Settings()
    generator = anthropic_generator(settings.ideas_model, settings.ideas_effort, budget_s=24)
    request = IdeaRequest(
        themes=["animals"] * 3, locale="id", inventory=INVENTORY, excluded=[], shown=[]
    )

    async def ask():
        try:
            return await generator.propose(request)
        finally:
            await generator.aclose()

    ideas = asyncio.run(ask())

    assert len(ideas) == 3
    assert all(idea.key.startswith("claude:") for idea in ideas), caplog.text
    for idea in ideas:
        assert idea.theme == "animals"
        assert idea.title and idea.summary and idea.steps
        assert all(line.part in PART_IDS and line.color in COLOR_IDS for line in idea.requires)
    assert len({idea.title for idea in ideas}) == 3
