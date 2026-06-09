from misanthropic import savings, pricing


def test_record_accumulates_all_time_and_month():
    added = savings.record("opus", 1_000_000, 1_000_000)
    assert added == pricing.estimated_cost("opus", 1_000_000, 1_000_000)

    savings.record("sonnet", 1_000_000, 0)  # +$3
    s = savings.summary()
    assert s["all_time_usd"] == 33.0          # 30 (opus) + 3 (sonnet)
    assert s["month_usd"] == 33.0             # both in the current month
    assert s["all_time_requests"] == 2
    assert s["input_tokens"] == 2_000_000
    assert s["output_tokens"] == 1_000_000
    assert s["since"] is not None


def test_record_zero_tokens_is_noop():
    assert savings.record("sonnet", 0, 0) == 0.0
    s = savings.summary()
    assert s["all_time_requests"] == 0
    assert s["all_time_usd"] == 0.0


def test_summary_empty_when_nothing_recorded():
    s = savings.summary()
    assert s["all_time_usd"] == 0.0
    assert s["all_time_requests"] == 0
    assert s["since"] is None


def test_persists_across_reload():
    savings.record("haiku", 1_000_000, 1_000_000)  # $1 + $5 = $6
    # A fresh summary() re-reads the file from disk — simulates a restart.
    assert savings.summary()["all_time_usd"] == 6.0
