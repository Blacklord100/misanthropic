import json

from misanthropic import sessions


def test_approve_and_remove_keys():
    assert sessions.approved_keys() == set()
    assert sessions.session_mode_enabled() is False

    sessions.add_key("alpha")
    assert "alpha" in sessions.approved_keys()
    assert sessions.is_approved("alpha") is True
    assert sessions.session_mode_enabled() is True

    sessions.remove_key("alpha")
    assert sessions.approved_keys() == set()


def test_create_key_looks_like_anthropic_key():
    key = sessions.create_key("my-project")
    assert key.startswith("sk-ant-local-")
    assert sessions.is_approved(key)
    assert sessions.keys_detail()[key]["label"] == "my-project"


def test_session_record_increments_turns_and_forget():
    sessions.record_session("k", "sess-1")
    sessions.record_session("k", "sess-2")
    assert sessions.get_session_id("k") == "sess-2"
    assert sessions.all_sessions()["k"]["turns"] == 2

    sessions.forget_session("k")
    assert sessions.get_session_id("k") is None


def test_legacy_list_keys_file_is_read(tmp_path):
    # Older versions stored keys as a bare JSON list; it must still load.
    sessions.KEYS_FILE.write_text(json.dumps(["legacy1", "legacy2"]))
    assert sessions.approved_keys() == {"legacy1", "legacy2"}


def test_env_keys_merge(monkeypatch):
    monkeypatch.setenv("MISANTHROPIC_KEYS", "env1, env2 ,")
    sessions.add_key("stored")
    assert sessions.approved_keys() == {"stored", "env1", "env2"}
