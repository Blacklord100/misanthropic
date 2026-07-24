"""Parallel load-balancing: spread concurrent runs across eligible accounts.

Two layers:
  * unit — accounts.reserve/release/balanced_order/inflight_counts pick the
    least-loaded account, rotate on ties, honor pins, and re-pick on failover.
  * contract — with the shipped "balanced" default and failover on, sequential
    requests actually land on different accounts (strict failover would pin
    them all to #1).
"""

import json
import threading

import pytest

from misanthropic import accounts, history, server, settings


def _registry(tmp_path, *ids, pinned=None, backend="claude"):
    """Write an accounts.json with healthy accounts named by id."""
    accs = [{"id": aid, "label": aid.upper(), "backend": backend,
             "auth": {"kind": "config_dir", "path": str(tmp_path / f"healthy-{aid}")},
             "priority": i, "enabled": True} for i, aid in enumerate(ids)]
    (tmp_path / "accounts.json").write_text(json.dumps(
        {"version": 1, "pinned": pinned, "accounts": accs}))


# ---- unit: reserve / release / counts ----------------------------------------

def test_reserve_spreads_without_release(tmp_path):
    """Reserving three slots across three accounts hands out one each — no
    account is loaded twice while another sits idle (the anti-herd property)."""
    _registry(tmp_path, "a", "b", "c")
    picked = [accounts.reserve({"text": True})["id"] for _ in range(3)]
    assert sorted(picked) == ["a", "b", "c"]
    assert accounts.inflight_counts() == {"a": 1, "b": 1, "c": 1}


def test_reserve_prefers_least_loaded(tmp_path):
    """A loaded account is skipped in favor of an idle one."""
    _registry(tmp_path, "a", "b")
    accounts.acquire_inflight("a")            # a is busy
    assert accounts.reserve({"text": True})["id"] == "b"


def test_reserve_round_robins_across_sequential_requests(tmp_path):
    """Reserve+release in a loop (each run finishes before the next) alternates
    accounts rather than always picking priority #1."""
    _registry(tmp_path, "a", "b")
    seen = []
    for _ in range(4):
        acc = accounts.reserve({"text": True})
        seen.append(acc["id"])
        accounts.release(acc["id"])
    assert seen.count("a") == 2 and seen.count("b") == 2   # even split
    assert accounts.inflight_counts() == {}                # all released


def test_concurrent_reserves_do_not_stampede(tmp_path):
    """Many threads reserving at once end up evenly spread (select+increment is
    atomic under one lock, re-reading live counts)."""
    _registry(tmp_path, "a", "b", "c", "d")
    out = []
    lock = threading.Lock()

    def grab():
        acc = accounts.reserve({"text": True})
        with lock:
            out.append(acc["id"])

    threads = [threading.Thread(target=grab) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # 12 reservations over 4 accounts, none released -> 3 each, perfectly even.
    assert accounts.inflight_counts() == {"a": 3, "b": 3, "c": 3, "d": 3}
    assert len(out) == 12


def test_reserve_honors_pin(tmp_path):
    """An explicit pin overrides load: the pinned account serves even when it's
    the busiest."""
    _registry(tmp_path, "a", "b", pinned="a")
    accounts.acquire_inflight("a")            # a is busy, but pinned
    assert accounts.reserve({"text": True})["id"] == "a"


def test_reserve_excludes_and_repicks(tmp_path):
    """Failover: excluding the tried account makes reserve pick the next."""
    _registry(tmp_path, "a", "b")
    first = accounts.reserve({"text": True})
    second = accounts.reserve({"text": True}, exclude={first["id"]})
    assert second["id"] != first["id"]


def test_reserve_single_account_degrades_to_it(tmp_path):
    _registry(tmp_path, "solo")
    assert accounts.reserve({"text": True})["id"] == "solo"


def test_reserve_none_when_all_excluded(tmp_path):
    _registry(tmp_path, "a", "b")
    assert accounts.reserve({"text": True}, exclude={"a", "b"}) is None


def test_reserve_skips_cooling_account(tmp_path):
    """A rate-limited account is not eligible, so it's never reserved."""
    _registry(tmp_path, "a", "b")
    accounts.report_limited("a", "usage limit")
    assert accounts.reserve({"text": True})["id"] == "b"
    assert accounts.reserve({"text": True}, exclude={"b"}) is None   # a still cooling


def test_release_bookkeeping(tmp_path):
    _registry(tmp_path, "a")
    accounts.acquire_inflight("a")
    accounts.acquire_inflight("a")
    assert accounts.inflight_counts() == {"a": 2}
    accounts.release("a")
    assert accounts.inflight_counts() == {"a": 1}
    accounts.release("a")
    assert accounts.inflight_counts() == {}                # zero drops the key
    accounts.release("a")                                  # underflow is a no-op
    assert accounts.inflight_counts() == {}


# ---- unit: balanced_order ----------------------------------------------------

def test_balanced_order_least_loaded_first(tmp_path):
    _registry(tmp_path, "a", "b", "c")
    accounts.acquire_inflight("a")
    accounts.acquire_inflight("a")
    accounts.acquire_inflight("b")
    order = [a["id"] for a in accounts.balanced_order({"text": True})]
    assert order[0] == "c"                    # idle account leads
    assert order[-1] == "a"                   # busiest trails


def test_balanced_order_keeps_pin_first(tmp_path):
    _registry(tmp_path, "a", "b", pinned="b")
    accounts.acquire_inflight("b")            # busy, but pinned
    order = [a["id"] for a in accounts.balanced_order({"text": True})]
    assert order[0] == "b"


# ---- unit: governor autoscaling ----------------------------------------------

def test_autoscale_scales_with_account_count(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "_governor", server.Governor(8))
    _registry(tmp_path, "a", "b", "c")
    assert server.autoscale_concurrency() == 24        # 8 * 3
    assert server._governor.limit == 24


def test_autoscale_clamps_to_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "_governor", server.Governor(8))
    _registry(tmp_path, "a", "b", "c", "d", "e")       # 8 * 5 = 40
    assert server.autoscale_concurrency() == server.MAX_CONCURRENCY_CAP == 30


def test_autoscale_single_account_is_base(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "_governor", server.Governor(8))
    _registry(tmp_path, "solo")
    assert server.autoscale_concurrency() == 8


# ---- contract: balanced dispatch spreads real requests -----------------------

anthropic = pytest.importorskip("anthropic")

from misanthropic import claude as claude_mod          # noqa: E402
from tests.test_contract import FAKE_CLAUDE            # noqa: E402


@pytest.fixture()
def live_server(tmp_path, monkeypatch):
    fake = tmp_path / "claude"
    fake.write_text(FAKE_CLAUDE)
    fake.chmod(0o755)
    monkeypatch.setattr(claude_mod, "CLAUDE_BIN", str(fake))
    monkeypatch.setattr(claude_mod, "_resolved_claude", None)
    httpd = server.make_httpd("127.0.0.1", 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture()
def client(live_server):
    return anthropic.Anthropic(base_url=live_server, api_key="unused", max_retries=0)


def test_balanced_spreads_across_accounts(tmp_path, client):
    """Two healthy accounts, failover on, default (balanced) strategy: a series
    of requests lands on BOTH accounts. Strict failover would pin them all to
    the priority-#1 account."""
    settings.update({"failover_policy": "auto"})          # balanced is the default
    _registry(tmp_path, "a", "b")
    for _ in range(6):
        client.messages.create(model="claude-sonnet-4-6", max_tokens=64,
                                messages=[{"role": "user", "content": "ping"}])
    served = {row["account"] for row in history.recent(limit=6)}
    assert served == {"A", "B"}                           # both carried load


def test_failover_strategy_pins_to_first(tmp_path, client):
    """The opt-out: with strategy 'failover', every request rides priority #1
    until it hits a limit."""
    settings.update({"failover_policy": "auto", "dispatch_strategy": "failover"})
    _registry(tmp_path, "a", "b")
    for _ in range(4):
        client.messages.create(model="claude-sonnet-4-6", max_tokens=64,
                                messages=[{"role": "user", "content": "ping"}])
    served = {row["account"] for row in history.recent(limit=4)}
    assert served == {"A"}                                # all on #1
