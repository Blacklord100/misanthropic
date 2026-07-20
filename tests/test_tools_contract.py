"""SDK contract tests for client-defined tool use (function calling).

Backed by tests/fake_tool_claude.py, which spawns the REAL tool_shim from the
--mcp-config it receives and speaks real MCP — so these tests cover server
routing, the bridge TCP protocol, shim blocking, and the park/continue cycle
end-to-end. Only the model is scripted.
"""

import threading
from pathlib import Path

import pytest

anthropic = pytest.importorskip("anthropic")

from misanthropic import claude as claude_mod
from misanthropic import server, tool_bridge

FAKE = Path(__file__).parent / "fake_tool_claude.py"

TOOLS = [
    {"name": "get_weather", "description": "Weather for a location.",
     "input_schema": {"type": "object",
                      "properties": {"location": {"type": "string"}},
                      "required": ["location"]}},
    {"name": "get_time", "description": "Time in a timezone.",
     "input_schema": {"type": "object",
                      "properties": {"timezone": {"type": "string"}},
                      "required": ["timezone"]}},
]


@pytest.fixture(autouse=True)
def _clean_parks():
    yield
    for run in list(tool_bridge._runs.values()):
        run.destroy()


@pytest.fixture()
def pid_file(tmp_path, monkeypatch):
    p = tmp_path / "fake_pids"
    monkeypatch.setenv("FAKE_PID_FILE", str(p))
    return p


@pytest.fixture()
def live_server(tmp_path, monkeypatch, pid_file):
    FAKE.chmod(0o755)
    monkeypatch.setattr(claude_mod, "CLAUDE_BIN", str(FAKE))
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


def _continuation(first, prompt, results):
    """Full history for a tool-loop continuation, as an SDK agent loop sends."""
    return [
        {"role": "user", "content": prompt},
        {"role": "assistant", "content": [
            b.model_dump(exclude_none=True) if hasattr(b, "model_dump") else b
            for b in first.content]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tid, "content": text}
            for tid, text in results]},
    ]


def test_tool_roundtrip_blocking(client, pid_file):
    first = client.messages.create(
        model="claude-sonnet-4-6", max_tokens=64, tools=TOOLS,
        messages=[{"role": "user", "content": "TOOLCALL"}])
    assert first.stop_reason == "tool_use"
    calls = [b for b in first.content if b.type == "tool_use"]
    assert len(calls) == 1
    assert calls[0].name == "get_weather"       # MCP prefix stripped
    assert calls[0].input == {"location": "Paris"}
    assert calls[0].id == "toolu_fake_1"        # authentic stream id

    second = client.messages.create(
        model="claude-sonnet-4-6", max_tokens=64, tools=TOOLS,
        messages=_continuation(first, "TOOLCALL", [(calls[0].id, "72F and dry")]))
    assert second.stop_reason == "end_turn"
    assert second.content[0].text == "Got: 72F and dry"
    # One PID = the parked process served both requests.
    assert len(pid_file.read_text().split()) == 1


def test_tool_roundtrip_streaming(client, pid_file):
    with client.messages.stream(
            model="claude-sonnet-4-6", max_tokens=64, tools=TOOLS,
            messages=[{"role": "user", "content": "TOOLCALL"}]) as stream:
        first = stream.get_final_message()
    assert first.stop_reason == "tool_use"
    call = [b for b in first.content if b.type == "tool_use"][0]
    assert call.name == "get_weather"
    assert call.input == {"location": "Paris"}
    assert first.model == "claude-sonnet-4-6"   # not the CLI tier

    with client.messages.stream(
            model="claude-sonnet-4-6", max_tokens=64, tools=TOOLS,
            messages=_continuation(first, "TOOLCALL", [(call.id, "72F")])) as stream:
        second = stream.get_final_message()
    assert second.content[0].text == "Got: 72F"
    assert len(pid_file.read_text().split()) == 1


def test_parallel_tool_calls(client, pid_file):
    first = client.messages.create(
        model="claude-sonnet-4-6", max_tokens=64, tools=TOOLS,
        messages=[{"role": "user", "content": "TOOLCALL2"}])
    calls = [b for b in first.content if b.type == "tool_use"]
    assert [c.name for c in calls] == ["get_weather", "get_time"]

    second = client.messages.create(
        model="claude-sonnet-4-6", max_tokens=64, tools=TOOLS,
        messages=_continuation(first, "TOOLCALL2",
                               [(calls[0].id, "72F"), (calls[1].id, "14:05")]))
    assert second.content[0].text == "Got: 72F & 14:05"
    assert len(pid_file.read_text().split()) == 1


def test_partial_tool_results_400(client):
    first = client.messages.create(
        model="claude-sonnet-4-6", max_tokens=64, tools=TOOLS,
        messages=[{"role": "user", "content": "TOOLCALL2"}])
    calls = [b for b in first.content if b.type == "tool_use"]
    with pytest.raises(anthropic.BadRequestError):
        client.messages.create(
            model="claude-sonnet-4-6", max_tokens=64, tools=TOOLS,
            messages=_continuation(first, "TOOLCALL2", [(calls[0].id, "72F")]))


def test_expired_park_falls_back_to_fresh_run(client, pid_file, monkeypatch):
    monkeypatch.setattr(tool_bridge, "PARK_TTL_S", 0.05)
    first = client.messages.create(
        model="claude-sonnet-4-6", max_tokens=64, tools=TOOLS,
        messages=[{"role": "user", "content": "TOOLCALL"}])
    call = [b for b in first.content if b.type == "tool_use"][0]
    import time
    time.sleep(0.3)  # park expires, process killed
    second = client.messages.create(
        model="claude-sonnet-4-6", max_tokens=64, tools=TOOLS,
        messages=_continuation(first, "TOOLCALL", [(call.id, "72F")]))
    # Fallback ran fresh over the flattened history (fake detects the marker).
    assert second.content[0].text == "Recovered from flattened history."
    assert len(pid_file.read_text().split()) == 2


def test_parked_run_releases_governor_slot(client, live_server, monkeypatch):
    monkeypatch.setattr(server, "_governor", server.Governor(1))
    monkeypatch.setattr(server, "QUEUE_WAIT_S", 0.5)
    first = client.messages.create(
        model="claude-sonnet-4-6", max_tokens=64, tools=TOOLS,
        messages=[{"role": "user", "content": "TOOLCALL"}])
    assert first.stop_reason == "tool_use"
    assert tool_bridge.parked_count() == 1
    # The park must have released its slot: a plain request needs it.
    msg = client.messages.create(
        model="claude-sonnet-4-6", max_tokens=64,
        messages=[{"role": "user", "content": "plain please"}])
    assert msg.content[0].text == "plain"


def test_bad_tool_name_400(client):
    with pytest.raises(anthropic.BadRequestError):
        client.messages.create(
            model="claude-sonnet-4-6", max_tokens=64,
            tools=[{"name": "bad name!", "input_schema": {"type": "object"}}],
            messages=[{"role": "user", "content": "hi"}])


def test_no_tool_call_plain_answer(client):
    """Tools offered but unused: a normal end_turn text response."""
    msg = client.messages.create(
        model="claude-sonnet-4-6", max_tokens=64, tools=TOOLS,
        messages=[{"role": "user", "content": "just say plain"}])
    assert msg.stop_reason == "end_turn"
    assert msg.content[0].text == "plain"
