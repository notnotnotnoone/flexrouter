import pytest

from flexrouter.wire import Target, bucket_id, model_id, parse_model, resolve


def test_bare_name_is_a_bucket():
    assert parse_model("smart") == Target("bucket", "smart")


def test_name_with_a_slash_pins_one_model():
    assert parse_model("groq/llama-3.3-70b-versatile") == Target(
        "pin", "groq/llama-3.3-70b-versatile")


def test_auto_is_a_bucket_named_auto():
    assert parse_model("auto") == Target("bucket", "auto")


def test_legacy_auto_prefix_still_names_a_bucket():
    assert parse_model("auto-smart") == Target("bucket", "smart")


def test_legacy_double_colon_form_now_pins_the_model():
    assert parse_model("smart::groq/llama-3.3-70b-versatile") == Target(
        "pin", "groq/llama-3.3-70b-versatile")


def test_empty_model_falls_back_to_auto():
    assert parse_model("") == Target("bucket", "auto")


def test_resolve_passes_a_known_bucket_through():
    assert resolve(Target("bucket", "fast"), ["smart", "fast"], "smart") == "fast"


def test_resolve_auto_uses_the_best_bucket():
    assert resolve(Target("bucket", "auto"), ["smart", "fast"], "smart") == "smart"


def test_resolve_unknown_bucket_raises_and_names_the_real_ones():
    with pytest.raises(KeyError) as exc:
        resolve(Target("bucket", "nope"), ["smart", "fast"], "smart")
    assert "nope" in str(exc.value)
    assert "smart" in str(exc.value)


def test_resolve_pin_passes_the_model_name_through():
    assert resolve(Target("pin", "groq/x"), ["smart"], "smart") == "groq/x"


def test_ids_are_what_v1_models_advertises():
    assert bucket_id("smart") == "smart"
    assert model_id("groq", "llama-3.3") == "groq/llama-3.3"
