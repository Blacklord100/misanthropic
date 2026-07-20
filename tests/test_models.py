"""GET /v1/models: catalog shape, paging, and single-model fetch."""

from misanthropic import models


def test_list_shape():
    out = models.list_models()
    assert out["data"], "catalog must not be empty"
    assert out["first_id"] == out["data"][0]["id"]
    assert out["last_id"] == out["data"][-1]["id"]
    assert out["has_more"] is False
    for m in out["data"]:
        assert m["type"] == "model"
        assert m["id"] and m["display_name"] and m["created_at"]


def test_every_catalog_id_maps_to_a_cli_tier():
    # The catalog's promise: every listed id resolves to a real tier, never the
    # unknown-model fallback masquerading as support.
    from misanthropic.claude import _MODEL_ALIASES, cli_model
    for m in models.MODELS:
        assert cli_model(m["id"]) in _MODEL_ALIASES


def test_paging_limit_and_after():
    first = models.list_models(limit=2)
    assert len(first["data"]) == 2
    assert first["has_more"] is True
    rest = models.list_models(limit=1000, after_id=first["last_id"])
    assert rest["data"][0]["id"] == models.MODELS[2]["id"]
    assert rest["has_more"] is False
    assert len(first["data"]) + len(rest["data"]) == len(models.MODELS)


def test_paging_before():
    third = models.MODELS[2]["id"]
    page = models.list_models(limit=1, before_id=third)
    assert [m["id"] for m in page["data"]] == [models.MODELS[1]["id"]]
    assert page["has_more"] is True


def test_paging_garbage_limit_falls_back():
    assert models.list_models(limit="bogus")["data"] == models.list_models()["data"]


def test_get_model():
    mid = models.MODELS[0]["id"]
    assert models.get_model(mid)["id"] == mid
    assert models.get_model("claude-nonexistent") is None
