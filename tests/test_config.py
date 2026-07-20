import os, yaml, pytest
from pathlib import Path
from flexrouter.config import FlexConfig, load_config, RETRY_PRESETS, discover_config

def test_load_minimal_config(config_file):
    cfg = load_config(config_file)
    assert "low" in cfg.tiers
    assert cfg.tiers["low"][0].model == "llama-3.1-8b-instant"
    assert cfg.tiers["low"][0].score == 85

def test_provider_api_key_resolved_from_env(config_file):
    cfg = load_config(config_file)
    keys = cfg.providers["groq"].api_keys
    assert keys[0] == "test-key"

def test_missing_env_var_raises(tmp_path):
    raw = {
        "tiers": {"low": [{"provider": "x", "model": "m", "score": 50, "rpm": 10, "tpm": 1000, "context_window": 4096}]},
        "providers": {"x": {"base_url": "http://x", "api_keys": [{"env": "MISSING_KEY_XYZ"}]}},
        "settings": {"state_dir": str(tmp_path)},
    }
    p = tmp_path / "flexrouter.yaml"
    p.write_text(yaml.dump(raw))
    from flexrouter.exceptions import ConfigError
    with pytest.raises(ConfigError, match="MISSING_KEY_XYZ"):
        load_config(p)

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

def test_discover_config_finds_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    os.environ["GROQ_API_KEY"] = "k"
    p = tmp_path / "flexrouter.yaml"
    p.write_text(yaml.dump({
        "tiers": {"low": [{"provider": "groq", "model": "m", "score": 50, "rpm": 10, "tpm": 1000, "context_window": 4096}]},
        "providers": {"groq": {"base_url": "http://groq", "api_keys": [{"env": "GROQ_API_KEY"}]}},
        "settings": {"state_dir": str(tmp_path)},
    }))
    found = discover_config()
    assert found is not None
    assert found.name == "flexrouter.yaml"


def test_sampler_settings_defaults(tmp_path):
    from flexrouter.config import load_config
    p = tmp_path / "f.yaml"
    p.write_text(
        "providers:\n  groq:\n    base_url: http://x\n    api_keys: [k]\n"
        "tiers:\n  default:\n    - {provider: groq, model: m, score: 50, rpm: 1, tpm: 1}\n"
    )
    cfg = load_config(p)
    assert cfg.sample_interval_seconds == 60
    assert cfg.health_history_days == 30


def test_sampler_settings_override(tmp_path):
    from flexrouter.config import load_config
    p = tmp_path / "f.yaml"
    p.write_text(
        "providers:\n  groq:\n    base_url: http://x\n    api_keys: [k]\n"
        "tiers:\n  default:\n    - {provider: groq, model: m, score: 50, rpm: 1, tpm: 1}\n"
        "settings:\n  sample_interval_seconds: 15\n  health_history_days: 7\n"
    )
    cfg = load_config(p)
    assert cfg.sample_interval_seconds == 15
    assert cfg.health_history_days == 7


def test_provider_header_parser_defaults_to_openai_compatible(tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    cfg_dict = {
        "tiers": {"low": [{"provider": "groq", "model": "m", "score": 50, "rpm": 1, "tpm": 1}]},
        "providers": {"groq": {"base_url": "https://x", "api_keys": [{"env": "GROQ_API_KEY"}]}},
    }
    p = tmp_path / "flexrouter.yaml"
    p.write_text(yaml.dump(cfg_dict))
    cfg = load_config(p)
    assert cfg.providers["groq"].header_parser == "openai_compatible"


def test_provider_header_parser_explicit_value_is_read(tmp_path, monkeypatch):
    monkeypatch.setenv("CEREBRAS_KEY", "k")
    cfg_dict = {
        "tiers": {"low": [{"provider": "cb", "model": "m", "score": 50, "rpm": 1, "tpm": 1}]},
        "providers": {"cb": {
            "base_url": "https://x", "api_keys": [{"env": "CEREBRAS_KEY"}],
            "header_parser": "cerebras",
        }},
    }
    p = tmp_path / "flexrouter.yaml"
    p.write_text(yaml.dump(cfg_dict))
    cfg = load_config(p)
    assert cfg.providers["cb"].header_parser == "cerebras"
