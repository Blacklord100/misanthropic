"""history.analytics(): the Analytics page's aggregation."""

from misanthropic import history


def test_analytics_aggregates():
    history.append({"model": "claude-sonnet-4-6", "mode": "stateless", "status": 200,
                    "duration_ms": 100, "input_tokens": 10, "output_tokens": 5,
                    "cache_read": 30, "account": "A", "backend": "claude",
                    "stream": True})
    history.append({"model": "codex:default-model", "mode": "stateless", "status": 529,
                    "duration_ms": 300, "account": "B", "backend": "codex",
                    "error": "limited"})
    a = history.analytics(7)
    t = a["totals"]
    assert t["requests"] == 2 and t["errors"] == 1
    assert t["error_rate"] == 0.5
    assert t["stream_share"] == 0.5
    assert t["p95_ms"] == 300
    assert 0 < t["cache_read_share"] <= 1
    assert a["by_account"]["A"]["requests"] == 1
    assert a["by_account"]["A"]["usd"] > 0          # priced 200s count
    assert a["by_account"]["B"]["errors"] == 1
    assert a["by_model"]["claude-sonnet-4-6"]["output_tokens"] == 5
    assert a["by_backend"]["codex"]["requests"] == 1
    assert len(a["series"]) == 7
    assert a["series"][-1]["requests"] == 2         # both rows landed today


def test_analytics_empty_window():
    a = history.analytics(7)
    assert a["totals"]["requests"] == 0
    assert a["totals"]["error_rate"] == 0
    assert len(a["series"]) == 7
    assert a["by_account"] == {}
