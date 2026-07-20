"""Unit tests for the account registry, eligibility routing, and cooldowns."""

import time

import pytest

from misanthropic import accounts


def test_implicit_default_account():
    accs = accounts.list_accounts()
    assert len(accs) == 1
    assert accs[0]["id"] == "claude-default"
    assert accs[0]["backend"] == "claude"
    assert accounts.serving({"text": True})["id"] == "claude-default"


def test_add_and_priority_order(tmp_path):
    a = accounts.add("Claude two", "claude")
    b = accounts.add("Codex one", "codex")
    ids = [x["id"] for x in accounts.list_accounts()]
    assert ids[0] == "claude-default"          # priority 0
    assert set(ids[1:]) == {a["id"], b["id"]}
    # The default claude login is already claimed (implicit account), so a
    # second claude account gets its own config dir; the FIRST codex account
    # claims the user's existing ~/.codex login automatically.
    assert a["auth"]["kind"] == "config_dir"
    assert b["auth"]["kind"] == "codex_default"


def test_second_codex_account_gets_own_home(tmp_path):
    accounts.add("Codex one", "codex")
    c = accounts.add("Codex two", "codex")
    assert c["auth"]["kind"] == "codex_home"
    assert c["auth"]["path"]


def test_registry_file_is_private(tmp_path):
    import os
    accounts.add("x", "claude")
    mode = os.stat(accounts._accounts_path()).st_mode & 0o777
    assert mode == 0o600


def test_backend_capability_gate():
    caps_tools = {"tools": True, "text": True}
    caps_plain = {"text": True, "images": True, "thinking": True}
    assert accounts.backend_supports("claude", caps_tools)
    assert not accounts.backend_supports("codex", caps_tools)
    assert accounts.backend_supports("codex", caps_plain)
    assert accounts.claude_only(caps_tools)
    assert not accounts.claude_only(caps_plain)


def test_eligible_excludes_codex_for_tools():
    accounts.add("Codex", "codex")
    only_claude = accounts.eligible({"tools": True})
    assert all(a["backend"] == "claude" for a in only_claude)
    both = accounts.eligible({"text": True})
    assert {a["backend"] for a in both} == {"claude", "codex"}


def test_cooldown_and_failover_order():
    b = accounts.add("Claude B", "claude")
    order = accounts.eligible({})
    assert order[0]["id"] == "claude-default"
    accounts.report_limited("claude-default", "usage limit reached")
    order = accounts.eligible({})
    assert [a["id"] for a in order] == [b["id"]]
    cds, _ = accounts.cooldown_state()
    assert "claude-default" in cds and cds["claude-default"]["seconds_left"] > 0
    accounts.report_ok("claude-default")
    assert accounts.eligible({})[0]["id"] == "claude-default"


def test_cooldown_escalates():
    accounts.report_limited("claude-default", "rate limit")
    first = accounts._cooldowns["claude-default"]["until"] - time.time()
    accounts.report_limited("claude-default", "rate limit")
    second = accounts._cooldowns["claude-default"]["until"] - time.time()
    assert second > first * 2


def test_explicit_reset_time_wins():
    # "resets at 3pm" should set the cooldown to that wall-clock time.
    secs = accounts._parse_reset_seconds("Limit reached · resets at 3pm",
                                         now=time.mktime((2026, 7, 20, 13, 0, 0, 0, 0, -1)))
    assert secs == pytest.approx(2 * 3600, abs=60)


def test_logged_out_until_ok():
    accounts.mark_logged_out("claude-default", "Not logged in")
    assert accounts.eligible({}) == []
    accounts.report_ok("claude-default")
    assert accounts.eligible({})


def test_set_first_and_move_order():
    b = accounts.add("Claude B", "claude")
    c = accounts.add("Codex C", "codex")
    ids = lambda: [a["id"] for a in accounts.list_accounts()]
    assert ids() == ["claude-default", b["id"], c["id"]]
    accounts.set_first(c["id"])
    assert ids() == [c["id"], "claude-default", b["id"]]
    accounts.move(b["id"], -1)   # up: swaps with claude-default
    assert ids() == [c["id"], b["id"], "claude-default"]
    accounts.move(c["id"], 1)    # down one
    assert ids() == [b["id"], c["id"], "claude-default"]
    accounts.move(b["id"], -1)   # already first: no-op
    assert ids()[0] == b["id"]
    with pytest.raises(KeyError):
        accounts.set_first("nope")


def test_set_first_clears_pin():
    b = accounts.add("Claude B", "claude")
    accounts.set_pinned(b["id"])
    accounts.set_first("claude-default")
    assert accounts.pinned() is None
    assert accounts.eligible({})[0]["id"] == "claude-default"


def test_pin_moves_to_front():
    b = accounts.add("Claude B", "claude")
    accounts.set_pinned(b["id"])
    assert accounts.eligible({})[0]["id"] == b["id"]
    accounts.set_pinned(None)
    assert accounts.eligible({})[0]["id"] == "claude-default"


def test_classify_strictness():
    assert accounts.classify("claude", "You've reached your usage limit") == "limit"
    assert accounts.classify("claude", "429 too many requests") == "limit"
    assert accounts.classify("claude", "Not logged in · Please run /login") == "auth"
    assert accounts.classify("codex", "Run codex login first") == "auth"
    # Provider-side incidents must NOT cool an account.
    assert accounts.classify("claude", "overloaded_error: capacity") == "other"
    assert accounts.classify("claude", "Request timed out") == "other"


def test_child_env_overlay():
    assert accounts.child_env_overlay({"auth": {"kind": "default"}}) == {}
    assert accounts.child_env_overlay(
        {"auth": {"kind": "config_dir", "path": "/tmp/x"}}
    ) == {"CLAUDE_CONFIG_DIR": "/tmp/x"}
    assert accounts.child_env_overlay(
        {"auth": {"kind": "codex_home", "path": "/tmp/y"}}
    ) == {"CODEX_HOME": "/tmp/y"}


def test_remove_clears_pin_and_state():
    b = accounts.add("Claude B", "claude")
    accounts.set_pinned(b["id"])
    accounts.report_limited(b["id"], "usage limit")
    accounts.remove(b["id"])
    assert accounts.pinned() is None
    assert accounts.get(b["id"]) is None
    cds, _ = accounts.cooldown_state()
    assert b["id"] not in cds
