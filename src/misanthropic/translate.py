"""Translate between the Anthropic Messages API schema and the local CLI.

The hosted Messages API takes a `system` string plus a `messages` array; the
`claude -p` CLI takes one system prompt and one stdin prompt. These helpers
flatten a request into that shape and build a spec-shaped response back.
"""

import base64
import json
import re

# Client tools surface to the CLI's model as MCP tools under this server name
# (see tool_bridge.py); responses strip the prefix so clients see their own
# tool names.
MCP_PREFIX = "mcp__misanthropic__"

_TOOL_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


class ToolRequestError(ValueError):
    """A client `tools`/`tool_choice` shape the proxy must 400 on."""


def extract_client_tools(body):
    """Pull client-defined tools out of a request.

    Returns (tools, tool_choice) where `tools` is a list of
    {name, description, input_schema} dicts — empty when the request carries
    none. `web_search*` server tools are excluded (they ride the web path).
    Raises ToolRequestError for shapes the hosted API would reject.
    """
    raw = body.get("tools")
    if not isinstance(raw, list):
        return [], body.get("tool_choice")
    tools, seen = [], set()
    for t in raw:
        if not isinstance(t, dict):
            continue
        ttype = str(t.get("type") or "custom")
        if ttype.startswith("web_search"):
            continue  # handled by the web path
        if ttype != "custom" and not t.get("input_schema"):
            # Other server tools (computer use, code execution, ...) aren't
            # backed by anything here — reject loudly rather than pretend.
            raise ToolRequestError(f"Server tool type `{ttype}` is not supported.")
        name = t.get("name")
        if not isinstance(name, str) or not _TOOL_NAME_RE.match(name):
            raise ToolRequestError(
                "Tool names must match ^[a-zA-Z0-9_-]{1,64}$.")
        if name in seen:
            raise ToolRequestError(f"Duplicate tool name: {name}")
        seen.add(name)
        tools.append({
            "name": name,
            "description": t.get("description") or "",
            "input_schema": t.get("input_schema") or {"type": "object"},
        })
    return tools, body.get("tool_choice")


def tool_choice_nudge(tool_choice):
    """A system-prompt suffix approximating `tool_choice` (best-effort — the
    CLI has no forced-tool mode, so `any`/`tool` are nudges, not guarantees)."""
    if not isinstance(tool_choice, dict):
        return None
    ctype = tool_choice.get("type")
    if ctype == "any":
        return ("You must respond by calling one of the provided tools; "
                "do not answer in plain text.")
    if ctype == "tool" and tool_choice.get("name"):
        return (f"You must respond by calling the `{tool_choice['name']}` tool; "
                "do not answer in plain text.")
    return None


def strip_mcp_prefix(name):
    if isinstance(name, str) and name.startswith(MCP_PREFIX):
        return name[len(MCP_PREFIX):]
    return name


def extract_system(body):
    """Pull the system prompt out of a Messages request (string or block list)."""
    system = body.get("system")
    if system is None:
        return None
    if isinstance(system, str):
        return system
    if isinstance(system, list):
        parts = []
        for block in system:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "\n\n".join(p for p in parts if p) or None
    return None


def _content_to_text(content):
    """Flatten a message's content (string or block list) to plain text."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            parts.append(block.get("text", ""))
        elif btype == "tool_result":
            # Carry the id so a flattened transcript (the dead-park fallback)
            # keeps calls and results pairable.
            inner = block.get("content")
            body = _content_to_text(inner) if inner is not None else ""
            parts.append(f"[tool_result for {block.get('tool_use_id', '')}: {body}]")
        elif btype == "tool_use":
            parts.append(f"[tool_use {block.get('id', '')} {block.get('name', '')}: "
                         f"{json.dumps(block.get('input', {}))}]")
        # `image` blocks are intentionally not flattened to text here. When a
        # request carries images, build_cli_input() routes the whole thing
        # through the CLI's stream-json input (which accepts image content) and
        # the images ride in their own channel — see _iter_image_blocks below.
    return "\n".join(p for p in parts if p)


def _iter_image_blocks(messages):
    """Yield every `image` content block across a Messages conversation, in order."""
    for m in messages:
        if not isinstance(m, dict):
            continue
        content = m.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "image":
                    yield block


def build_cli_input(messages):
    """Decide how to feed a Messages request to the local `claude` CLI.

    Returns an (input_format, payload) pair:

      ("text", prompt_str)
          No images present. `payload` is the flat prompt that goes to
          `claude -p` over stdin — the default, unchanged behavior.

      ("stream-json", jsonl_str)
          One or more `image` blocks present. The plain text CLI can't take
          image input, but `claude --input-format stream-json` accepts an
          Anthropic-shaped user message — including image content blocks. We
          render the conversation's text into one transcript block and append
          every image block after it, as a single user message (one JSONL line).
    """
    msgs = [m for m in messages if isinstance(m, dict)]
    images = list(_iter_image_blocks(msgs))
    text = messages_to_prompt(msgs)
    if not images:
        return ("text", text)
    content = []
    if text:
        content.append({"type": "text", "text": text})
    content.extend(images)
    line = {"type": "user", "message": {"role": "user", "content": content}}
    return ("stream-json", json.dumps(line) + "\n")


def messages_to_prompt(messages):
    """Flatten a Messages-API conversation into a single prompt for `claude -p`.

    A lone trailing user message is passed verbatim. Multi-turn history is
    rendered as a labeled transcript so the model continues as the assistant.
    """
    msgs = [m for m in messages if isinstance(m, dict)]
    if len(msgs) == 1 and msgs[0].get("role") == "user":
        return _content_to_text(msgs[0].get("content"))

    lines = []
    for m in msgs:
        label = "Human" if m.get("role") == "user" else "Assistant"
        lines.append(f"{label}: {_content_to_text(m.get('content'))}")
    lines.append("Assistant:")
    return "\n\n".join(lines)


def _message_id(wrapper):
    session = (wrapper.get("session_id") or "").replace("-", "")
    return "msg_" + (session[:24] or "local")


def thinking_requested(body):
    """True when the request opts into extended thinking. The CLI offers no
    thinking control (no flag, no budget), so this only gates whether thinking
    blocks are *surfaced*; `budget_tokens` is accepted and unenforced."""
    t = body.get("thinking")
    return isinstance(t, dict) and t.get("type") == "enabled"


def _blocks_to_content(blocks, fallback_text):
    """Shape CLI content blocks (thinking/text) into API response content."""
    content = []
    for b in blocks or []:
        t = b.get("type")
        if t == "text":
            if b.get("text"):
                content.append({"type": "text", "text": b["text"]})
        elif t == "thinking":
            content.append({"type": "thinking",
                            "thinking": b.get("thinking", ""),
                            "signature": b.get("signature", "")})
        elif t == "redacted_thinking":
            content.append({"type": "redacted_thinking", "data": b.get("data", "")})
    if not any(c["type"] == "text" for c in content):
        content.append({"type": "text", "text": fallback_text})
    return content


def wrapper_to_message(wrapper, model_requested, blocks=None):
    """Build an Anthropic Messages response from the CLI's JSON wrapper.

    `blocks` (from a thinking-enabled stream-collection run) carries the
    ordered thinking/text content; without it the response is the single text
    block built from the wrapper's result string.
    """
    text = wrapper.get("result")
    if not isinstance(text, str):
        text = ""
    usage = wrapper.get("usage") or {}
    if blocks is not None:
        content = _blocks_to_content(blocks, text)
    else:
        content = [{"type": "text", "text": text}]
    return {
        "id": _message_id(wrapper),
        "type": "message",
        "role": "assistant",
        "model": model_requested,
        "content": content,
        "stop_reason": wrapper.get("stop_reason") or "end_turn",
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
            "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
        },
    }


class ThinkingFilter:
    """Strip thinking content blocks from a raw event stream — unless the
    request opted in.

    The CLI thinks by default and the proxy forwards events verbatim, so
    without this filter clients that never sent `thinking` would receive
    thinking blocks the hosted API only emits on request. Dropping a block
    means swallowing its start/delta/stop events AND re-indexing every later
    block so the client sees a contiguous sequence.
    """

    _THINKING_TYPES = ("thinking", "redacted_thinking")

    def __init__(self, enabled):
        self._enabled = enabled
        self._dropped = []   # source indices of dropped blocks, ascending

    def _remap(self, event):
        idx = event.get("index")
        if not isinstance(idx, int) or not self._dropped:
            return event
        shift = sum(1 for d in self._dropped if d < idx)
        return dict(event, index=idx - shift) if shift else event

    def feed(self, event):
        """Return the event to forward (possibly re-indexed), or None to drop."""
        if self._enabled:
            return event
        et = event.get("type")
        if et == "content_block_start":
            btype = (event.get("content_block") or {}).get("type")
            if btype in self._THINKING_TYPES:
                self._dropped.append(event.get("index"))
                return None
            return self._remap(event)
        if et in ("content_block_delta", "content_block_stop"):
            if event.get("index") in self._dropped:
                return None
            return self._remap(event)
        return event


def count_tokens(body):
    """Approximate `/v1/messages/count_tokens`. Heuristic (~4 chars/token).

    The CLI exposes no exact pre-flight tokenizer, so this is a best-effort
    estimate — fine for budgeting, not for exact accounting.
    """
    text = extract_system(body) or ""
    for m in body.get("messages", []) or []:
        if isinstance(m, dict):
            text += "\n" + _content_to_text(m.get("content"))
    return {"input_tokens": max(1, len(text) // 4)}


# ---- web search: CLI tool blocks -> API `web_search` content shape ----------
#
# The hosted Messages API represents a web search as structured content blocks:
# a `server_tool_use` (name "web_search"), a `web_search_tool_result` carrying
# `web_search_result` items, and `text` blocks. The local CLI instead emits its
# own `WebSearch` tool_use plus a tool_result *string* ("Links: [{title,url}]").
# These helpers remap CLI blocks to the API shape so clients see the real thing.
#
# Caveats (documented, unavoidable from CLI output):
#   * `encrypted_content` / `encrypted_index` are opaque API-internal tokens the
#     CLI never exposes. We synthesize a deterministic placeholder so the field
#     is well-typed; it is NOT reusable against the hosted API.
#   * `page_age` is unknown from the CLI -> null.
#   * The CLI's final text has no structured citations to reconstruct -> the
#     text block's `citations` is null.

def _remap_tool_id(tid):
    """CLI tool ids are `toolu_...`; the API's server tool uses `srvtoolu_...`."""
    if isinstance(tid, str) and tid.startswith("toolu_"):
        return "srvtoolu_" + tid[len("toolu_"):]
    return tid or "srvtoolu_unknown"


def _synth_encrypted(url, title):
    raw = json.dumps({"u": url, "t": title}, separators=(",", ":")).encode()
    return base64.b64encode(raw).decode()


def _parse_search_links(text):
    """Pull the `Links: [{title,url}, ...]` JSON array out of a WebSearch result.

    The CLI's tool_result string is: a header line, `Links: [...]`, then snippet
    prose and a REMINDER. Only the array is structured, so raw_decode it and
    ignore the trailing prose.
    """
    if not isinstance(text, str):
        return []
    idx = text.find("Links:")
    start = text.find("[", idx) if idx != -1 else -1
    if start == -1:
        return []
    try:
        data, _ = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError:
        return []
    results = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and item.get("url"):
                url, title = item.get("url"), item.get("title", "")
                results.append({
                    "type": "web_search_result",
                    "url": url,
                    "title": title,
                    "encrypted_content": _synth_encrypted(url, title),
                    "page_age": None,
                })
    return results


def web_blocks_to_content(blocks):
    """Turn the CLI's ordered tool blocks into an API `content` array.

    Only WebSearch tool_use/tool_result pairs are mapped; a tool_result is
    emitted as `web_search_tool_result` only when it matches a WebSearch
    tool_use we already saw (the CLI always emits the call before its result),
    so a stray result from any other tool can't produce an orphaned block."""
    content = []
    web_ids = set()
    for b in blocks:
        bt = b.get("type")
        if bt == "text":
            txt = b.get("text", "")
            if txt:
                content.append({"type": "text", "text": txt, "citations": None})
        elif bt == "tool_use" and b.get("name") == "WebSearch":
            tid = b.get("id")
            if not tid:
                continue  # skip rather than coerce to a colliding placeholder id
            web_ids.add(tid)
            content.append({
                "type": "server_tool_use",
                "id": _remap_tool_id(tid),
                "name": "web_search",
                "input": b.get("input") or {},
            })
        elif bt == "tool_result" and b.get("tool_use_id") in web_ids:
            content.append({
                "type": "web_search_tool_result",
                "tool_use_id": _remap_tool_id(b.get("tool_use_id")),
                "content": _parse_search_links(b.get("content")),
            })
    if not content:
        content = [{"type": "text", "text": "", "citations": None}]
    return content


def web_usage(wrapper):
    """Build API usage. The CLI's top-level usage reflects only the final turn;
    `modelUsage` carries per-model totals across the whole loop (incl. the haiku
    sub-model that actually runs searches), so sum those for representative
    totals and the `web_search_requests` count."""
    usage = wrapper.get("usage") or {}
    model_usage = wrapper.get("modelUsage") or {}
    in_tok = out_tok = cache_read = cache_create = web_reqs = 0
    if model_usage:
        for mu in model_usage.values():
            if not isinstance(mu, dict):
                continue
            in_tok += mu.get("inputTokens", 0) or 0
            out_tok += mu.get("outputTokens", 0) or 0
            cache_read += mu.get("cacheReadInputTokens", 0) or 0
            cache_create += mu.get("cacheCreationInputTokens", 0) or 0
            web_reqs += mu.get("webSearchRequests", 0) or 0
    else:
        in_tok = usage.get("input_tokens", 0) or 0
        out_tok = usage.get("output_tokens", 0) or 0
        cache_read = usage.get("cache_read_input_tokens", 0) or 0
        cache_create = usage.get("cache_creation_input_tokens", 0) or 0
    if not web_reqs:
        web_reqs = (usage.get("server_tool_use") or {}).get("web_search_requests", 0) or 0
    return {
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "cache_creation_input_tokens": cache_create,
        "cache_read_input_tokens": cache_read,
        "server_tool_use": {"web_search_requests": web_reqs, "web_fetch_requests": 0},
    }


def web_message(blocks, wrapper, model_requested):
    """Assemble a non-streaming Messages response from a web-enabled run."""
    return {
        "id": _message_id(wrapper),
        "type": "message",
        "role": "assistant",
        "model": model_requested,
        "content": web_blocks_to_content(blocks),
        "stop_reason": wrapper.get("stop_reason") or "end_turn",
        "stop_sequence": None,
        "usage": web_usage(wrapper),
    }


def web_sse_events(content, usage, stop_reason, model_requested, message_id):
    """Yield (event_type, data) pairs reproducing the API's web_search SSE order.

    The CLI's agentic loop is several separate messages; the hosted API folds a
    web search into ONE message with interleaved blocks. We can't faithfully do
    that incrementally, so we buffer the run (see claude.run_web) and replay a
    well-formed single-message stream: message_start, then per-block
    start/delta/stop, then message_delta/message_stop. Shape is exact; only the
    token-by-token timing is lost for web responses."""
    start_usage = dict(usage)
    start_usage["output_tokens"] = 0
    yield ("message_start", {
        "type": "message_start",
        "message": {
            "id": message_id,
            "type": "message",
            "role": "assistant",
            "model": model_requested,
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": start_usage,
        },
    })
    for i, block in enumerate(content):
        bt = block.get("type")
        if bt == "text":
            yield ("content_block_start", {"type": "content_block_start", "index": i,
                                           "content_block": {"type": "text", "text": "", "citations": None}})
            yield ("content_block_delta", {"type": "content_block_delta", "index": i,
                                           "delta": {"type": "text_delta", "text": block.get("text", "")}})
        elif bt == "server_tool_use":
            yield ("content_block_start", {"type": "content_block_start", "index": i,
                                           "content_block": {"type": "server_tool_use", "id": block.get("id"),
                                                             "name": "web_search", "input": {}}})
            yield ("content_block_delta", {"type": "content_block_delta", "index": i,
                                           "delta": {"type": "input_json_delta",
                                                     "partial_json": json.dumps(block.get("input") or {})}})
        elif bt == "web_search_tool_result":
            yield ("content_block_start", {"type": "content_block_start", "index": i, "content_block": block})
        yield ("content_block_stop", {"type": "content_block_stop", "index": i})
    yield ("message_delta", {
        "type": "message_delta",
        "delta": {"stop_reason": stop_reason, "stop_sequence": None},
        "usage": {"output_tokens": usage.get("output_tokens", 0)},
    })
    yield ("message_stop", {"type": "message_stop"})


# ---- client tool use: MCP-shim runs -> API tool_use shape --------------------

def extract_tool_results(body):
    """The `tool_result` blocks of the last user message, if that message is
    made of nothing else (the shape the SDK produces when continuing a tool
    loop). Returns a list of {tool_use_id, content, is_error} or None when the
    last message isn't a pure tool-result turn."""
    msgs = [m for m in body.get("messages", []) or [] if isinstance(m, dict)]
    if not msgs or msgs[-1].get("role") != "user":
        return None
    content = msgs[-1].get("content")
    if not isinstance(content, list) or not content:
        return None
    results = []
    for b in content:
        if not (isinstance(b, dict) and b.get("type") == "tool_result"):
            return None
        results.append({
            "tool_use_id": b.get("tool_use_id"),
            "content": b.get("content"),
            "is_error": bool(b.get("is_error")),
        })
    return results


def rewrite_tool_event(event, model_requested):
    """In-flight rewrite of a raw stream event from a tool-enabled run:
    strip the MCP prefix from tool_use block names and pin the requested model
    id in message_start (the CLI reports the tier it actually ran)."""
    et = event.get("type")
    if et == "message_start":
        message = dict(event.get("message") or {}, model=model_requested)
        return dict(event, message=message)
    if et == "content_block_start":
        cb = event.get("content_block") or {}
        if cb.get("type") == "tool_use":
            return dict(event, content_block=dict(cb, name=strip_mcp_prefix(cb.get("name"))))
    return event


def tool_blocks_to_content(blocks):
    """Shape a tool turn's CLI blocks (thinking/text/tool_use) into API content."""
    content = []
    for b in blocks:
        bt = b.get("type")
        if bt == "text":
            if b.get("text"):
                content.append({"type": "text", "text": b["text"]})
        elif bt == "thinking":
            content.append({"type": "thinking", "thinking": b.get("thinking", ""),
                            "signature": b.get("signature", "")})
        elif bt == "redacted_thinking":
            content.append({"type": "redacted_thinking", "data": b.get("data", "")})
        elif bt == "tool_use":
            content.append({"type": "tool_use", "id": b.get("id"),
                            "name": strip_mcp_prefix(b.get("name")),
                            "input": b.get("input") or {}})
    return content


def tool_use_message(content, usage, model_requested, message_id):
    """Non-streaming response for a turn that ended in tool calls."""
    return {
        "id": message_id,
        "type": "message",
        "role": "assistant",
        "model": model_requested,
        "content": content,
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
            "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
        },
    }


def tool_sse_events(content, usage, stop_reason, model_requested, message_id):
    """Replay a buffered tool turn as a single well-formed SSE message —
    the non-streaming collection path's streaming twin (mirrors
    web_sse_events; token-by-token timing is lost, shape is exact)."""
    start_usage = dict(usage)
    start_usage["output_tokens"] = 0
    yield ("message_start", {
        "type": "message_start",
        "message": {"id": message_id, "type": "message", "role": "assistant",
                    "model": model_requested, "content": [],
                    "stop_reason": None, "stop_sequence": None,
                    "usage": start_usage},
    })
    for i, block in enumerate(content):
        bt = block.get("type")
        if bt == "text":
            yield ("content_block_start", {"type": "content_block_start", "index": i,
                                           "content_block": {"type": "text", "text": ""}})
            yield ("content_block_delta", {"type": "content_block_delta", "index": i,
                                           "delta": {"type": "text_delta",
                                                     "text": block.get("text", "")}})
        elif bt == "tool_use":
            yield ("content_block_start", {"type": "content_block_start", "index": i,
                                           "content_block": {"type": "tool_use",
                                                             "id": block.get("id"),
                                                             "name": block.get("name"),
                                                             "input": {}}})
            yield ("content_block_delta", {"type": "content_block_delta", "index": i,
                                           "delta": {"type": "input_json_delta",
                                                     "partial_json": json.dumps(block.get("input") or {})}})
        elif bt == "thinking":
            yield ("content_block_start", {"type": "content_block_start", "index": i,
                                           "content_block": {"type": "thinking", "thinking": ""}})
            yield ("content_block_delta", {"type": "content_block_delta", "index": i,
                                           "delta": {"type": "thinking_delta",
                                                     "thinking": block.get("thinking", "")}})
            if block.get("signature"):
                yield ("content_block_delta", {"type": "content_block_delta", "index": i,
                                               "delta": {"type": "signature_delta",
                                                         "signature": block["signature"]}})
        elif bt == "redacted_thinking":
            yield ("content_block_start", {"type": "content_block_start", "index": i,
                                           "content_block": block})
        yield ("content_block_stop", {"type": "content_block_stop", "index": i})
    yield ("message_delta", {
        "type": "message_delta",
        "delta": {"stop_reason": stop_reason, "stop_sequence": None},
        "usage": {"output_tokens": usage.get("output_tokens", 0)},
    })
    yield ("message_stop", {"type": "message_stop"})
