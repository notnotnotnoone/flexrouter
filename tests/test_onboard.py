from flexrouter.onboard import PROVIDERS, ProviderDef


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
