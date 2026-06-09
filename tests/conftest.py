"""Shared fixtures. Tests never touch the real `claude` CLI or ~/.misanthropic —
state is redirected to a per-test tmp dir, and the web policy is restored after
each test so order can't leak global state."""

import pytest


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch):
    from misanthropic import sessions, savings

    monkeypatch.setattr(sessions, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(sessions, "KEYS_FILE", tmp_path / "keys.json")
    monkeypatch.setattr(sessions, "SESSIONS_FILE", tmp_path / "sessions.json")
    monkeypatch.setattr(sessions, "WORKSPACE", tmp_path / "workspace")
    # savings.py binds CONFIG_DIR / SAVINGS_FILE at import — patch its own globals.
    monkeypatch.setattr(savings, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(savings, "SAVINGS_FILE", tmp_path / "savings.json")
    # Don't let an inherited env var flip the server into session mode.
    monkeypatch.delenv("MISANTHROPIC_KEYS", raising=False)


@pytest.fixture(autouse=True)
def _restore_web_policy(monkeypatch):
    from misanthropic import claude

    # Pin the current value back after the test (monkeypatch restores the original).
    monkeypatch.setattr(claude, "_web_policy", claude._web_policy)
