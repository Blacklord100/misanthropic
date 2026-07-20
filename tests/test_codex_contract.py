"""SDK contract tests for the codex backend, driven through account routing:
a codex account is pinned (or is the only eligible one) so requests land on
the fake codex CLI (tests/fake_codex.py)."""

import base64
import json
import threading
from pathlib import Path

import pytest

anthropic = pytest.importorskip("anthropic")

from misanthropic import accounts, history, settings
from misanthropic import claude as claude_mod
from misanthropic import codex as codex_mod
from misanthropic import server
from tests.test_contract import FAKE_CLAUDE

FAKE_CODEX = Path(__file__).parent / "fake_codex.py"


@pytest.fixture(autouse=True)
def _auto_failover(_isolate_state):
    # The codex-limit tests rely on hopping to the claude account.
    settings.update({"failover_policy": "auto"})


@pytest.fixture()
def codex_first(tmp_path):
    """Registry with a codex account pinned ahead of a healthy claude one."""
    (tmp_path / "accounts.json").write_text(json.dumps({
        "version": 1, "pinned": "acc-cx",
        "accounts": [
            {"id": "acc-cl", "label": "Claude", "backend": "claude",
             "auth": {"kind": "config_dir", "path": str(tmp_path / "healthy")},
             "priority": 0, "enabled": True},
            {"id": "acc-cx", "label": "Codex", "backend": "codex",
             "auth": {"kind": "codex_home", "path": str(tmp_path / "cxhome")},
             "priority": 1, "enabled": True},
        ]}))


@pytest.fixture()
def live_server(tmp_path, monkeypatch):
    fake_claude = tmp_path / "claude"
    fake_claude.write_text(FAKE_CLAUDE)
    fake_claude.chmod(0o755)
    FAKE_CODEX.chmod(0o755)
    monkeypatch.setattr(claude_mod, "CLAUDE_BIN", str(fake_claude))
    monkeypatch.setattr(claude_mod, "_resolved_claude", None)
    monkeypatch.setattr(codex_mod, "CODEX_BIN", str(FAKE_CODEX))
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


def test_codex_blocking_roundtrip(codex_first, client):
    msg = client.messages.create(
        model="claude-sonnet-4-6", max_tokens=64,
        messages=[{"role": "user", "content": "hello"}])
    assert msg.content[0].text == "codex says hi"
    assert msg.stop_reason == "end_turn"
    assert msg.model == "claude-sonnet-4-6"     # echoes the requested id
    # cached_input_tokens split out the Anthropic way
    assert msg.usage.input_tokens == 40
    assert msg.usage.cache_read_input_tokens == 60
    assert msg.usage.output_tokens == 9
    row = history.recent(limit=1)[0]
    assert row["account"] == "Codex"
    assert row["backend"] == "codex"
    # The log shows what actually ran — codex never runs the requested
    # Claude model (the API response echo above is unchanged).
    assert row["model"] == "codex:default-model"


def test_codex_thinking_gated_by_default(codex_first, client):
    msg = client.messages.create(
        model="claude-sonnet-4-6", max_tokens=64,
        messages=[{"role": "user", "content": "hello"}])
    assert [b.type for b in msg.content] == ["text"]


def test_codex_thinking_surfaced_when_enabled(codex_first, client):
    msg = client.messages.create(
        model="claude-sonnet-4-6", max_tokens=2048,
        thinking={"type": "enabled", "budget_tokens": 1024},
        messages=[{"role": "user", "content": "hello"}])
    assert [b.type for b in msg.content] == ["thinking", "text"]
    assert msg.content[0].thinking == "pondering deeply"


def test_codex_streaming_replay(codex_first, client):
    chunks = []
    with client.messages.stream(
            model="claude-sonnet-4-6", max_tokens=64,
            messages=[{"role": "user", "content": "hello"}]) as stream:
        for text in stream.text_stream:
            chunks.append(text)
        final = stream.get_final_message()
    assert "".join(chunks) == "codex says hi"
    assert final.stop_reason == "end_turn"
    assert final.usage.output_tokens == 9


def test_codex_system_prompt_reaches_agents_md(codex_first, client):
    msg = client.messages.create(
        model="claude-sonnet-4-6", max_tokens=64,
        system="Speak like a pirate.",
        messages=[{"role": "user", "content": "SYSCHECK"}])
    assert msg.content[0].text == "system: Speak like a pirate."


def test_codex_images_land_as_files(codex_first, client):
    png = base64.b64encode(b"fakepngbytes").decode()
    msg = client.messages.create(
        model="claude-sonnet-4-6", max_tokens=64,
        messages=[{"role": "user", "content": [
            {"type": "text", "text": "IMGCHECK"},
            {"type": "image", "source": {"type": "base64",
                                         "media_type": "image/png",
                                         "data": png}},
        ]}])
    assert msg.content[0].text == "images: 1"


def test_codex_limit_fails_over_to_claude(codex_first, client):
    msg = client.messages.create(
        model="claude-sonnet-4-6", max_tokens=64,
        messages=[{"role": "user", "content": "CODEXLIMIT ping"}])
    # The fake codex rate-limits; the claude account picks it up.
    assert msg.content[0].text == "pong"
    cds, _ = accounts.cooldown_state()
    assert "acc-cx" in cds
    assert history.recent(limit=1)[0]["backend"] == "claude"


def test_codex_authfail_marks_logged_out(codex_first, client):
    msg = client.messages.create(
        model="claude-sonnet-4-6", max_tokens=64,
        messages=[{"role": "user", "content": "CODEXNOAUTH ping"}])
    assert msg.content[0].text == "pong"
    _, logged_out = accounts.cooldown_state()
    assert "acc-cx" in logged_out


def test_codex_unique_message_ids(codex_first, client):
    a = client.messages.create(model="claude-sonnet-4-6", max_tokens=64,
                               messages=[{"role": "user", "content": "one"}])
    assert a.id.startswith("msg_")
    assert a.id != "msg_local"


def test_codex_login_status_probe():
    ok, detail = codex_mod.login_status()
    # CODEX_BIN not patched here — whatever the real machine says, the probe
    # must not crash and must return a tuple.
    assert ok in (True, False, None)
    assert isinstance(detail, str)
