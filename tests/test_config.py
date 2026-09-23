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
