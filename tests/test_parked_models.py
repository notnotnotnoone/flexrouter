"""flexrouter/parked_models.py: state/parked_models.json, where a non-chat
model from the "Add models with AI" flow lands since it can never go in a
bucket (see flexrouter/dashboard/add_models.py's docstring for why)."""
from flexrouter import parked_models


def test_starts_empty(tmp_path):
    assert parked_models.all(str(tmp_path)) == []


def test_add_saves_every_field_and_reports_it_as_new(tmp_path):
    is_update = parked_models.add(str(tmp_path), "openai", "whisper-1", {
        "kind": "speech_to_text", "context": None, "rpm": 50, "tpm": None,
        "vision": False, "free": False, "score": 80, "source": "ai_paste",
    })
    assert is_update is False
    entry = parked_models.get(str(tmp_path), "openai", "whisper-1")
    assert entry["provider"] == "openai"
    assert entry["model"] == "whisper-1"
    assert entry["kind"] == "speech_to_text"
    assert entry["rpm"] == 50
    assert entry["score"] == 80


def test_adding_the_same_identity_again_is_reported_as_an_update(tmp_path):
    parked_models.add(str(tmp_path), "openai", "whisper-1", {"kind": "speech_to_text"})
    is_update = parked_models.add(str(tmp_path), "openai", "whisper-1", {"kind": "speech_to_text",
                                                                          "score": 90})
    assert is_update is True
    assert parked_models.get(str(tmp_path), "openai", "whisper-1")["score"] == 90


def test_all_is_sorted_by_provider_then_model(tmp_path):
    parked_models.add(str(tmp_path), "openai", "z-model", {"kind": "image"})
    parked_models.add(str(tmp_path), "anthropic", "a-model", {"kind": "embedding"})
    parked_models.add(str(tmp_path), "openai", "a-model", {"kind": "image"})
    idents = [(e["provider"], e["model"]) for e in parked_models.all(str(tmp_path))]
    assert idents == [("anthropic", "a-model"), ("openai", "a-model"), ("openai", "z-model")]


def test_get_of_an_unknown_identity_is_none(tmp_path):
    assert parked_models.get(str(tmp_path), "nope", "nothing") is None


def test_survives_across_separate_calls_reading_the_same_state_dir(tmp_path):
    parked_models.add(str(tmp_path), "openai", "dall-e-3", {"kind": "image"})
    reloaded = parked_models.all(str(tmp_path))
    assert len(reloaded) == 1
    assert reloaded[0]["model"] == "dall-e-3"
