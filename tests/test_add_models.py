"""flexrouter/dashboard/add_models.py - the prompt builder and the pasted-
answer parser, tested hard since it is the one place free-form AI output
turns into something this app will act on.
"""
from types import SimpleNamespace

from flexrouter.dashboard import add_models


def _provider(keys):
    return SimpleNamespace(keys=keys)


def _router(providers):
    return SimpleNamespace(_cfg=SimpleNamespace(providers=providers))


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
    assert "provider | model | kind | context | rpm | tpm | vision | free | score" in prompt


def test_build_prompt_with_no_existing_models_says_so():
    prompt = add_models.build_prompt("groq", [], "")
    assert "Nothing is configured for groq yet." in prompt


def test_build_prompt_includes_pasted_notes():
    prompt = add_models.build_prompt("groq", [], "Groq's docs say X")
    assert "Groq's docs say X" in prompt


def test_build_prompt_tells_the_ai_to_write_none_rather_than_guess():
    prompt = add_models.build_prompt("groq", [], "")
    assert "none" in prompt.lower()
    assert "guess" in prompt.lower()


# ── parse_answer: the happy path ───────────────────────────────────────

def test_parses_a_well_formed_line():
    result = add_models.parse_answer(
        "groq | llama-3.1-8b-instant | chat | 131072 | 30 | 6000 | no | yes | 85")
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


def test_parses_none_fields():
    result = add_models.parse_answer(
        "groq | some-model | embedding | none | none | none | no | no | none")
    assert result.issues == []
    row = result.rows[0]
    assert row.context is None
    assert row.rpm is None
    assert row.tpm is None
    assert row.score is None


def test_none_is_case_insensitive():
    result = add_models.parse_answer(
        "groq | m | chat | NONE | None | nOnE | yes | no | none")
    assert result.issues == []
    row = result.rows[0]
    assert row.context is None and row.rpm is None and row.tpm is None and row.score is None


def test_kind_is_case_insensitive_and_normalized():
    result = add_models.parse_answer("groq | m | CHAT | none | none | none | no | no | none")
    assert result.issues == []
    assert result.rows[0].kind == "chat"


def test_extra_spaces_are_tolerated():
    result = add_models.parse_answer(
        "  groq   |   m   |  chat  | 1000 |  10 |  1000 |  yes |  no  |  50  ")
    assert result.issues == []
    row = result.rows[0]
    assert row.provider == "groq" and row.model == "m" and row.context == 1000


def test_multiple_lines_all_parse():
    text = "\n".join([
        "groq | a | chat | 1000 | 10 | 1000 | no | no | 50",
        "groq | b | chat | 2000 | 20 | 2000 | yes | yes | 60",
    ])
    result = add_models.parse_answer(text)
    assert result.issues == []
    assert [r.model for r in result.rows] == ["a", "b"]


# ── header lines, markdown noise ───────────────────────────────────────

def test_header_line_is_skipped_not_reported_as_an_error():
    text = "\n".join([
        "provider | model | kind | context | rpm | tpm | vision | free | score",
        "groq | m | chat | none | none | none | no | no | none",
    ])
    result = add_models.parse_answer(text)
    assert result.issues == []
    assert len(result.rows) == 1


def test_markdown_table_pipes_and_separator_row_are_tolerated():
    text = "\n".join([
        "| provider | model | kind | context | rpm | tpm | vision | free | score |",
        "|---|---|---|---|---|---|---|---|---|",
        "| groq | m | chat | none | none | none | no | no | none |",
    ])
    result = add_models.parse_answer(text)
    assert result.issues == []
    assert len(result.rows) == 1
    assert result.rows[0].provider == "groq"
    assert result.rows[0].model == "m"


def test_backticked_model_ids_are_tolerated():
    text = "groq | `llama-3.1-8b-instant` | chat | none | none | none | no | no | none"
    result = add_models.parse_answer(text)
    assert result.issues == []
    assert result.rows[0].model == "llama-3.1-8b-instant"


def test_code_fences_are_skipped():
    text = "\n".join([
        "```",
        "groq | m | chat | none | none | none | no | no | none",
        "```",
    ])
    result = add_models.parse_answer(text)
    assert result.issues == []
    assert len(result.rows) == 1


def test_blank_lines_are_ignored():
    text = "\n\ngroq | m | chat | none | none | none | no | no | none\n\n"
    result = add_models.parse_answer(text)
    assert result.issues == []
    assert len(result.rows) == 1


# ── never silently dropped: every bad line becomes an issue ────────────

def test_wrong_column_count_is_reported():
    result = add_models.parse_answer("groq | m | chat | none | none")
    assert result.rows == []
    assert len(result.issues) == 1
    assert "5" in result.issues[0].reason


def test_empty_provider_or_model_is_reported():
    result = add_models.parse_answer(" | m | chat | none | none | none | no | no | none")
    assert result.rows == []
    assert len(result.issues) == 1


def test_bad_kind_is_reported():
    result = add_models.parse_answer(
        "groq | m | translation | none | none | none | no | no | none")
    assert result.rows == []
    assert "kind" in result.issues[0].reason


def test_non_numeric_context_is_reported():
    result = add_models.parse_answer(
        "groq | m | chat | lots | none | none | no | no | none")
    assert result.rows == []
    assert "context" in result.issues[0].reason


def test_non_numeric_rpm_is_reported():
    result = add_models.parse_answer(
        "groq | m | chat | none | fast | none | no | no | none")
    assert result.rows == []
    assert "rpm" in result.issues[0].reason


def test_non_numeric_tpm_is_reported():
    result = add_models.parse_answer(
        "groq | m | chat | none | none | lots | no | no | none")
    assert result.rows == []
    assert "tpm" in result.issues[0].reason


def test_non_numeric_score_is_reported():
    result = add_models.parse_answer(
        "groq | m | chat | none | none | none | no | no | great")
    assert result.rows == []
    assert "score" in result.issues[0].reason


def test_out_of_range_score_is_reported():
    result = add_models.parse_answer(
        "groq | m | chat | none | none | none | no | no | 150")
    assert result.rows == []
    assert "range" in result.issues[0].reason


def test_bad_vision_value_is_reported():
    result = add_models.parse_answer(
        "groq | m | chat | none | none | none | maybe | no | none")
    assert result.rows == []
    assert "vision" in result.issues[0].reason


def test_bad_free_value_is_reported():
    result = add_models.parse_answer(
        "groq | m | chat | none | none | none | no | sometimes | none")
    assert result.rows == []
    assert "free" in result.issues[0].reason


def test_duplicate_provider_model_is_reported_not_silently_merged():
    text = "\n".join([
        "groq | m | chat | none | none | none | no | no | 50",
        "groq | m | chat | none | none | none | yes | yes | 90",
    ])
    result = add_models.parse_answer(text)
    assert len(result.rows) == 1
    assert result.rows[0].score == 50  # the first line wins
    assert len(result.issues) == 1
    assert "duplicate" in result.issues[0].reason


def test_a_bad_line_does_not_stop_the_good_ones_from_parsing():
    text = "\n".join([
        "groq | good | chat | none | none | none | no | no | 50",
        "groq | bad | chat | none",
        "groq | also-good | chat | none | none | none | no | no | 60",
    ])
    result = add_models.parse_answer(text)
    assert [r.model for r in result.rows] == ["good", "also-good"]
    assert len(result.issues) == 1
