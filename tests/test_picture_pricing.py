from types import SimpleNamespace

import pytest

from legoin_api.picture.pricing import cost_of, worst_case


def usage(input=0, output=0, read=0, write=0):
    return SimpleNamespace(
        input_tokens=input,
        output_tokens=output,
        cache_read_input_tokens=read,
        cache_creation_input_tokens=write,
    )


def test_cost_comes_from_usage_and_the_models_prices():
    # Opus 5.5: $4 in, $20 out, $0.20 cache read, $5 cache write, per million tokens.
    cost = cost_of(usage(1_000, 4_000, 10_000, 8_000), "claude-opus-5-5", "claude-opus-5-5")

    assert cost == pytest.approx((4_000 + 80_000 + 2_000 + 40_000) / 1e6)


def test_a_fallback_or_an_unknown_model_is_never_cheaper():
    spent = usage(output=1_000_000)

    assert cost_of(spent, "claude-sonnet-5", "claude-opus-5-5") == pytest.approx(20.0)
    assert cost_of(spent, "claude-new-model", "claude-sonnet-5-5") == pytest.approx(25.0)


def test_the_worst_case_writes_every_input_token_and_uses_every_output_token():
    assert worst_case("claude-sonnet-5-5", 10_000, 8_000) == pytest.approx(
        (10_000 * 2.5 + 8_000 * 10) / 1e6
    )
