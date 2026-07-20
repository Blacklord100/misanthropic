"""Unit tests for limits.py: stop-sequence scanning across delta boundaries and
the approximate max_tokens budget."""

import pytest

from misanthropic import limits
from misanthropic.limits import LimitGate, truncate_text


@pytest.fixture()
def enforce_max_tokens(monkeypatch):
    monkeypatch.setenv("MISANTHROPIC_ENFORCE_MAX_TOKENS", "1")


def _drain(gate, deltas):
    out = []
    for d in deltas:
        emit, done = gate.feed(d)
        out.append(emit)
        if done:
            return "".join(out), True
    out.append(gate.flush())
    return "".join(out), gate.finished


def test_no_sequences_passes_through():
    gate = LimitGate([], None)
    assert not gate.active


def test_stop_hit_within_one_delta():
    gate = LimitGate(["STOP"])
    text, done = _drain(gate, ["hello STOP world"])
    assert (text, done) == ("hello ", True)
    assert gate.stop_reason == "stop_sequence"
    assert gate.stop_sequence == "STOP"


def test_stop_straddles_delta_boundary():
    gate = LimitGate(["STOP"])
    text, done = _drain(gate, ["hello ST", "OP world"])
    assert (text, done) == ("hello ", True)
    assert gate.stop_sequence == "STOP"


def test_stop_straddles_three_deltas():
    gate = LimitGate(["-->"])
    text, done = _drain(gate, ["a-", "-", ">b"])
    assert (text, done) == ("a", True)


def test_earliest_of_multiple_sequences_wins():
    gate = LimitGate(["zzz", "b"])
    text, done = _drain(gate, ["abc zzz"])
    assert (text, done) == ("a", True)
    assert gate.stop_sequence == "b"


def test_no_hit_flushes_everything():
    gate = LimitGate(["STOP"])
    text, done = _drain(gate, ["hello ", "world"])
    assert (text, done) == ("hello world", False)
    assert gate.stop_reason is None


def test_max_tokens_ignored_by_default():
    gate = LimitGate([], max_tokens=1)
    assert not gate.active


def test_max_tokens_budget(enforce_max_tokens):
    gate = LimitGate([], max_tokens=2)  # ~8 chars
    text, done = _drain(gate, ["abcdef", "ghijkl"])
    assert (text, done) == ("abcdefgh", True)
    assert gate.stop_reason == "max_tokens"
    assert gate.stop_sequence is None
    assert gate.emitted_tokens() == 2


def test_budget_overrun_before_stop_sequence_wins(enforce_max_tokens):
    gate = LimitGate(["STOP"], max_tokens=1)  # 4 chars
    text, done = _drain(gate, ["abcdefgh STOP x"])
    assert (text, done) == ("abcd", True)
    assert gate.stop_reason == "max_tokens"


def test_truncate_text_blocking():
    text, reason, seq = truncate_text("one\n\nHuman: two", stop_sequences=["\n\nHuman:"])
    assert (text, reason, seq) == ("one", "stop_sequence", "\n\nHuman:")


def test_truncate_text_no_trigger():
    text, reason, seq = truncate_text("plain answer", stop_sequences=["STOP"])
    assert (text, reason, seq) == ("plain answer", None, None)


def test_apply_to_message(enforce_max_tokens):
    msg = {"content": [{"type": "text", "text": "x" * 100}],
           "stop_reason": "end_turn", "stop_sequence": None,
           "usage": {"input_tokens": 5, "output_tokens": 25}}
    out = limits.apply_to_message(msg, None, 10)
    assert out["content"][0]["text"] == "x" * 40
    assert out["stop_reason"] == "max_tokens"
    assert out["usage"]["output_tokens"] == 10


def test_enforcement_env_off_beats_setting(monkeypatch):
    from misanthropic import settings
    settings.update({"enforce_max_tokens": True})
    assert limits.max_tokens_enforced()
    monkeypatch.setenv("MISANTHROPIC_ENFORCE_MAX_TOKENS", "off")
    assert not limits.max_tokens_enforced()
