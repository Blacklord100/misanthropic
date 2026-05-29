"""Translate between the Anthropic Messages API schema and the local CLI.

The hosted Messages API takes a `system` string plus a `messages` array; the
`claude -p` CLI takes one system prompt and one stdin prompt. These helpers
flatten a request into that shape and build a spec-shaped response back.
"""

import json


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
            inner = block.get("content")
            parts.append(_content_to_text(inner) if inner is not None else "")
        elif btype == "tool_use":
            parts.append(f"[tool_use {block.get('name', '')}: {json.dumps(block.get('input', {}))}]")
        elif btype == "image":
            parts.append("[image omitted — image input is not supported by the CLI proxy]")
    return "\n".join(p for p in parts if p)


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


def wrapper_to_message(wrapper, model_requested):
    """Build an Anthropic Messages response from the CLI's JSON wrapper."""
    text = wrapper.get("result")
    if not isinstance(text, str):
        text = ""
    usage = wrapper.get("usage") or {}
    return {
        "id": _message_id(wrapper),
        "type": "message",
        "role": "assistant",
        "model": model_requested,
        "content": [{"type": "text", "text": text}],
        "stop_reason": wrapper.get("stop_reason") or "end_turn",
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
            "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
        },
    }


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
