"""Unit tests for the thinking filter and block-aware response building."""

from misanthropic.translate import (ThinkingFilter, thinking_requested,
                                    wrapper_to_message)


def _events():
    return [
        {"type": "message_start", "message": {"usage": {"input_tokens": 3}}},
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "thinking", "thinking": ""}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "thinking_delta", "thinking": "mull"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1,
         "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 1,
         "delta": {"type": "text_delta", "text": "hi"}},
        {"type": "content_block_stop", "index": 1},
        {"type": "message_stop"},
    ]


def test_thinking_requested():
    assert thinking_requested({"thinking": {"type": "enabled", "budget_tokens": 1024}})
    assert not thinking_requested({"thinking": {"type": "disabled"}})
    assert not thinking_requested({})
    assert not thinking_requested({"thinking": "enabled"})


def test_filter_disabled_strips_and_reindexes():
    f = ThinkingFilter(enabled=False)
    out = [e for e in (f.feed(e) for e in _events()) if e is not None]
    types = [e["type"] for e in out]
    assert types == ["message_start", "content_block_start",
                     "content_block_delta", "content_block_stop", "message_stop"]
    # The surviving text block must be re-indexed to 0.
    assert all(e.get("index", 0) == 0 for e in out)


def test_filter_enabled_passes_everything():
    f = ThinkingFilter(enabled=True)
    out = [f.feed(e) for e in _events()]
    assert out == _events()


def test_filter_multiple_thinking_blocks():
    f = ThinkingFilter(enabled=False)
    events = [
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "thinking"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1,
         "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_stop", "index": 1},
        {"type": "content_block_start", "index": 2,
         "content_block": {"type": "redacted_thinking"}},
        {"type": "content_block_stop", "index": 2},
        {"type": "content_block_start", "index": 3,
         "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_stop", "index": 3},
    ]
    out = [e for e in (f.feed(e) for e in events) if e is not None]
    assert [e["index"] for e in out] == [0, 0, 1, 1]


def test_wrapper_to_message_with_blocks():
    wrapper = {"result": "fallback", "usage": {"input_tokens": 1, "output_tokens": 2},
               "session_id": "s", "stop_reason": "end_turn"}
    blocks = [{"type": "thinking", "thinking": "mull", "signature": "sig"},
              {"type": "text", "text": "answer"}]
    msg = wrapper_to_message(wrapper, "claude-sonnet-4-6", blocks=blocks)
    assert [b["type"] for b in msg["content"]] == ["thinking", "text"]
    assert msg["content"][1]["text"] == "answer"


def test_wrapper_to_message_blocks_fallback_to_result_text():
    wrapper = {"result": "fallback", "usage": {}, "session_id": "s"}
    msg = wrapper_to_message(wrapper, "m", blocks=[])
    assert msg["content"] == [{"type": "text", "text": "fallback"}]
