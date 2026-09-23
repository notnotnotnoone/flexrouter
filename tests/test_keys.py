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
    assert mask("") == ""


def test_mask_shows_nothing_at_all_below_four_characters():
    """Under four characters there is nothing to show without showing the
    whole thing, so nothing is shown."""
    assert mask("ab") == "…"
    assert mask("abc") == "…"
    assert mask("abcd") == "…abcd"


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


def test_add_key_after_removing_middle_gets_a_fresh_unused_id():
    add_key("groq", "a")
    add_key("groq", "b")
    add_key("groq", "c")
    remove_key("groq", "groq-2")
    rec = add_key("groq", "d")
    assert rec.id == "groq-4"
    ids = [r.id for r in load_keys()["groq"]]
    assert len(ids) == len(set(ids))


def test_add_key_ids_stay_unique_across_add_remove_add():
    ids = []
    for secret in ("a", "b", "c"):
        ids.append(add_key("groq", secret).id)
    remove_key("groq", ids[1])
    ids.append(add_key("groq", "d").id)
    remove_key("groq", ids[0])
    ids.append(add_key("groq", "e").id)
    assert len(ids) == len(set(ids))


def test_add_key_ignores_non_conforming_ids_when_allocating():
    save_keys({"groq": [KeyRecord(id="imported-by-hand", secret="gsk-x")]})
    rec = add_key("groq", "fresh")
    assert rec.id == "groq-1"
    ids = [r.id for r in load_keys()["groq"]]
    assert len(ids) == len(set(ids))


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


# -- update_key tests --------------------------------------------------

from flexrouter import keys as keystore


def test_a_key_can_be_renamed_without_touching_its_secret(tmp_path):
    path = tmp_path / "keys.json"
    made = keystore.add_key("groq", "sk-secret-value", "first", path=path)
    updated = keystore.update_key("groq", made.id, label="renamed", path=path)
    assert updated.label == "renamed"
    # The secret is not a field this function can reach.
    assert keystore.load_keys(path)["groq"][0].secret == "sk-secret-value"


def test_a_key_can_be_disabled_and_re_enabled(tmp_path):
    path = tmp_path / "keys.json"
    made = keystore.add_key("groq", "sk-x", path=path)
    assert keystore.update_key("groq", made.id, enabled=False, path=path).enabled is False
    assert keystore.update_key("groq", made.id, enabled=True, path=path).enabled is True


def test_weight_and_globs_are_editable(tmp_path):
    path = tmp_path / "keys.json"
    made = keystore.add_key("groq", "sk-x", path=path)
    out = keystore.update_key(
        "groq", made.id, weight=5, allow_models=["llama-*", "gemma-*"], path=path)
    assert out.weight == 5
    assert out.allow_models == ["llama-*", "gemma-*"]


def test_an_omitted_field_is_left_alone(tmp_path):
    # The difference between "set this to None" and "do not touch this" is
    # the whole reason every parameter defaults to None rather than to its
    # own empty value.
    path = tmp_path / "keys.json"
    made = keystore.add_key("groq", "sk-x", "keep me", path=path)
    keystore.update_key("groq", made.id, weight=9, path=path)
    assert keystore.load_keys(path)["groq"][0].label == "keep me"


def test_updating_a_key_that_is_not_there_answers_none(tmp_path):
    path = tmp_path / "keys.json"
    keystore.add_key("groq", "sk-x", path=path)
    assert keystore.update_key("groq", "groq-99", label="x", path=path) is None
    assert keystore.update_key("nobody", "nobody-1", label="x", path=path) is None


def test_an_empty_glob_list_means_all_models(tmp_path):
    path = tmp_path / "keys.json"
    made = keystore.add_key("groq", "sk-x", path=path)
    out = keystore.update_key("groq", made.id, allow_models=[], path=path)
    assert out.allow_models == ["*"]
    assert keystore.allows(out, "anything-at-all") is True
