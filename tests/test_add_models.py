"""flexrouter/dashboard/add_models.py - the prompt builder and the pasted-
answer parser, tested hard since it is the one place free-form AI output
turns into something this app will act on.
"""
import json
from types import SimpleNamespace

from flexrouter.dashboard import add_models


def _provider(keys):
    return SimpleNamespace(keys=keys)


def _router(providers):
    return SimpleNamespace(_cfg=SimpleNamespace(providers=providers))


def _obj(**overrides) -> dict:
    obj = {
        "provider": "groq", "model": "m", "kind": "chat", "context": None,
        "rpm": None, "tpm": None, "rph": None, "rpd": None, "rps": None,
        "tph": None, "tpd": None, "tps": None,
        "vision": False, "free": False, "score": None,
    }
    obj.update(overrides)
    return obj


def test_providers_with_keys_only_lists_providers_that_have_one():
    router = _router({
        "groq": _provider([SimpleNamespace(id="k1")]),
        "empty": _provider([]),
        "openai": _provider([SimpleNamespace(id="k2"), SimpleNamespace(id="k3")]),
    })
    assert add_models.providers_with_keys(router) == ["groq", "openai"]


def test_build_prompt_names_the_provider_and_existing_models():
    prompt = add_models.build_prompt("groq", ["llama-3.1-8b-instant"], "")
    assert "groq" in prompt
    assert "llama-3.1-8b-instant" in prompt
    assert "JSON array" in prompt
    for field in ("provider", "model", "kind", "context", "rpm", "tpm",
                  "rph", "rpd", "rps", "tph", "tpd", "tps", "vision", "free", "score"):
        assert f'"{field}"' in prompt


def test_build_prompt_with_no_existing_models_says_so():
    prompt = add_models.build_prompt("groq", [], "")
    assert "Nothing is configured for groq yet." in prompt


def test_build_prompt_includes_pasted_notes():
    prompt = add_models.build_prompt("groq", [], "Groq's docs say X")
    assert "Groq's docs say X" in prompt


def test_build_prompt_tells_the_ai_to_write_null_rather_than_guess():
    prompt = add_models.build_prompt("groq", [], "")
    assert "null" in prompt.lower()
    assert "guess" in prompt.lower()


def test_build_prompt_lists_the_allowed_kinds():
    prompt = add_models.build_prompt("groq", [], "")
    for kind in add_models.KINDS:
        assert kind in prompt


# ── parse_answer: the happy path ───────────────────────────────────────

def test_parses_a_well_formed_object():
    result = add_models.parse_answer(json.dumps([_obj(
        model="llama-3.1-8b-instant", context=131072, rpm=30, tpm=6000,
        free=True, score=85,
    )]))
    assert result.issues == []
    assert len(result.rows) == 1
    row = result.rows[0]
    assert row.provider == "groq"
    assert row.model == "llama-3.1-8b-instant"
    assert row.kind == "chat"
    assert row.context == 131072
    assert row.rpm == 30
    assert row.tpm == 6000
    assert row.vision is False
    assert row.free is True
    assert row.score == 85


def test_parses_all_the_rate_and_quota_fields():
    result = add_models.parse_answer(json.dumps([_obj(
        rpm=30, tpm=6000, rph=1000, rpd=20000, rps=2, tph=100000, tpd=2000000, tps=50,
    )]))
    row = result.rows[0]
    assert (row.rpm, row.tpm, row.rph, row.rpd, row.rps, row.tph, row.tpd, row.tps) == (
        30, 6000, 1000, 20000, 2, 100000, 2000000, 50)


def test_parses_null_fields():
    result = add_models.parse_answer(json.dumps([_obj(kind="embedding")]))
    assert result.issues == []
    row = result.rows[0]
    assert row.context is None
    assert row.rpm is None
    assert row.tpm is None
    assert row.rph is None
    assert row.rpd is None
    assert row.rps is None
    assert row.tph is None
    assert row.tpd is None
    assert row.tps is None
    assert row.score is None


def test_string_none_is_also_accepted_case_insensitively():
    obj = _obj(context="NONE", rpm="None", tpm="nOnE", score="none")
    result = add_models.parse_answer(json.dumps([obj]))
    assert result.issues == []
    row = result.rows[0]
    assert row.context is None and row.rpm is None and row.tpm is None and row.score is None


def test_kind_is_case_insensitive_and_normalized():
    result = add_models.parse_answer(json.dumps([_obj(kind="CHAT")]))
    assert result.issues == []
    assert result.rows[0].kind == "chat"


def test_multiple_objects_all_parse():
    result = add_models.parse_answer(json.dumps([
        _obj(model="a", context=1000, rpm=10, tpm=1000, score=50),
        _obj(model="b", context=2000, rpm=20, tpm=2000, vision=True, free=True, score=60),
    ]))
    assert result.issues == []
    assert [r.model for r in result.rows] == ["a", "b"]


# ── tolerance for stray formatting around the JSON ──────────────────────

def test_code_fences_are_stripped():
    text = "```json\n" + json.dumps([_obj()]) + "\n```"
    result = add_models.parse_answer(text)
    assert result.issues == []
    assert len(result.rows) == 1


def test_a_stray_sentence_around_the_array_is_tolerated():
    text = "Sure, here you go:\n" + json.dumps([_obj()]) + "\nLet me know if you need more."
    result = add_models.parse_answer(text)
    assert result.issues == []
    assert len(result.rows) == 1


def test_blank_answer_parses_to_nothing_with_no_issues():
    result = add_models.parse_answer("   \n  ")
    assert result.rows == []
    assert result.issues == []


# ── never silently dropped: every bad entry becomes an issue ───────────

def test_not_valid_json_is_reported():
    result = add_models.parse_answer("not json at all")
    assert result.rows == []
    assert len(result.issues) == 1
    assert "json" in result.issues[0].reason.lower()


def test_a_non_array_top_level_is_reported():
    result = add_models.parse_answer(json.dumps(_obj()))
    assert result.rows == []
    assert "array" in result.issues[0].reason.lower()


def test_a_non_object_entry_is_reported():
    result = add_models.parse_answer(json.dumps(["just a string"]))
    assert result.rows == []
    assert "object" in result.issues[0].reason.lower()


def test_missing_key_is_reported():
    obj = _obj()
    del obj["rpm"]
    result = add_models.parse_answer(json.dumps([obj]))
    assert result.rows == []
    assert "rpm" in result.issues[0].reason


def test_empty_provider_or_model_is_reported():
    result = add_models.parse_answer(json.dumps([_obj(provider="")]))
    assert result.rows == []
    assert len(result.issues) == 1


def test_bad_kind_is_reported():
    result = add_models.parse_answer(json.dumps([_obj(kind="translation")]))
    assert result.rows == []
    assert "kind" in result.issues[0].reason


def test_non_numeric_context_is_reported():
    result = add_models.parse_answer(json.dumps([_obj(context="lots")]))
    assert result.rows == []
    assert "context" in result.issues[0].reason


def test_non_numeric_rpm_is_reported():
    result = add_models.parse_answer(json.dumps([_obj(rpm="fast")]))
    assert result.rows == []
    assert "rpm" in result.issues[0].reason


def test_non_numeric_tpm_is_reported():
    result = add_models.parse_answer(json.dumps([_obj(tpm="lots")]))
    assert result.rows == []
    assert "tpm" in result.issues[0].reason


def test_non_numeric_tps_is_reported():
    result = add_models.parse_answer(json.dumps([_obj(tps="lots")]))
    assert result.rows == []
    assert "tps" in result.issues[0].reason


def test_non_numeric_score_is_reported():
    result = add_models.parse_answer(json.dumps([_obj(score="great")]))
    assert result.rows == []
    assert "score" in result.issues[0].reason


def test_out_of_range_score_is_reported():
    result = add_models.parse_answer(json.dumps([_obj(score=150)]))
    assert result.rows == []
    assert "range" in result.issues[0].reason


def test_bad_vision_value_is_reported():
    result = add_models.parse_answer(json.dumps([_obj(vision="maybe")]))
    assert result.rows == []
    assert "vision" in result.issues[0].reason


def test_bad_free_value_is_reported():
    result = add_models.parse_answer(json.dumps([_obj(free="sometimes")]))
    assert result.rows == []
    assert "free" in result.issues[0].reason


def test_duplicate_provider_model_is_reported_not_silently_merged():
    result = add_models.parse_answer(json.dumps([
        _obj(score=50),
        _obj(score=90, vision=True, free=True),
    ]))
    assert len(result.rows) == 1
    assert result.rows[0].score == 50  # the first entry wins
    assert len(result.issues) == 1
    assert "duplicate" in result.issues[0].reason


def test_a_bad_entry_does_not_stop_the_good_ones_from_parsing():
    bad = _obj(model="bad")
    del bad["rpm"]
    result = add_models.parse_answer(json.dumps([
        _obj(model="good", score=50),
        bad,
        _obj(model="also-good", score=60),
    ]))
    assert [r.model for r in result.rows] == ["good", "also-good"]
    assert len(result.issues) == 1
