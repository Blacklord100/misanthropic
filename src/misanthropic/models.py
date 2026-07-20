"""GET /v1/models — a static catalog shaped like the hosted API's model list.

Many SDK tools probe /v1/models on startup to populate a model picker or verify
connectivity; without this endpoint they 404 and bail before the first request.
The catalog lists the canonical Anthropic ids this proxy understands — every id
here maps onto a Claude Code tier via claude.cli_model() (and anything NOT here
still works at request time; unknown ids fall back to the default tier).

Hand-maintained, like pricing.PRICES: when Anthropic ships a model, add a row.
Ordered newest-first, matching the hosted list.
"""


def _model(mid, name, created):
    return {"type": "model", "id": mid, "display_name": name, "created_at": created}


MODELS = [
    _model("claude-opus-4-8", "Claude Opus 4.8", "2026-05-15T00:00:00Z"),
    _model("claude-sonnet-4-6", "Claude Sonnet 4.6", "2026-02-19T00:00:00Z"),
    _model("claude-haiku-4-5-20251001", "Claude Haiku 4.5", "2025-10-01T00:00:00Z"),
    _model("claude-opus-4-1", "Claude Opus 4.1", "2025-08-05T00:00:00Z"),
    _model("claude-sonnet-4-20250514", "Claude Sonnet 4", "2025-05-14T00:00:00Z"),
    _model("claude-3-7-sonnet-20250219", "Claude Sonnet 3.7", "2025-02-19T00:00:00Z"),
    _model("claude-3-5-sonnet-20241022", "Claude Sonnet 3.5 (New)", "2024-10-22T00:00:00Z"),
    _model("claude-3-5-haiku-20241022", "Claude Haiku 3.5", "2024-10-22T00:00:00Z"),
]

_BY_ID = {m["id"]: m for m in MODELS}


def get_model(model_id):
    """The catalog entry for `model_id`, or None."""
    return _BY_ID.get(model_id)


def list_models(limit=20, before_id=None, after_id=None):
    """Build the hosted API's list envelope over the static catalog.

    Paging mirrors api.anthropic.com just enough for SDK iterators to
    terminate: `after_id` returns entries after that id, `before_id` before it,
    `limit` caps the page (clamped to 1..1000).
    """
    try:
        limit = max(1, min(int(limit), 1000))
    except (TypeError, ValueError):
        limit = 20
    ids = [m["id"] for m in MODELS]
    if after_id in _BY_ID:
        rest = MODELS[ids.index(after_id) + 1:]
        page = rest[:limit]
    elif before_id in _BY_ID:
        rest = MODELS[:ids.index(before_id)]
        page = rest[-limit:]
    else:
        rest = MODELS
        page = rest[:limit]
    return {
        "data": page,
        "first_id": page[0]["id"] if page else None,
        "last_id": page[-1]["id"] if page else None,
        "has_more": len(rest) > len(page),
    }
