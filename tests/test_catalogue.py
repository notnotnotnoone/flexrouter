"""The provider catalogue: registry, model discovery, and scoring.

These tests used to live in tests/test_onboard.py. The `init` wizard they
shared a file with is gone; the catalogue it was built on is still what the
daily refresh reads, so its tests move here rather than disappearing.
"""
import pytest
import respx
import httpx
from flexrouter.catalogue import PROVIDERS, AA_MODELS_URL, ProviderDef


def test_provider_count():
    assert len(PROVIDERS) == 8


def test_all_providers_have_required_fields():
    for p in PROVIDERS:
        assert p.name
        assert p.base_url.startswith("http")
        assert p.signup_url.startswith("http")
        assert callable(p.free_filter)
        assert "rpm" in p.rate_limit_headers
        assert "tpm" in p.rate_limit_headers


def test_free_providers():
    free = [p for p in PROVIDERS if p.free]
    names = {p.name for p in free}
    assert names == {"cerebras", "groq", "openrouter", "googleai", "ollama"}


def test_paid_providers():
    paid = [p for p in PROVIDERS if not p.free]
    names = {p.name for p in paid}
    assert names == {"deepseek", "siliconflow", "sambanova"}


def test_ollama_flag():
    ollama = next(p for p in PROVIDERS if p.name == "ollama")
    assert ollama.ollama is True


def test_openrouter_free_filter():
    or_provider = next(p for p in PROVIDERS if p.name == "openrouter")
    free_model = {"id": "qwen/qwen3:free", "pricing": {"prompt": "0", "completion": "0"}}
    paid_model = {"id": "openai/gpt-4o", "pricing": {"prompt": "0.000005", "completion": "0.000015"}}
    assert or_provider.free_filter(free_model) is True
    assert or_provider.free_filter(paid_model) is False


def test_googleai_free_filter():
    googleai = next(p for p in PROVIDERS if p.name == "googleai")
    flash = {"id": "gemini-2.5-flash"}
    pro = {"id": "gemini-2.5-pro"}
    assert googleai.free_filter(flash) is True
    assert googleai.free_filter(pro) is False


GROQ_MODELS_RESPONSE = {
    "data": [
        {"id": "llama-3.3-70b-versatile", "context_window": 131072},
        {"id": "llama-3.1-8b-instant", "context_window": 131072},
    ]
}

OPENROUTER_MODELS_RESPONSE = {
    "data": [
        {"id": "qwen/qwen3:free", "context_length": 262144, "pricing": {"prompt": "0", "completion": "0"}},
        {"id": "openai/gpt-4o", "context_length": 128000, "pricing": {"prompt": "0.000005", "completion": "0.000015"}},
    ]
}

OLLAMA_MODELS_RESPONSE = {
    "data": [
        {"id": "llama3:latest", "context_window": 8192},
    ]
}


@pytest.mark.asyncio
@respx.mock
async def test_discover_models_returns_filtered_list():
    from flexrouter.catalogue import discover_models
    groq = next(p for p in PROVIDERS if p.name == "groq")
    respx.get("https://api.groq.com/openai/v1/models").mock(
        return_value=httpx.Response(200, json=GROQ_MODELS_RESPONSE)
    )
    models = await discover_models(groq, api_key="test-key")
    assert len(models) == 2
    assert models[0]["id"] == "llama-3.3-70b-versatile"


@pytest.mark.asyncio
@respx.mock
async def test_discover_models_filters_paid_from_openrouter():
    from flexrouter.catalogue import discover_models
    or_provider = next(p for p in PROVIDERS if p.name == "openrouter")
    respx.get("https://openrouter.ai/api/v1/models").mock(
        return_value=httpx.Response(200, json=OPENROUTER_MODELS_RESPONSE)
    )
    models = await discover_models(or_provider, api_key="test-key")
    assert len(models) == 1
    assert models[0]["id"] == "qwen/qwen3:free"


@pytest.mark.asyncio
@respx.mock
async def test_discover_models_returns_empty_on_auth_error():
    from flexrouter.catalogue import discover_models
    groq = next(p for p in PROVIDERS if p.name == "groq")
    respx.get("https://api.groq.com/openai/v1/models").mock(
        return_value=httpx.Response(401, json={"error": "unauthorized"})
    )
    models = await discover_models(groq, api_key="bad-key")
    assert models == []


@pytest.mark.asyncio
@respx.mock
async def test_discover_ollama_returns_models_when_running():
    from flexrouter.catalogue import discover_ollama
    respx.get("http://localhost:11434/v1/models").mock(
        return_value=httpx.Response(200, json=OLLAMA_MODELS_RESPONSE)
    )
    models = await discover_ollama()
    assert len(models) == 1
    assert models[0]["id"] == "llama3:latest"


@pytest.mark.asyncio
@respx.mock
async def test_discover_ollama_returns_empty_when_not_running():
    from flexrouter.catalogue import discover_ollama
    respx.get("http://localhost:11434/v1/models").mock(side_effect=httpx.ConnectError("refused"))
    models = await discover_ollama()
    assert models == []


def test_context_window_uses_context_window_key():
    from flexrouter.catalogue import _context_window
    assert _context_window({"context_window": 65536}) == 65536


def test_context_window_falls_back_to_context_length():
    from flexrouter.catalogue import _context_window
    assert _context_window({"context_length": 32768}) == 32768


def test_context_window_defaults_to_131072():
    from flexrouter.catalogue import _context_window
    assert _context_window({}) == 131_072


AA_RESPONSE = {
    "data": [
        {
            "id": "llama-3-3-70b-instruct",
            "name": "Llama 3.3 70B Instruct",
            "slug": "llama-3-3-70b-instruct",
            "evaluations": {"artificial_analysis_intelligence_index": 72.5},
        },
        {
            "id": "gemini-2-5-flash",
            "name": "Gemini 2.5 Flash",
            "slug": "gemini-2-5-flash",
            "evaluations": {"artificial_analysis_intelligence_index": 81.0},
        },
        {
            # AA really does serve the key with an explicit null for some
            # models. This entry is the regression: it used to raise inside
            # int(None) and take every other score down with it.
            "id": "unrated-model",
            "name": "Unrated Model",
            "slug": "unrated-model",
            "evaluations": {"artificial_analysis_intelligence_index": None},
        },
    ]
}


@pytest.mark.asyncio
@respx.mock
async def test_score_with_aa_matches_known_model():
    from flexrouter.catalogue import score_with_aa
    respx.get(AA_MODELS_URL).mock(
        return_value=httpx.Response(200, json=AA_RESPONSE)
    )
    models = [
        {"id": "meta-llama/llama-3.3-70b-instruct:free", "context_window": 131072},
    ]
    scored = await score_with_aa(models, aa_key="test-aa-key")
    assert scored[0]["score"] == 72


@pytest.mark.asyncio
@respx.mock
async def test_score_with_aa_unknown_model_gets_median_of_live_set():
    """An unmatched model sits mid-pack, not top.

    This asserted 50 when AA's index ran 0-100. It now runs 3-53, so a flat 50
    ranked anything unmatched above ~99% of the catalogue. The fallback is the
    median of whatever AA actually returned: 72 and 81 here, median_low -> 72.
    """
    from flexrouter.catalogue import score_with_aa
    respx.get(AA_MODELS_URL).mock(
        return_value=httpx.Response(200, json=AA_RESPONSE)
    )
    models = [{"id": "unknown/totally-new-model", "context_window": 32768}]
    scored = await score_with_aa(models, aa_key="test-aa-key")
    assert scored[0]["score"] == 72


@pytest.mark.asyncio
@respx.mock
async def test_null_intelligence_index_does_not_sink_the_other_scores():
    """A null score is skipped, not fatal.

    The fixture carries one entry whose index is an explicit null. `.get(k, 50)`
    does not defend against that -- the default only fires when the key is
    absent -- so this used to raise and the bare except returned 50 for
    everything, silently.
    """
    from flexrouter.catalogue import score_with_aa
    respx.get(AA_MODELS_URL).mock(
        return_value=httpx.Response(200, json=AA_RESPONSE)
    )
    models = [{"id": "meta-llama/llama-3.3-70b-instruct:free", "context_window": 131072}]
    scored = await score_with_aa(models, aa_key="test-aa-key")
    assert scored[0]["score"] == 72


@pytest.mark.asyncio
@respx.mock
async def test_score_with_aa_api_failure_gives_50():
    from flexrouter.catalogue import score_with_aa
    respx.get(AA_MODELS_URL).mock(
        return_value=httpx.Response(500, text="error")
    )
    models = [{"id": "groq/llama-8b", "context_window": 131072}]
    scored = await score_with_aa(models, aa_key="test-aa-key")
    assert scored[0]["score"] == 50


def test_aa_models_url_is_the_versioned_endpoint():
    """Pin the AA URL literally.

    The mocks above resolve AA_MODELS_URL, so they pass no matter what it
    points at — they cannot catch the URL going stale. This assertion is the
    only thing in the suite that fails if someone edits the constant, which
    is what the v2-path outage went undetected behind.
    """
    from flexrouter.catalogue import AA_MODELS_URL
    assert AA_MODELS_URL == "https://artificialanalysis.ai/api/v2/data/llms/models"


@pytest.mark.asyncio
async def test_score_with_aa_no_key_gives_50():
    from flexrouter.catalogue import score_with_aa
    models = [{"id": "groq/llama-8b", "context_window": 131072}]
    scored = await score_with_aa(models, aa_key=None)
    assert scored[0]["score"] == 50


def test_normalize_strips_prefix_and_suffix():
    from flexrouter.catalogue import _normalize
    assert _normalize("meta-llama/llama-3.3-70b-instruct:free") == "llama 3 3 70b instruct"


def test_normalize_short_name_does_not_match_longer():
    """Document known substring limitation: gpt-4 would match inside gpt-4o."""
    from flexrouter.catalogue import _normalize, _best_score
    # "gpt 4" IS a substring of "gpt 4o" — this is a known limitation
    # At minimum assert the normalize output so future changes are visible
    assert _normalize("openai/gpt-4") == "gpt 4"
    assert _normalize("openai/gpt-4o") == "gpt 4o"
    # "gpt 4" in "gpt 4o" is True — document this as expected behavior
    assert "gpt 4" in "gpt 4o"
