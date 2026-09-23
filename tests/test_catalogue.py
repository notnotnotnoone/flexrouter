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
    assert len(PROVIDERS) == 11


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
    assert names == {"cerebras", "groq", "openrouter", "googleai", "ollama", "mistral", "nvidia", "llm7"}


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


def _provider(name):
    return next(p for p in PROVIDERS if p.name == name)


@pytest.mark.parametrize("model_id,wanted", [
    # Free chat models, per ai.google.dev/gemini-api/docs/pricing (2026-09).
    ("models/gemini-3.8-flash", True),
    ("models/gemini-3.5-flash-lite", True),
    ("models/gemini-3-flash-preview", True),
    ("models/gemini-2.5-pro", True),
    ("models/gemma-4-27b-it", True),
    # Paid-only, or not a chat model at all.
    ("models/gemini-3.1-pro-preview", False),
    ("models/gemini-2.5-flash-image", False),
    ("models/gemini-3.8-flash-tts", False),
    ("models/gemini-3.8-live", False),
    ("models/gemini-3.5-transcribe", False),
    ("models/gemini-2.5-flash-native-audio-latest", False),
    ("models/gemini-2.5-computer-use-preview-10-2025", False),
    ("models/antigravity-preview-05-2026", False),
    ("models/deep-research-preview-04-2026", False),
    ("models/gemini-embedding-2", False),
    ("models/aqa", False),
])
def test_googleai_filter_keeps_only_free_chat_models(model_id, wanted):
    assert _provider("googleai").free_filter({"id": model_id}) is wanted


def test_llm7_filter_keeps_only_free_chat_models():
    # docs.llm7.io/guides/models-api: "turbo" is the free tier, "pro" needs
    # a balance; model_type says chat vs image/video.
    keep = _provider("llm7").free_filter
    assert keep({"id": "a", "model_type": "chat", "tier": "turbo", "usage_based_only": False})
    assert not keep({"id": "b", "model_type": "chat", "tier": "pro", "usage_based_only": True})
    assert not keep({"id": "c", "model_type": "chat", "tier": "turbo", "usage_based_only": True})
    assert not keep({"id": "d", "model_type": "video", "tier": "turbo", "usage_based_only": False})


def test_mistral_filter_uses_the_completion_chat_capability():
    keep = _provider("mistral").free_filter
    assert keep({"id": "ministral-8b-latest", "capabilities": {"completion_chat": True}})
    assert not keep({"id": "mistral-embed", "capabilities": {"completion_chat": False}})
    assert not keep({"id": "mistral-ocr-latest", "capabilities": {"completion_chat": False}})
    assert not keep({"id": "old", "capabilities": {"completion_chat": True}, "archived": True})


def test_groq_filter_drops_inactive_and_non_text_models():
    keep = _provider("groq").free_filter
    assert keep({"id": "llama", "active": True, "output_modalities": ["text"]})
    assert keep({"id": "no-fields-at-all"})
    assert not keep({"id": "gone", "active": False, "output_modalities": ["text"]})
    assert not keep({"id": "tts", "active": True, "output_modalities": ["audio"]})


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
async def test_discover_models_raises_on_a_failed_lookup():
    # An empty list would read as "this provider lists nothing", and refresh
    # would then report every configured model as vanished.
    from flexrouter.catalogue import DiscoveryError, discover_models
    groq = next(p for p in PROVIDERS if p.name == "groq")
    respx.get("https://api.groq.com/openai/v1/models").mock(
        return_value=httpx.Response(401, json={"error": {"message": "Invalid API Key"}})
    )
    with pytest.raises(DiscoveryError, match="401"):
        await discover_models(groq, api_key="bad-key")


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


def test_context_window_reads_tokens_out_of_a_nested_shape():
    """llm7's /v1/models answers context_length as {"tokens": N, "chars":
    null} rather than a bare number - every one of its ~44 models has this
    shape. Before the fix, int() on that dict blew up the whole discovery
    run for any provider mixed in with llm7 in the same refresh."""
    from flexrouter.catalogue import _context_window
    assert _context_window({"context_length": {"tokens": 400000, "chars": None}}) == 400000


def test_context_window_falls_back_to_default_when_nested_tokens_is_absent():
    from flexrouter.catalogue import _context_window
    assert _context_window({"context_length": {"tokens": None, "chars": None}}) == 131_072


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


def _aa(name, slug, score):
    return {"name": name, "slug": slug,
            "evaluations": {"artificial_analysis_intelligence_index": score}}


# Real AA names and slugs, 2026-09. The old first-substring matcher gave
# every one of the wrong answers below.
LIVE_AA = [
    _aa("HyperNova 60B 2605 (high, based on gpt-oss-120b)", "hypernova-60b", 30),
    _aa("gpt-oss-120b (high)", "gpt-oss-120b", 33),
    _aa("gpt-oss-120b (low)", "gpt-oss-120b-low", 25),
    _aa("gpt-oss-20b (low)", "gpt-oss-20b-low", 20),
    _aa("gpt-oss-20b (high)", "gpt-oss-20b", 24),
    _aa("Claude Opus 5.5 (Adaptive Reasoning, Max Effort)", "claude-opus-5-5", 60),
    _aa("Inkling Small", "inkling-small", 12),
    _aa("Mistral Medium 3.5", "mistral-medium-3-5", 28),
    _aa("Ministral 3 8B", "ministral-3-8b", 14),
    _aa("Ministral 8B", "ministral-8b", 11),
    _aa("DeepSeek V4 Flash 0731 (Reasoning, Max Effort)", "deepseek-v4-flash", 40),
    _aa("Gemma 4 31B", "gemma-4-31b", 22),
    _aa("GPT-4o (Nov '24)", "gpt-4o", 29),
]


def _lookup():
    from flexrouter.catalogue import _aa_lookup_from
    return _aa_lookup_from({"data": LIVE_AA})


@pytest.mark.parametrize("model_id,expected", [
    ("openai/gpt-oss-120b", 25),            # median_low of its own variants, not HyperNova
    ("openai/gpt-oss-20b", 20),
    ("claude-opus-5", -1),                  # a different model from Opus 5.5
    ("Inkling", -1),                        # not Inkling Small
    ("mistral-medium-3", -1),               # not Medium 3.5
    ("ministral-8b-latest", 11),            # "-latest" is noise
    ("ministral-8b-2512", 11),              # so is a date stamp
    ("DeepSeek-V4-Flash-0731", 40),
    ("deepseek-v4-flash:0731", 40),
    ("gemma4:31b", 22),
    ("openai/gpt-4", -1),                   # never a prefix of gpt-4o
])
def test_aa_matching_needs_the_same_model_not_a_substring(model_id, expected):
    from flexrouter.catalogue import _best_score
    assert _best_score(model_id, _lookup(), fallback=-1) == expected
