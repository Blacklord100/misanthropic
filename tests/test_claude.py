import pytest

from misanthropic import claude


@pytest.mark.parametrize("requested,expected", [
    ("opus", "opus"),
    ("sonnet", "sonnet"),
    ("haiku", "haiku"),
    ("sonnet[1m]", "sonnet[1m]"),                 # alias with suffix passes through
    ("claude-3-5-sonnet-20241022", "sonnet"),     # full id -> tier
    ("claude-opus-4-1", "opus"),
    ("claude-haiku-4-5", "haiku"),
])
def test_cli_model_maps_to_tier(requested, expected):
    assert claude.cli_model(requested) == expected


def test_cli_model_unknown_falls_back_to_default():
    assert claude.cli_model("gpt-4-turbo") == claude.DEFAULT_MODEL
    assert claude.cli_model("") == claude.DEFAULT_MODEL
    assert claude.cli_model(None) == claude.DEFAULT_MODEL


def test_resolve_web_policy_matrix(monkeypatch):
    monkeypatch.setattr(claude, "_web_policy", "auto")
    assert claude.resolve_web(True) is True       # auto honors the request
    assert claude.resolve_web(False) is False

    monkeypatch.setattr(claude, "_web_policy", "on")
    assert claude.resolve_web(False) is True       # force on regardless

    monkeypatch.setattr(claude, "_web_policy", "off")
    assert claude.resolve_web(True) is False        # hard kill-switch


@pytest.mark.parametrize("env,expected", [
    ("1", "on"), ("true", "on"), ("yes", "on"), ("on", "on"),
    ("0", "off"), ("false", "off"), ("off", "off"),
    ("auto", "auto"), ("", "auto"), ("garbage", "auto"),
])
def test_initial_web_policy_env(env, expected, monkeypatch):
    monkeypatch.setenv("MISANTHROPIC_WEB", env)
    assert claude._initial_web_policy() == expected


def test_set_web_policy_validates():
    assert claude.set_web_policy("on") == "on"
    with pytest.raises(ValueError):
        claude.set_web_policy("sometimes")
