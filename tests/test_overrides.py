import pytest

from flexrouter.overrides import (
    add_bucket, add_model, add_provider,
    apply_overrides, clear_override, load_overrides, save_overrides, set_override,
)


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("FLEXROUTER_HOME", str(tmp_path))
    return tmp_path


BASE = {
    "settings": {"port": 4891, "window_seconds": 60},
    "providers": {"openrouter": {"base_url": "https://openrouter.ai/api/v1"}},
    "buckets": {
        "smart": [
            {"provider": "openrouter", "model": "deepseek-chat", "score": 90,
             "rpm": 20, "tpm": 10000},
            {"provider": "openrouter", "model": "gone-model", "score": 50,
             "rpm": 20, "tpm": 10000},
        ]
    },
}


def test_load_overrides_is_empty_at_first():
    assert load_overrides() == {}


def test_set_and_load_an_override():
    set_override("models", "openrouter/deepseek-chat", "score", 75)
    assert load_overrides()["models"]["openrouter/deepseek-chat"]["score"] == 75


def test_set_override_on_settings_uses_the_key_as_the_name():
    set_override("settings", "port", None, 7000)
    assert load_overrides()["settings"]["port"] == 7000


def test_set_override_rejects_an_unknown_section():
    with pytest.raises(ValueError):
        set_override("nonsense", "x", "y", 1)


def test_clear_override_removes_the_entry():
    set_override("models", "openrouter/deepseek-chat", "score", 75)
    assert clear_override("models", "openrouter/deepseek-chat") is True
    assert load_overrides().get("models", {}) == {}


def test_clear_override_reports_when_nothing_matched():
    assert clear_override("models", "openrouter/nope") is False


def test_apply_overrides_leaves_the_original_untouched():
    merged = apply_overrides(BASE, {"settings": {"port": 9999}})
    assert merged["settings"]["port"] == 9999
    assert BASE["settings"]["port"] == 4891


def test_apply_overrides_merges_settings_field_by_field():
    merged = apply_overrides(BASE, {"settings": {"port": 9999}})
    assert merged["settings"]["window_seconds"] == 60


def test_apply_overrides_merges_a_provider_field():
    merged = apply_overrides(
        BASE, {"providers": {"openrouter": {"base_url": "http://localhost:1234/v1"}}})
    assert merged["providers"]["openrouter"]["base_url"] == "http://localhost:1234/v1"


def test_apply_overrides_changes_a_model_score():
    merged = apply_overrides(
        BASE, {"models": {"openrouter/deepseek-chat": {"score": 75}}})
    smart = merged["buckets"]["smart"]
    assert smart[0]["score"] == 75
    assert smart[0]["rpm"] == 20


def test_apply_overrides_disabling_a_model_drops_it_from_the_bucket():
    merged = apply_overrides(
        BASE, {"models": {"openrouter/gone-model": {"enabled": False}}})
    assert [m["model"] for m in merged["buckets"]["smart"]] == ["deepseek-chat"]


def test_apply_overrides_also_understands_the_tiers_spelling():
    base = {"tiers": {"fast": [{"provider": "groq", "model": "llama", "score": 80,
                                "rpm": 30, "tpm": 6000}]}}
    merged = apply_overrides(base, {"models": {"groq/llama": {"score": 10}}})
    assert merged["tiers"]["fast"][0]["score"] == 10


def test_apply_overrides_with_nothing_set_is_a_faithful_copy():
    assert apply_overrides(BASE, {}) == BASE


def test_save_overrides_round_trips():
    save_overrides({"settings": {"port": 1}})
    assert load_overrides() == {"settings": {"port": 1}}


@pytest.mark.parametrize("off", [False, 0, "false", "False", "0", "off", "no", None])
def test_a_model_is_disabled_by_any_spelling_of_off(off):
    """`enabled` arrives in a JSON body, so "off" turns up in whatever shape
    the caller sent. Testing `is False` recognised only one of them."""
    merged = apply_overrides(
        BASE, {"models": {"openrouter/gone-model": {"enabled": off}}})
    models = [m["model"] for m in merged["buckets"]["smart"]]
    assert "gone-model" not in models


@pytest.mark.parametrize("on", [True, 1, "true", "yes"])
def test_a_model_stays_when_enabled_says_so(on):
    merged = apply_overrides(
        BASE, {"models": {"openrouter/gone-model": {"enabled": on}}})
    models = [m["model"] for m in merged["buckets"]["smart"]]
    assert "gone-model" in models


# --- Stage 8 sub-plan 7: adding a provider, a bucket, a model -------------


def test_add_provider_requires_a_base_url():
    with pytest.raises(ValueError):
        add_provider("newprov", {})


def test_add_provider_rejects_a_field_outside_the_allowed_set():
    with pytest.raises(ValueError):
        add_provider("newprov", {"base_url": "https://x/v1", "api_key": "sk-x"})


def test_add_provider_refuses_a_name_that_already_has_changes():
    add_provider("newprov", {"base_url": "https://x/v1"})
    with pytest.raises(ValueError):
        add_provider("newprov", {"base_url": "https://y/v1"})


def test_a_new_provider_appears_after_apply_overrides():
    add_provider("newprov", {"base_url": "https://x/v1", "header_parser": "openai_compatible"})
    merged = apply_overrides(BASE, load_overrides())
    assert merged["providers"]["newprov"]["base_url"] == "https://x/v1"
    # The original provider must still be there, untouched.
    assert merged["providers"]["openrouter"]["base_url"] == BASE["providers"]["openrouter"]["base_url"]


def test_add_bucket_creates_an_empty_bucket():
    add_bucket("experimental")
    merged = apply_overrides(BASE, load_overrides())
    assert merged["buckets"]["experimental"] == []


def test_add_bucket_is_idempotent():
    add_bucket("experimental")
    add_bucket("experimental")
    assert load_overrides()["new_buckets"] == ["experimental"]


def test_add_model_requires_provider_and_model():
    with pytest.raises(ValueError):
        add_model("smart", {"score": 90, "rpm": 20, "tpm": 10000})


def test_add_model_requires_score_rpm_tpm():
    with pytest.raises(ValueError):
        add_model("smart", {"provider": "openrouter", "model": "brand-new"})


def test_add_model_rejects_an_unknown_field():
    with pytest.raises(ValueError):
        add_model("smart", {
            "provider": "openrouter", "model": "brand-new",
            "score": 90, "rpm": 20, "tpm": 10000, "api_key": "sk-x",
        })


def test_a_new_model_appears_in_its_bucket_after_apply_overrides():
    add_model("smart", {
        "provider": "openrouter", "model": "brand-new",
        "score": 90, "rpm": 20, "tpm": 10000,
    })
    merged = apply_overrides(BASE, load_overrides())
    models = [m["model"] for m in merged["buckets"]["smart"]]
    assert "brand-new" in models
    # The models that were already there must still be there too.
    assert "deepseek-chat" in models


def test_a_new_model_can_land_in_a_new_bucket():
    add_bucket("experimental")
    add_model("experimental", {
        "provider": "openrouter", "model": "brand-new",
        "score": 90, "rpm": 20, "tpm": 10000,
    })
    merged = apply_overrides(BASE, load_overrides())
    assert [m["model"] for m in merged["buckets"]["experimental"]] == ["brand-new"]


def test_a_new_model_is_not_droppable_by_an_enabled_override_it_never_had():
    add_model("smart", {
        "provider": "openrouter", "model": "brand-new",
        "score": 90, "rpm": 20, "tpm": 10000,
    })
    # An unrelated model override must not accidentally sweep up the new one.
    ov = load_overrides()
    ov.setdefault("models", {})["openrouter/gone-model"] = {"enabled": False}
    merged = apply_overrides(BASE, ov)
    models = [m["model"] for m in merged["buckets"]["smart"]]
    assert "brand-new" in models
    assert "gone-model" not in models
