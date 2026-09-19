import json

import pytest

from flexrouter.keys import (
    KeyRecord, add_key, allows, load_keys, mask, remove_key, save_keys,
)


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("FLEXROUTER_HOME", str(tmp_path))
    return tmp_path


def test_mask_shows_only_the_last_four():
    assert mask("sk-or-v1-abcdefgh") == "…efgh"
    assert mask("ab") == "…ab"
    assert mask("") == ""


def test_load_keys_is_empty_when_there_is_no_file():
    assert load_keys() == {}


def test_add_key_persists_and_round_trips():
    rec = add_key("openrouter", "sk-or-v1-secret", label="Main account")
    assert rec.id == "openrouter-1"
    assert rec.enabled is True
    assert rec.weight == 1
    assert rec.allow_models == ["*"]
    assert rec.added_at.endswith("Z")

    loaded = load_keys()
    assert [r.secret for r in loaded["openrouter"]] == ["sk-or-v1-secret"]
    assert loaded["openrouter"][0].label == "Main account"


def test_add_key_numbers_ids_within_a_provider():
    add_key("openrouter", "a")
    add_key("openrouter", "b")
    add_key("groq", "c")
    loaded = load_keys()
    assert [r.id for r in loaded["openrouter"]] == ["openrouter-1", "openrouter-2"]
    assert [r.id for r in loaded["groq"]] == ["groq-1"]


def test_remove_key_removes_only_that_key():
    add_key("openrouter", "a")
    add_key("openrouter", "b")
    assert remove_key("openrouter", "openrouter-1") is True
    assert [r.id for r in load_keys()["openrouter"]] == ["openrouter-2"]


def test_remove_key_reports_when_nothing_matched():
    assert remove_key("openrouter", "nope") is False


def test_saved_file_is_json_with_the_expected_shape(_home):
    add_key("openrouter", "sk-secret", label="Main")
    raw = json.loads((_home / "keys.json").read_text(encoding="utf-8"))
    entry = raw["openrouter"][0]
    assert entry["secret"] == "sk-secret"
    assert entry["label"] == "Main"
    assert entry["allow_models"] == ["*"]
    assert entry["enabled"] is True


def test_public_hides_the_secret():
    rec = KeyRecord(id="k", secret="sk-or-v1-abcd")
    pub = rec.public()
    assert "secret" not in pub
    assert pub["masked"] == "…abcd"


def test_allow_models_globs_restrict_a_key():
    free_only = KeyRecord(id="k", secret="s", allow_models=["*:free"])
    assert allows(free_only, "deepseek/deepseek-chat-v3.1:free") is True
    assert allows(free_only, "openai/gpt-4o") is False

    wide = KeyRecord(id="k", secret="s")
    assert allows(wide, "anything/at-all") is True


def test_save_keys_accepts_records_and_reloads_them():
    save_keys({"groq": [KeyRecord(id="groq-1", secret="gsk", weight=3)]})
    assert load_keys()["groq"][0].weight == 3
