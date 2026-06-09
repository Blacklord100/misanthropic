"""Public hosted-API list prices — used only to compute "what you would have paid."

Misanthropic charges nothing; this module exists so the dashboard can show the
counterfactual: the bill the hosted Anthropic API *would* have rung up for the
same tokens. Prices are USD per 1,000,000 tokens (input, output), keyed by the
Claude tier the request resolved to (opus / sonnet / haiku).

These are list prices, updated by hand — they're for a feel-good running total,
not an invoice. If Anthropic changes pricing, edit PRICES.
"""

from . import claude

# USD per 1M tokens: (input, output). Source: public Anthropic API pricing.
PRICES = {
    "opus": (5.0, 25.0),
    "sonnet": (3.0, 15.0),
    "haiku": (1.0, 5.0),
}
DEFAULT_TIER = "sonnet"

# Hosted web search bills per 1,000 searches (server tool). Counted when known.
WEB_SEARCH_USD_PER_1K = 10.0


def tier_for(model):
    """Resolve a requested model id to the pricing tier it maps to on the CLI."""
    tier = (claude.cli_model(model) or "").lower().split("[", 1)[0]
    return tier if tier in PRICES else DEFAULT_TIER


def estimated_cost(model, input_tokens=0, output_tokens=0, web_search_requests=0,
                   cache_write_tokens=0, cache_read_tokens=0):
    """What the hosted API would have charged for this generation, in USD.

    Claude Code auto-caches large prompts, so the CLI reports most prompt tokens
    under cache_creation / cache_read rather than input_tokens. A hosted-API user
    *without* caching pays full input price for all of them, so we count every
    prompt token (input + cache write + cache read) at the input rate — the honest
    "you'd have paid" figure.
    """
    pin, pout = PRICES[tier_for(model)]
    prompt = (input_tokens or 0) + (cache_write_tokens or 0) + (cache_read_tokens or 0)
    usd = prompt / 1e6 * pin + (output_tokens or 0) / 1e6 * pout
    usd += (web_search_requests or 0) / 1000.0 * WEB_SEARCH_USD_PER_1K
    return usd
