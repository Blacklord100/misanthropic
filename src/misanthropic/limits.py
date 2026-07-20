"""Client-side enforcement of `stop_sequences` and (opt-in) `max_tokens`.

The CLI exposes neither knob, so the proxy enforces them on the way out:
`stop_sequences` whenever a request supplies them (a client that sends them
wants them honored), `max_tokens` only when enabled — the count is the same
~4 chars/token estimate as count_tokens(), not the real tokenizer, so cutting
every response at an approximate boundary is a behavior change users opt into
(Settings, or MISANTHROPIC_ENFORCE_MAX_TOKENS=1).

LimitGate is fed streamed text deltas. It withholds just enough tail to catch
a stop sequence straddling two deltas, so callers must flush() at end-of-block
to emit the held-back text.
"""

import os

from . import settings

# Chars-per-token heuristic shared with translate.count_tokens().
CHARS_PER_TOKEN = 4

_TRUTHY = ("1", "true", "yes", "on")
_FALSY = ("0", "false", "no", "off")


def max_tokens_enforced():
    """Whether max_tokens is enforced: env wins, else the persisted setting."""
    raw = os.environ.get("MISANTHROPIC_ENFORCE_MAX_TOKENS", "").strip().lower()
    if raw in _TRUTHY:
        return True
    if raw in _FALSY:
        return False
    return bool(settings.get("enforce_max_tokens"))


class LimitGate:
    """Scan streamed text for a stop sequence / token-budget overrun.

    feed(text) -> (emit_now, finished) — `emit_now` is the text safe to send to
    the client; once `finished` is True the caller must emit nothing further
    and terminate the stream. After a hit, `stop_reason` is "stop_sequence" or
    "max_tokens" and `stop_sequence` is the matched string (or None).
    flush() returns any withheld tail (empty after a hit).
    """

    def __init__(self, stop_sequences=None, max_tokens=None):
        seqs = [s for s in (stop_sequences or []) if isinstance(s, str) and s]
        self._seqs = seqs
        self._hold = max(len(s) for s in seqs) - 1 if seqs else 0
        self._buf = ""
        self._budget = (int(max_tokens) * CHARS_PER_TOKEN
                        if max_tokens and max_tokens_enforced() else None)
        self._emitted = 0
        self.stop_reason = None
        self.stop_sequence = None

    @property
    def active(self):
        return bool(self._seqs) or self._budget is not None

    @property
    def finished(self):
        return self.stop_reason is not None

    def _cap(self, text):
        """Truncate `text` to the remaining token budget; set state on overrun."""
        if self._budget is not None and self._emitted + len(text) > self._budget:
            text = text[:self._budget - self._emitted]
            self.stop_reason = "max_tokens"
        self._emitted += len(text)
        return text

    def feed(self, text):
        if self.finished:
            return "", True
        self._buf += text
        # Earliest stop-sequence hit wins, regardless of which sequence it is.
        hit_at, hit_seq = -1, None
        for s in self._seqs:
            i = self._buf.find(s)
            if i != -1 and (hit_at == -1 or i < hit_at):
                hit_at, hit_seq = i, s
        if hit_at != -1:
            emit = self._cap(self._buf[:hit_at])
            self._buf = ""
            # A budget overrun inside the pre-match text takes precedence
            # (it happened first in stream order); _cap already set it.
            if self.stop_reason is None:
                self.stop_reason = "stop_sequence"
                self.stop_sequence = hit_seq
            return emit, True
        keep = self._hold
        emit = self._buf[:-keep] if keep else self._buf
        self._buf = self._buf[-keep:] if keep else ""
        emit = self._cap(emit)
        return emit, self.finished

    def flush(self):
        """End of block/stream with no hit: release the withheld tail."""
        if self.finished:
            return ""
        emit, self._buf = self._cap(self._buf), ""
        return emit

    def emitted_tokens(self):
        """Approximate output tokens actually sent — for the synthesized
        message_delta usage after an early cut."""
        return max(1, self._emitted // CHARS_PER_TOKEN)


def apply_to_message(msg, stop_sequences=None, max_tokens=None):
    """Enforce limits on a buffered Messages response (single text block).

    Mutates and returns `msg`. Multi-block responses (web search) keep their
    documented honest-gap behavior and are left alone by the callers.
    """
    content = msg.get("content") or []
    if len(content) == 1 and content[0].get("type") == "text":
        new, reason, seq = truncate_text(content[0].get("text", ""),
                                         stop_sequences, max_tokens)
        if reason:
            content[0]["text"] = new
            msg["stop_reason"] = reason
            msg["stop_sequence"] = seq
            usage = msg.setdefault("usage", {})
            usage["output_tokens"] = max(1, len(new) // CHARS_PER_TOKEN)
    return msg


def truncate_text(text, stop_sequences=None, max_tokens=None):
    """Post-hoc enforcement for buffered (non-streaming) responses.

    Returns (text, stop_reason, stop_sequence) — stop_reason is None when
    nothing triggered.
    """
    gate = LimitGate(stop_sequences, max_tokens)
    if not gate.active or not isinstance(text, str):
        return text, None, None
    emit, _ = gate.feed(text)
    emit += gate.flush()
    return emit, gate.stop_reason, gate.stop_sequence
