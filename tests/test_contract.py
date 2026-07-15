"""SDK contract tests: the promise is "an unmodified Anthropic SDK can't tell
the difference." These run the official `anthropic` client against a real
server instance backed by a fake `claude` binary, so the wire format — success,
streaming, and the error taxonomy SDK retry logic keys off — is enforced, not
aspirational.

Skipped automatically when the `anthropic` package isn't installed.
"""

import json
import threading

import pytest

anthropic = pytest.importorskip("anthropic")

from misanthropic import claude as claude_mod
from misanthropic import server

FAKE_CLAUDE = r'''#!/usr/bin/env python3
import json, sys

args = sys.argv[1:]
prompt = sys.stdin.read()

if "AUTHFAIL" in prompt:
    sys.stderr.write("Invalid API key. Please run /login to authenticate.\n")
    sys.exit(1)
if "RATELIMIT" in prompt:
    sys.stderr.write("Rate limit exceeded, try again later.\n")
    sys.exit(1)

usage = {"input_tokens": 7, "output_tokens": 2,
         "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}

if "--output-format" in args and args[args.index("--output-format") + 1] == "stream-json":
    def line(obj):
        sys.stdout.write(json.dumps(obj) + "\n")
    line({"type": "system", "subtype": "init", "session_id": "sess-fake-1"})
    def ev(event):
        line({"type": "stream_event", "event": event})
    ev({"type": "message_start", "message": {
        "id": "msg_fake", "type": "message", "role": "assistant", "content": [],
        "model": "sonnet", "stop_reason": None, "stop_sequence": None,
        "usage": {"input_tokens": 7, "output_tokens": 0}}})
    ev({"type": "content_block_start", "index": 0,
        "content_block": {"type": "text", "text": ""}})
    ev({"type": "content_block_delta", "index": 0,
        "delta": {"type": "text_delta", "text": "po"}})
    ev({"type": "content_block_delta", "index": 0,
        "delta": {"type": "text_delta", "text": "ng"}})
    ev({"type": "content_block_stop", "index": 0})
    ev({"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
        "usage": {"output_tokens": 2}})
    ev({"type": "message_stop"})
    line({"type": "result", "result": "pong", "usage": usage,
          "stop_reason": "end_turn", "session_id": "sess-fake-1"})
else:
    json.dump({"type": "result", "result": "pong", "usage": usage,
               "stop_reason": "end_turn", "session_id": "sess-fake-1",
               "is_error": False}, sys.stdout)
'''


@pytest.fixture()
def live_server(tmp_path, monkeypatch):
    fake = tmp_path / "claude"
    fake.write_text(FAKE_CLAUDE)
    fake.chmod(0o755)
    monkeypatch.setattr(claude_mod, "CLAUDE_BIN", str(fake))
    monkeypatch.setattr(claude_mod, "_resolved_claude", None)

    httpd = server.make_httpd("127.0.0.1", 0)  # ephemeral port
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


def test_sdk_blocking_roundtrip(client):
    msg = client.messages.create(
        model="claude-sonnet-4-6", max_tokens=64,
        messages=[{"role": "user", "content": "ping"}],
    )
    assert msg.type == "message"
    assert msg.role == "assistant"
    assert msg.content[0].text == "pong"
    assert msg.stop_reason == "end_turn"
    assert msg.usage.input_tokens == 7
    assert msg.usage.output_tokens == 2
    # The response must echo the requested model, not the CLI tier.
    assert msg.model == "claude-sonnet-4-6"


def test_sdk_streaming_roundtrip(client):
    chunks = []
    with client.messages.stream(
        model="claude-sonnet-4-6", max_tokens=64,
        messages=[{"role": "user", "content": "ping"}],
    ) as stream:
        for text in stream.text_stream:
            chunks.append(text)
        final = stream.get_final_message()
    assert "".join(chunks) == "pong"
    assert final.stop_reason == "end_turn"
    assert final.usage.output_tokens == 2


def test_sdk_auth_error_maps_to_401(client):
    with pytest.raises(anthropic.AuthenticationError):
        client.messages.create(
            model="claude-sonnet-4-6", max_tokens=64,
            messages=[{"role": "user", "content": "AUTHFAIL"}],
        )


def test_sdk_rate_limit_maps_to_529(client):
    with pytest.raises(anthropic.APIStatusError) as exc:
        client.messages.create(
            model="claude-sonnet-4-6", max_tokens=64,
            messages=[{"role": "user", "content": "RATELIMIT"}],
        )
    assert exc.value.status_code == 529
    assert exc.value.body["error"]["type"] == "overloaded_error"


def test_governor_saturation_returns_529(live_server, client, monkeypatch):
    # Zero slots and no queue window: the server must refuse with the API's
    # overload signal rather than stacking subprocesses.
    monkeypatch.setattr(server, "_governor", server.Governor(1))
    monkeypatch.setattr(server, "QUEUE_WAIT_S", 0.05)
    server._governor.acquire()
    try:
        with pytest.raises(anthropic.APIStatusError) as exc:
            client.messages.create(
                model="claude-sonnet-4-6", max_tokens=64,
                messages=[{"role": "user", "content": "ping"}],
            )
        assert exc.value.status_code == 529
    finally:
        server._governor.release()


def test_count_tokens_endpoint(client):
    n = client.messages.count_tokens(
        model="claude-sonnet-4-6",
        messages=[{"role": "user", "content": "hello world"}],
    )
    assert n.input_tokens > 0


def test_history_records_request(client):
    from misanthropic import history
    before = history.count()
    client.messages.create(
        model="claude-sonnet-4-6", max_tokens=64,
        messages=[{"role": "user", "content": "ping"}],
    )
    assert history.count() == before + 1
    row = history.recent(limit=1)[0]
    assert row["status"] == 200
    assert row["response_text"] == "pong"
    assert row["usd"] > 0
