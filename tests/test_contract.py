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
import json, os, sys

args = sys.argv[1:]
prompt = sys.stdin.read()

# Multi-account tests select behavior via the account's CLAUDE_CONFIG_DIR:
# a path containing "limited" acts rate-limited, "loggedout" acts logged out.
cfgdir = os.environ.get("CLAUDE_CONFIG_DIR", "")
if "limited" in cfgdir:
    sys.stderr.write("You've reached your usage limit for this account.\n")
    sys.exit(1)
if "loggedout" in cfgdir:
    sys.stderr.write("Not logged in. Please run /login.\n")
    sys.exit(1)

if "AUTHFAIL" in prompt:
    sys.stderr.write("Invalid API key. Please run /login to authenticate.\n")
    sys.exit(1)
if "RATELIMIT" in prompt:
    sys.stderr.write("Rate limit exceeded, try again later.\n")
    sys.exit(1)

usage = {"input_tokens": 7, "output_tokens": 2,
         "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}

# LONGSTREAM exercises the limit gate: text with a STOP marker split so the
# marker straddles delta boundaries.
if "LONGSTREAM" in prompt:
    text, deltas = ("Hello world STOP more text after",
                    ["Hel", "lo wo", "rld ST", "OP more", " text after"])
else:
    text, deltas = "pong", ["po", "ng"]

think = "THINKING" in prompt

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
    idx = 0
    assistant_content = []
    if think:
        # The real CLI thinks by default and interleaves a thinking block
        # before the text block.
        ev({"type": "content_block_start", "index": 0,
            "content_block": {"type": "thinking", "thinking": ""}})
        ev({"type": "content_block_delta", "index": 0,
            "delta": {"type": "thinking_delta", "thinking": "hmm pondering"}})
        ev({"type": "content_block_delta", "index": 0,
            "delta": {"type": "signature_delta", "signature": "sig=="}})
        ev({"type": "content_block_stop", "index": 0})
        assistant_content.append({"type": "thinking", "thinking": "hmm pondering",
                                  "signature": "sig=="})
        idx = 1
    ev({"type": "content_block_start", "index": idx,
        "content_block": {"type": "text", "text": ""}})
    for d in deltas:
        ev({"type": "content_block_delta", "index": idx,
            "delta": {"type": "text_delta", "text": d}})
    ev({"type": "content_block_stop", "index": idx})
    ev({"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
        "usage": {"output_tokens": 2}})
    ev({"type": "message_stop"})
    assistant_content.append({"type": "text", "text": text})
    line({"type": "assistant", "message": {"role": "assistant", "content": assistant_content}})
    line({"type": "result", "result": text, "usage": usage,
          "stop_reason": "end_turn", "session_id": "sess-fake-1"})
else:
    json.dump({"type": "result", "result": text, "usage": usage,
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


def test_sdk_models_list_and_get(client):
    listed = [m.id for m in client.models.list()]
    assert "claude-sonnet-4-6" in listed
    got = client.models.retrieve("claude-sonnet-4-6")
    assert got.display_name.startswith("Claude Sonnet")
    with pytest.raises(anthropic.NotFoundError):
        client.models.retrieve("claude-nonexistent")


def test_count_tokens_endpoint(client):
    n = client.messages.count_tokens(
        model="claude-sonnet-4-6",
        messages=[{"role": "user", "content": "hello world"}],
    )
    assert n.input_tokens > 0


def test_stop_sequence_enforced_streaming(client):
    chunks = []
    with client.messages.stream(
        model="claude-sonnet-4-6", max_tokens=64, stop_sequences=["STOP"],
        messages=[{"role": "user", "content": "LONGSTREAM"}],
    ) as stream:
        for text in stream.text_stream:
            chunks.append(text)
        final = stream.get_final_message()
    assert "".join(chunks) == "Hello world "
    assert final.stop_reason == "stop_sequence"
    assert final.stop_sequence == "STOP"


def test_stop_sequence_enforced_blocking(client):
    msg = client.messages.create(
        model="claude-sonnet-4-6", max_tokens=64, stop_sequences=["STOP"],
        messages=[{"role": "user", "content": "LONGSTREAM"}],
    )
    assert msg.content[0].text == "Hello world "
    assert msg.stop_reason == "stop_sequence"
    assert msg.stop_sequence == "STOP"


def test_max_tokens_ignored_by_default(client):
    msg = client.messages.create(
        model="claude-sonnet-4-6", max_tokens=2,
        messages=[{"role": "user", "content": "LONGSTREAM"}],
    )
    assert msg.content[0].text == "Hello world STOP more text after"
    assert msg.stop_reason == "end_turn"


def test_max_tokens_enforced_when_opted_in(client, monkeypatch):
    monkeypatch.setenv("MISANTHROPIC_ENFORCE_MAX_TOKENS", "1")
    msg = client.messages.create(
        model="claude-sonnet-4-6", max_tokens=2,
        messages=[{"role": "user", "content": "LONGSTREAM"}],
    )
    assert msg.content[0].text == "Hello wo"  # 2 tokens ~ 8 chars
    assert msg.stop_reason == "max_tokens"
    assert msg.usage.output_tokens == 2


def test_max_tokens_enforced_streaming(client, monkeypatch):
    monkeypatch.setenv("MISANTHROPIC_ENFORCE_MAX_TOKENS", "1")
    chunks = []
    with client.messages.stream(
        model="claude-sonnet-4-6", max_tokens=2,
        messages=[{"role": "user", "content": "LONGSTREAM"}],
    ) as stream:
        for text in stream.text_stream:
            chunks.append(text)
        final = stream.get_final_message()
    assert "".join(chunks) == "Hello wo"
    assert final.stop_reason == "max_tokens"
    assert final.usage.output_tokens == 2


def test_thinking_stripped_by_default(client):
    # The fake CLI thinks on THINKING prompts; a client that never opted in
    # must see contiguous text-only content.
    with client.messages.stream(
        model="claude-sonnet-4-6", max_tokens=64,
        messages=[{"role": "user", "content": "THINKING"}],
    ) as stream:
        final = stream.get_final_message()
    assert [b.type for b in final.content] == ["text"]
    assert final.content[0].text == "pong"


def test_thinking_passthrough_streaming(client):
    with client.messages.stream(
        model="claude-sonnet-4-6", max_tokens=2048,
        thinking={"type": "enabled", "budget_tokens": 1024},
        messages=[{"role": "user", "content": "THINKING"}],
    ) as stream:
        final = stream.get_final_message()
    assert [b.type for b in final.content] == ["thinking", "text"]
    assert final.content[0].thinking == "hmm pondering"
    assert final.content[0].signature == "sig=="
    assert final.content[1].text == "pong"


def test_thinking_passthrough_blocking(client):
    msg = client.messages.create(
        model="claude-sonnet-4-6", max_tokens=2048,
        thinking={"type": "enabled", "budget_tokens": 1024},
        messages=[{"role": "user", "content": "THINKING"}],
    )
    assert [b.type for b in msg.content] == ["thinking", "text"]
    assert msg.content[0].thinking == "hmm pondering"
    assert msg.content[1].text == "pong"


def test_thinking_disabled_blocking_stays_text_only(client):
    msg = client.messages.create(
        model="claude-sonnet-4-6", max_tokens=64,
        thinking={"type": "disabled"},
        messages=[{"role": "user", "content": "THINKING"}],
    )
    assert [b.type for b in msg.content] == ["text"]


def test_401_does_not_desync_keepalive(client):
    """A 401 sent before the request body was read used to leave the body
    bytes in the keep-alive stream — the NEXT request on the connection then
    parsed garbage and got the wrong response (e.g. the SPA's index.html)."""
    from misanthropic import sessions
    sessions.add_key("sk-desync-test", "t")
    try:
        with pytest.raises(anthropic.AuthenticationError):
            client.messages.create(model="claude-sonnet-4-6", max_tokens=16,
                                   messages=[{"role": "user", "content": "hi"}])
        for _ in range(2):  # same SDK client = same pooled connection
            assert "claude-sonnet-4-6" in [m.id for m in client.models.list()]
    finally:
        sessions.remove_key("sk-desync-test")


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
