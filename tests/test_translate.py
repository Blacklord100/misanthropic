from misanthropic import translate


def test_extract_system_variants():
    assert translate.extract_system({}) is None
    assert translate.extract_system({"system": "be terse"}) == "be terse"
    blocks = {"system": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}
    assert translate.extract_system(blocks) == "a\n\nb"
    # non-text blocks are skipped; empty result collapses to None
    assert translate.extract_system({"system": [{"type": "image"}]}) is None


def test_messages_to_prompt_single_vs_multi():
    one = [{"role": "user", "content": "hello"}]
    assert translate.messages_to_prompt(one) == "hello"
    multi = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hey"},
        {"role": "user", "content": "bye"},
    ]
    out = translate.messages_to_prompt(multi)
    assert out.startswith("Human: hi")
    assert "Assistant: hey" in out
    assert out.rstrip().endswith("Assistant:")  # primes the model to continue


def test_build_cli_input_text_vs_image():
    fmt, payload = translate.build_cli_input([{"role": "user", "content": "plain"}])
    assert fmt == "text"
    assert payload == "plain"

    img = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "x"}}
    fmt, payload = translate.build_cli_input(
        [{"role": "user", "content": [{"type": "text", "text": "what is this"}, img]}]
    )
    assert fmt == "stream-json"
    assert payload.endswith("\n")
    import json
    line = json.loads(payload)
    types = [b["type"] for b in line["message"]["content"]]
    assert types == ["text", "image"]


def test_wrapper_to_message_shape_and_echoes_model():
    wrapper = {
        "result": "the answer",
        "stop_reason": "end_turn",
        "session_id": "abc-123",
        "usage": {"input_tokens": 11, "output_tokens": 7},
    }
    msg = translate.wrapper_to_message(wrapper, "claude-opus-4-1")
    assert msg["type"] == "message" and msg["role"] == "assistant"
    assert msg["model"] == "claude-opus-4-1"  # echoes requested id, not the tier
    assert msg["content"] == [{"type": "text", "text": "the answer"}]
    assert msg["usage"]["input_tokens"] == 11
    assert msg["usage"]["output_tokens"] == 7


def test_count_tokens_estimate():
    body = {"messages": [{"role": "user", "content": "abcd" * 25}]}  # 100 chars
    est = translate.count_tokens(body)["input_tokens"]
    assert est >= 20  # ~4 chars/token, plus the system/newline padding
    assert translate.count_tokens({})["input_tokens"] == 1  # never zero


def test_web_blocks_to_content_maps_search():
    links = 'Header\nLinks: [{"title":"T","url":"https://e.com"}]\nsnippet'
    blocks = [
        {"type": "tool_use", "name": "WebSearch", "id": "toolu_x", "input": {"query": "q"}},
        {"type": "tool_result", "tool_use_id": "toolu_x", "content": links},
        {"type": "text", "text": "done"},
    ]
    content = translate.web_blocks_to_content(blocks)
    kinds = [b["type"] for b in content]
    assert kinds == ["server_tool_use", "web_search_tool_result", "text"]
    assert content[0]["name"] == "web_search"
    assert content[0]["id"].startswith("srvtoolu_")
    results = content[1]["content"]
    assert results[0]["url"] == "https://e.com"
    assert results[0]["encrypted_content"]  # synthesized, well-typed


def test_web_blocks_ignores_orphan_non_websearch_result():
    blocks = [{"type": "tool_result", "tool_use_id": "toolu_other", "content": "x"}]
    content = translate.web_blocks_to_content(blocks)
    # no real content -> single empty text block, no orphaned result
    assert content == [{"type": "text", "text": "", "citations": None}]


def test_web_usage_sums_model_usage():
    wrapper = {
        "modelUsage": {
            "claude-sonnet": {"inputTokens": 100, "outputTokens": 50, "webSearchRequests": 2},
            "claude-haiku": {"inputTokens": 10, "outputTokens": 5, "webSearchRequests": 1},
        }
    }
    usage = translate.web_usage(wrapper)
    assert usage["input_tokens"] == 110
    assert usage["output_tokens"] == 55
    assert usage["server_tool_use"]["web_search_requests"] == 3
