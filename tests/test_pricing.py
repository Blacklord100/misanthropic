import pytest

from misanthropic import pricing


@pytest.mark.parametrize("model,tier", [
    ("opus", "opus"),
    ("claude-opus-4-1", "opus"),
    ("sonnet", "sonnet"),
    ("claude-3-5-sonnet-20241022", "sonnet"),
    ("haiku", "haiku"),
    ("gpt-4", pricing.DEFAULT_TIER),  # unknown -> default tier
])
def test_tier_for(model, tier):
    assert pricing.tier_for(model) == tier


def test_estimated_cost_opus_one_million_each():
    # opus list price: $5 in / $25 out per 1M tokens
    assert pricing.estimated_cost("opus", 1_000_000, 1_000_000) == pytest.approx(30.0)


def test_estimated_cost_counts_web_search():
    # 1000 web searches at $10/1k = $10, on top of token cost
    cost = pricing.estimated_cost("haiku", 0, 0, web_search_requests=1000)
    assert cost == pytest.approx(10.0)


def test_estimated_cost_zero():
    assert pricing.estimated_cost("sonnet", 0, 0) == 0.0
