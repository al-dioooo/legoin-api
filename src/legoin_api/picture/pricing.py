"""What a Claude call costs, from its real usage and the model's prices.

Prices are dollars per million tokens, from Anthropic's pricing page on 2026-11-25. A response
from a model not listed here, such as a server-side fallback, is priced at `UNKNOWN`, the
dearest of the models a fallback can go to, so spending is never under-counted.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Price:
    input: float
    output: float
    cache_read: float
    cache_write: float
    """A 5-minute cache write, 1.25 × input."""


PRICES = {
    "claude-sonnet-5-5": Price(2.00, 10.00, 0.10, 2.50),
    "claude-opus-5-5": Price(4.00, 20.00, 0.20, 5.00),
    "claude-sonnet-5": Price(2.00, 10.00, 0.20, 2.50),
}
UNKNOWN = Price(5.00, 25.00, 0.50, 6.25)
"""Claude Opus 5's and Opus 4.8's prices."""


def price_of(model: str) -> Price:
    return PRICES.get(model, UNKNOWN)


def cost_of(usage, served_by: str, requested: str) -> float:
    """Dollars for one response. A response served by another model than the one requested
    is priced at the dearer of the two."""
    price = price_of(served_by)
    if served_by != requested:
        asked = price_of(requested)
        price = price if price.output >= asked.output else asked
    return (
        (usage.input_tokens or 0) * price.input
        + (usage.output_tokens or 0) * price.output
        + (usage.cache_read_input_tokens or 0) * price.cache_read
        + (usage.cache_creation_input_tokens or 0) * price.cache_write
    ) / 1_000_000


def worst_case(model: str, input_tokens: int, max_tokens: int) -> float:
    """The most a call to `model` could cost: every input token written to the cache, and every
    output token used. (A fallback answers only a declined request, and its real cost is still
    counted afterwards.)"""
    price = price_of(model)
    per_input = max(price.input, price.cache_write)
    return (input_tokens * per_input + max_tokens * price.output) / 1_000_000
