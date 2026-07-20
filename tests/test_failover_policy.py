"""The failover POLICY: off by default (stop at the serving account), opt-in
globally via settings, overridable per key."""

import json
import threading

import pytest

anthropic = pytest.importorskip("anthropic")

from misanthropic import accounts, history, sessions, settings
from misanthropic import claude as claude_mod
from misanthropic import server
from tests.test_contract import FAKE_CLAUDE


def _acct(aid, label, path, priority):
    return {"id": aid, "label": label, "backend": "claude",
            "auth": {"kind": "config_dir", "path": path},
            "priority": priority, "enabled": True}


@pytest.fixture()
def two_accounts(tmp_path):
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


def test_failover_enabled_resolution():
    assert not accounts.failover_enabled()               # shipped default: off
    assert accounts.failover_enabled("on")               # key override wins
    settings.update({"failover_policy": "auto"})
    assert accounts.failover_enabled()
    assert not accounts.failover_enabled("off")          # key override wins


def test_off_by_default_stops_at_serving_account(two_accounts, client):
    kw = dict(model="claude-sonnet-4-6", max_tokens=64,
              messages=[{"role": "user", "content": "ping"}])
    # First request: account A fails with its usage limit; NO hop to B.
    with pytest.raises(anthropic.APIStatusError) as exc:
        client.messages.create(**kw)
    assert exc.value.status_code == 529
    # A is cooling now; the next request short-circuits with a clear message
    # instead of spawning a doomed run.
    with pytest.raises(anthropic.APIStatusError) as exc:
        client.messages.create(**kw)
    assert exc.value.status_code == 529
    assert "failover is off" in exc.value.body["error"]["message"]
    # Account B was never touched.
    assert all(r["account"] != "Claude B" for r in history.recent(limit=10))


def test_streaming_off_stops_too(two_accounts, client):
    with pytest.raises(anthropic.APIStatusError) as exc:
        with client.messages.stream(
                model="claude-sonnet-4-6", max_tokens=64,
                messages=[{"role": "user", "content": "ping"}]) as stream:
            stream.get_final_message()
    assert exc.value.status_code == 529


def test_pin_still_respected_when_off(tmp_path, client):
    (tmp_path / "accounts.json").write_text(json.dumps({
        "version": 1, "pinned": "acc-b",
        "accounts": [
            _acct("acc-a", "A", str(tmp_path / "healthy-a"), 0),
            _acct("acc-b", "B", str(tmp_path / "healthy-b"), 1),
        ]}))
    client.messages.create(model="claude-sonnet-4-6", max_tokens=64,
                           messages=[{"role": "user", "content": "ping"}])
    assert history.recent(limit=1)[0]["account"] == "B"


def test_key_override_on_beats_global_off(tmp_path, client):
    (tmp_path / "accounts.json").write_text(json.dumps({
        "version": 1, "pinned": None,
        "accounts": [
            _acct("acc-a", "A", str(tmp_path / "limited-a"), 0),
            _acct("acc-b", "B", str(tmp_path / "healthy-b"), 1),
        ]}))
    sessions.add_key("sk-fo-key", "t")
    sessions.set_key_failover("sk-fo-key", "on")
    scoped = anthropic.Anthropic(base_url=client.base_url,
                                 api_key="sk-fo-key", max_retries=0)
    msg = scoped.messages.create(model="claude-sonnet-4-6", max_tokens=64,
                                 messages=[{"role": "user", "content": "ping"}])
    # Global policy is off, but this key opted in: A's limit hops to B.
    assert msg.content[0].text == "pong"
    assert sessions.get_session_account("sk-fo-key") == "acc-b"


def test_key_failover_meta_roundtrip():
    sessions.add_key("sk-x", "t")
    assert sessions.key_failover("sk-x") is None
    sessions.set_key_failover("sk-x", "off")
    assert sessions.key_failover("sk-x") == "off"
    sessions.set_key_failover("sk-x", "default")
    assert sessions.key_failover("sk-x") is None
    with pytest.raises(KeyError):
        sessions.set_key_failover("sk-unknown", "on")
