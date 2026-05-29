"""End-to-end smoke test for a running Breakthrough server.

Setup:

    python3 -m venv .venv
    .venv/bin/pip install anthropic
    .venv/bin/python examples/test-breakthrough.py

Prereqs:
    - Breakthrough.app is running (or `breakthrough serve`) on 127.0.0.1:8787.
    - At least one approved key. Create one from the dashboard at
      http://127.0.0.1:8787, copy it, and either:
        * export ANTHROPIC_API_KEY=sk-ant-local-...    (recommended)
        * or edit API_KEY below.

What it exercises:
    1) /health               — liveness
    2) messages.create       — plain text generation (non-streaming)
    3) messages.stream       — SSE streaming, token-by-token
    4) messages.count_tokens — pre-flight estimate
    5) key-linked session    — turn 1 sets context, turn 2 recalls it from memory

While it runs, watch http://127.0.0.1:8787/ — each request appears live in the
"Recent activity" panel (toggle "Show previews" to see the prompt/response).
"""
import json
import os
import urllib.request

from anthropic import Anthropic

BASE_URL = os.environ.get("ANTHROPIC_BASE_URL", "http://127.0.0.1:8787")
API_KEY = os.environ.get("ANTHROPIC_API_KEY") or "sk-ant-local-REPLACE-ME"
MODEL = os.environ.get("BREAKTHROUGH_TEST_MODEL", "claude-sonnet-4-6")

if "REPLACE-ME" in API_KEY:
    raise SystemExit(
        "Set ANTHROPIC_API_KEY to a key from the Breakthrough dashboard "
        "(http://127.0.0.1:8787) or edit examples/test-breakthrough.py."
    )

client = Anthropic(base_url=BASE_URL, api_key=API_KEY)


def section(title):
    print(f"\n=== {title} ===")


# 1. Liveness
section("1) /health")
with urllib.request.urlopen(f"{BASE_URL}/health") as r:
    print(json.load(r))

# 2. Plain non-streaming generation
section("2) messages.create (non-streaming)")
msg = client.messages.create(
    model=MODEL,
    max_tokens=128,
    system="You are a terse coding assistant.",
    messages=[{"role": "user", "content": "One sentence: what does the Python walrus operator do?"}],
)
print(msg.content[0].text)
print(f"  usage: {msg.usage.input_tokens} in / {msg.usage.output_tokens} out")

# 3. Streaming
section("3) messages.stream")
with client.messages.stream(
    model=MODEL,
    max_tokens=128,
    messages=[{"role": "user", "content": "Count to five, one number per line."}],
) as stream:
    for text in stream.text_stream:
        print(text, end="", flush=True)
    print()
    final = stream.get_final_message()
    print(f"  stop_reason={final.stop_reason} output_tokens={final.usage.output_tokens}")

# 4. Token counting
section("4) messages.count_tokens (estimate)")
est = client.messages.count_tokens(
    model=MODEL,
    messages=[{"role": "user", "content": "Hello, how are you today? " * 20}],
)
print(f"  estimated input_tokens: {est.input_tokens}")

# 5. Key-linked session: the key both authorizes AND names the conversation.
#    Send only the new turn each request — the server remembers the rest.
section("5) key-linked session (turn 1 -> turn 2 recall)")
client.messages.create(
    model=MODEL, max_tokens=64,
    messages=[{"role": "user", "content": "My favorite color is teal. Remember it."}],
)
recall = client.messages.create(
    model=MODEL, max_tokens=64,
    messages=[{"role": "user", "content": "What is my favorite color?"}],
)
print(recall.content[0].text)

print("\nAll checks passed.")
