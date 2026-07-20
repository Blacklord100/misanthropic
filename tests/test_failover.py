"""Contract tests for multi-account failover, against the real Anthropic SDK.

Accounts use config_dir auth whose PATH names select the fake CLI's behavior
(see FAKE_CLAUDE in test_contract.py): "limited" → usage-limit error,
"loggedout" → auth error, anything else → healthy.
"""

import threading

import pytest

anthropic = pytest.importorskip("anthropic")

from misanthropic import accounts, history, sessions
from misanthropic import claude as claude_mod
from misanthropic import server
from tests.test_contract import FAKE_CLAUDE


def _acct(aid, label, path, priority):
    return {"id": aid, "label": label, "backend": "claude",
            "auth": {"kind": "config_dir", "path": path},
            "priority": priority, "enabled": True}


@pytest.fixture()
def two_accounts(tmp_path):
    """Account A (priority 0) is rate-limited; account B is healthy."""
    import json
    (tmp_path / "accounts.json").write_text(json.dumps({
        "version": 1, "pinned": None,
        "accounts": [
            _acct("acc-a", "Claude A", str(tmp_path / "limited-a"), 0),
            _acct("acc-b", "Claude B", str(tmp_path / "healthy-b"), 1),
        ]}))


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
    return anthropic.Anthropic(base_url=live_server, api_key="unused",
                               max_retries=0)


def test_blocking_failover_to_second_account(two_accounts, client):
    msg = client.messages.create(
        model="claude-sonnet-4-6", max_tokens=64,
        messages=[{"role": "user", "content": "ping"}])
    assert msg.content[0].text == "pong"
    # The limited account is cooling; the row records the actual server.
    cds, _ = accounts.cooldown_state()
    assert "acc-a" in cds
    row = history.recent(limit=1)[0]
    assert row["account"] == "Claude B"
    assert row["backend"] == "claude"


def test_second_request_skips_cooling_account(two_accounts, client):
    client.messages.create(model="claude-sonnet-4-6", max_tokens=64,
                           messages=[{"role": "user", "content": "ping"}])
    assert [a["id"] for a in accounts.eligible({"text": True})] == ["acc-b"]


def test_streaming_failover_is_invisible(two_accounts, client):
    chunks = []
    with client.messages.stream(
            model="claude-sonnet-4-6", max_tokens=64,
            messages=[{"role": "user", "content": "ping"}]) as stream:
        for text in stream.text_stream:
            chunks.append(text)
        final = stream.get_final_message()
    assert "".join(chunks) == "pong"
    assert final.stop_reason == "end_turn"
    cds, _ = accounts.cooldown_state()
    assert "acc-a" in cds


def test_all_accounts_limited_529(tmp_path, client):
    import json
    (tmp_path / "accounts.json").write_text(json.dumps({
        "version": 1, "pinned": None,
        "accounts": [
            _acct("acc-a", "A", str(tmp_path / "limited-a"), 0),
            _acct("acc-b", "B", str(tmp_path / "limited-b"), 1),
        ]}))
    with pytest.raises(anthropic.APIStatusError) as exc:
        client.messages.create(model="claude-sonnet-4-6", max_tokens=64,
                               messages=[{"role": "user", "content": "ping"}])
    assert exc.value.status_code == 529
    assert exc.value.body["error"]["type"] == "overloaded_error"


def test_logged_out_account_marked_and_skipped(tmp_path, client):
    import json
    (tmp_path / "accounts.json").write_text(json.dumps({
        "version": 1, "pinned": None,
        "accounts": [
            _acct("acc-a", "A", str(tmp_path / "loggedout-a"), 0),
            _acct("acc-b", "B", str(tmp_path / "healthy-b"), 1),
        ]}))
    msg = client.messages.create(model="claude-sonnet-4-6", max_tokens=64,
                                 messages=[{"role": "user", "content": "ping"}])
    assert msg.content[0].text == "pong"
    _, logged_out = accounts.cooldown_state()
    assert "acc-a" in logged_out


def test_tools_claude_only_529_when_claude_limited(tmp_path, client):
    import json
    (tmp_path / "accounts.json").write_text(json.dumps({
        "version": 1, "pinned": None,
        "accounts": [
            _acct("acc-a", "A", str(tmp_path / "healthy-a"), 0),
            {"id": "acc-cx", "label": "Codex", "backend": "codex",
             "auth": {"kind": "codex_home", "path": str(tmp_path / "cx")},
             "priority": 1, "enabled": True},
        ]}))
    accounts.report_limited("acc-a", "usage limit")
    with pytest.raises(anthropic.APIStatusError) as exc:
        client.messages.create(
            model="claude-sonnet-4-6", max_tokens=64,
            tools=[{"name": "get_x", "input_schema": {"type": "object"}}],
            messages=[{"role": "user", "content": "hi"}])
    assert exc.value.status_code == 529
    assert "Claude-only" in exc.value.body["error"]["message"]


def test_pinned_account_serves_first(tmp_path, client):
    import json
    (tmp_path / "accounts.json").write_text(json.dumps({
        "version": 1, "pinned": "acc-b",
        "accounts": [
            _acct("acc-a", "A", str(tmp_path / "healthy-a"), 0),
            _acct("acc-b", "B", str(tmp_path / "healthy-b"), 1),
        ]}))
    client.messages.create(model="claude-sonnet-4-6", max_tokens=64,
                           messages=[{"role": "user", "content": "ping"}])
    assert history.recent(limit=1)[0]["account"] == "B"


def test_session_sticks_to_account_and_529_when_cooling(tmp_path, client):
    import json
    (tmp_path / "accounts.json").write_text(json.dumps({
        "version": 1, "pinned": None,
        "accounts": [
            _acct("acc-a", "A", str(tmp_path / "healthy-a"), 0),
            _acct("acc-b", "B", str(tmp_path / "healthy-b"), 1),
        ]}))
    sessions.add_key("sk-test-session-key", "t")
    kw = dict(model="claude-sonnet-4-6", max_tokens=64,
              messages=[{"role": "user", "content": "ping"}])
    scoped = anthropic.Anthropic(base_url=client.base_url,
                                 api_key="sk-test-session-key", max_retries=0)
    scoped.messages.create(**kw)
    assert sessions.get_session_account("sk-test-session-key") == "acc-a"
    # Bound account cooling -> 529, NOT a hop to account B.
    accounts.report_limited("acc-a", "usage limit")
    with pytest.raises(anthropic.APIStatusError) as exc:
        scoped.messages.create(**kw)
    assert exc.value.status_code == 529
    assert "session account" in exc.value.body["error"]["message"].lower()


def test_session_rebinds_when_account_deleted(tmp_path, client):
    import json
    (tmp_path / "accounts.json").write_text(json.dumps({
        "version": 1, "pinned": None,
        "accounts": [
            _acct("acc-a", "A", str(tmp_path / "healthy-a"), 0),
            _acct("acc-b", "B", str(tmp_path / "healthy-b"), 1),
        ]}))
    sessions.add_key("sk-test-session-key", "t")
    scoped = anthropic.Anthropic(base_url=client.base_url,
                                 api_key="sk-test-session-key", max_retries=0)
    kw = dict(model="claude-sonnet-4-6", max_tokens=64,
              messages=[{"role": "user", "content": "ping"}])
    scoped.messages.create(**kw)
    assert sessions.get_session_account("sk-test-session-key") == "acc-a"
    accounts.remove("acc-a")
    msg = scoped.messages.create(**kw)   # continuity impossible -> fresh bind
    assert msg.content[0].text == "pong"
    assert sessions.get_session_account("sk-test-session-key") == "acc-b"
