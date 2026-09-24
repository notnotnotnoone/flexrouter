import os, yaml, pytest
from pathlib import Path
from flexrouter.config import FlexConfig, load_config, RETRY_PRESETS

def test_load_minimal_config(config_file):
    cfg = load_config(config_file)
    assert "low" in cfg.tiers
    assert cfg.tiers["low"][0].model == "llama-3.1-8b-instant"
    assert cfg.tiers["low"][0].score == 85

def test_provider_api_key_resolved_from_env(config_file):
    cfg = load_config(config_file)
    keys = cfg.providers["groq"].api_keys
    assert keys[0] == "test-key"

def test_missing_env_var_loads_with_no_keys(tmp_path, monkeypatch):
    # Credential resolution (config.resolve_keys, task 5) no longer raises when
    # a declared env var isn't set — it silently falls through to no
    # credentials, same as a provider with nothing configured at all.
    monkeypatch.setenv("FLEXROUTER_HOME", str(tmp_path / "home"))
    raw = {
        "tiers": {"low": [{"provider": "x", "model": "m", "score": 50, "rpm": 10, "tpm": 1000, "context_window": 4096}]},
        "providers": {"x": {"base_url": "http://x", "api_keys": [{"env": "MISSING_KEY_XYZ"}]}},
        "settings": {"state_dir": str(tmp_path)},
    }
    p = tmp_path / "flexrouter.yaml"
    p.write_text(yaml.dump(raw))
    cfg = load_config(p)
    assert cfg.providers["x"].api_keys == []

def test_retry_preset_balanced(config_file):
    cfg = load_config(config_file)
    assert cfg.retry.retries == RETRY_PRESETS["balanced"]["retries"]
    assert cfg.retry.backoff_seconds == RETRY_PRESETS["balanced"]["backoff_seconds"]

def test_manual_retry_override(tmp_path):
    os.environ["GROQ_API_KEY"] = "k"
    p = tmp_path / "flexrouter.yaml"
    p.write_text(yaml.dump({
        "tiers": {"low": [{"provider": "groq", "model": "m", "score": 50, "rpm": 10, "tpm": 1000, "context_window": 4096}]},
        "providers": {"groq": {"base_url": "http://groq", "api_keys": [{"env": "GROQ_API_KEY"}]}},
        "settings": {"state_dir": str(tmp_path), "retries": 7, "backoff_seconds": 3.5},
    }))
    cfg = load_config(p)
    assert cfg.retry.retries == 7
    assert cfg.retry.backoff_seconds == 3.5

def test_config_defaults(config_file):
    cfg = load_config(config_file)
    assert cfg.window_seconds == 60
    assert cfg.session_ttl_minutes == 30
    assert cfg.dashboard_port == 7352

def test_vision_flag_parsed(tmp_path):
    os.environ["GROQ_API_KEY"] = "k"
    p = tmp_path / "flexrouter.yaml"
    p.write_text(yaml.dump({
        "tiers": {"high": [{"provider": "groq", "model": "vision-model", "score": 90, "rpm": 10, "tpm": 1000, "context_window": 4096, "vision": True}]},
        "providers": {"groq": {"base_url": "http://groq", "api_keys": [{"env": "GROQ_API_KEY"}]}},
        "settings": {"state_dir": str(tmp_path)},
    }))
    cfg = load_config(p)
    assert cfg.tiers["high"][0].vision is True

def test_provider_key_strategy_defaults_to_most_headroom(config_file):
    cfg = load_config(config_file)
    assert cfg.providers["groq"].key_strategy == "most_headroom"


def test_provider_key_strategy_is_read_from_settings(tmp_path, monkeypatch, minimal_config):
    cfg = minimal_config
    cfg["settings"]["state_dir"] = str(tmp_path / ".flexrouter")
    cfg["providers"]["groq"]["key_strategy"] = "round_robin"
    p = tmp_path / "flexrouter.yaml"
    p.write_text(yaml.dump(cfg))
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    loaded = load_config(p)
    assert loaded.providers["groq"].key_strategy == "round_robin"


def test_tokens_per_second_parsed(tmp_path):
    os.environ["GROQ_API_KEY"] = "k"
    p = tmp_path / "flexrouter.yaml"
    p.write_text(yaml.dump({
        "tiers": {"low": [{"provider": "groq", "model": "m", "score": 50, "rpm": 10,
                           "tpm": 1000, "context_window": 4096, "tokens_per_second": 142.5}]},
        "providers": {"groq": {"base_url": "http://groq", "api_keys": [{"env": "GROQ_API_KEY"}]}},
        "settings": {"state_dir": str(tmp_path)},
    }))
    cfg = load_config(p)
    assert cfg.tiers["low"][0].tokens_per_second == 142.5


def test_tokens_per_second_defaults_to_none(config_file):
    cfg = load_config(config_file)
    assert cfg.tiers["low"][0].tokens_per_second is None


def test_negative_tokens_per_second_becomes_none(tmp_path):
    os.environ["GROQ_API_KEY"] = "k"
    p = tmp_path / "flexrouter.yaml"
    p.write_text(yaml.dump({
        "tiers": {"low": [{"provider": "groq", "model": "m", "score": 50, "rpm": 10,
                           "tpm": 1000, "context_window": 4096, "tokens_per_second": -5}]},
        "providers": {"groq": {"base_url": "http://groq", "api_keys": [{"env": "GROQ_API_KEY"}]}},
        "settings": {"state_dir": str(tmp_path)},
    }))
    cfg = load_config(p)
    assert cfg.tiers["low"][0].tokens_per_second is None


def test_bucket_strategy_parsed(tmp_path):
    os.environ["GROQ_API_KEY"] = "k"
    p = tmp_path / "flexrouter.yaml"
    p.write_text(yaml.dump({
        "tiers": {"low": [{"provider": "groq", "model": "m", "score": 50, "rpm": 10,
                           "tpm": 1000, "context_window": 4096}]},
        "providers": {"groq": {"base_url": "http://groq", "api_keys": [{"env": "GROQ_API_KEY"}]}},
        "settings": {"state_dir": str(tmp_path)},
        "bucket_strategy": {"low": "fastest"},
    }))
    cfg = load_config(p)
    assert cfg.bucket_strategy == {"low": "fastest"}


def test_bucket_strategy_defaults_to_empty(config_file):
    cfg = load_config(config_file)
    assert cfg.bucket_strategy == {}


def test_key_concurrency_cap_defaults_to_four(config_file):
    cfg = load_config(config_file)
    assert cfg.key_concurrency_cap == 4


def test_key_concurrency_cap_is_read_from_settings(tmp_path, monkeypatch, minimal_config):
    cfg = minimal_config
    cfg["settings"]["state_dir"] = str(tmp_path / ".flexrouter")
    cfg["settings"]["key_concurrency_cap"] = 8
    p = tmp_path / "flexrouter.yaml"
    p.write_text(yaml.dump(cfg))
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    loaded = load_config(p)
    assert loaded.key_concurrency_cap == 8


def test_a_model_matching_a_non_chat_pattern_is_left_out_of_its_bucket(
        tmp_path, monkeypatch, minimal_config):
    # Live-verified: a video/image-generation or embedding model routed a
    # real chat completion fails every single time with "does not support
    # chat endpoints" - not flaky, guaranteed. Rather than let the engine
    # pick it and burn a request on a sure failure, it's dropped at load
    # time and a warning names it instead.
    cfg = minimal_config
    cfg["settings"]["state_dir"] = str(tmp_path / ".flexrouter")
    bucket = next(iter(cfg["tiers"].values()))
    non_chat = dict(bucket[0])
    non_chat["model"] = "seedance-2.0"
    bucket.append(non_chat)
    p = tmp_path / "flexrouter.yaml"
    p.write_text(yaml.dump(cfg))
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    with pytest.warns(UserWarning, match="non-chat name pattern"):
        loaded = load_config(p)
    models = [m.model for models in loaded.tiers.values() for m in models]
    assert "seedance-2.0" not in models


def test_a_chat_shaped_model_whose_name_merely_contains_guard_is_kept(
        tmp_path, monkeypatch, minimal_config):
    # Regression: the first version of this filter used the full
    # (warn-only) NON_CHAT_PATTERNS list to also block routing, and
    # "guard" matching "gpt-oss-safeguard-20b" - a real OpenAI chat-
    # completions safety classifier - silently dropped a working model.
    cfg = minimal_config
    cfg["settings"]["state_dir"] = str(tmp_path / ".flexrouter")
    bucket = next(iter(cfg["tiers"].values()))
    guard_model = dict(bucket[0])
    guard_model["model"] = "gpt-oss-safeguard-20b"
    bucket.append(guard_model)
    p = tmp_path / "flexrouter.yaml"
    p.write_text(yaml.dump(cfg))
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    loaded = load_config(p)
    models = [m.model for models in loaded.tiers.values() for m in models]
    assert "gpt-oss-safeguard-20b" in models
