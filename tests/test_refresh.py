import json
from pathlib import Path
import yaml
import flexrouter.refresh as refresh
from flexrouter.refresh import refresh_config

OLD = """
providers:
  groq:
    base_url: https://api.groq.com/openai/v1
    api_keys: [test-key]
tiers:
  default:
    - {provider: groq, model: old-model, score: 50, rpm: 30, tpm: 6000, context_window: 131072}
settings:
  state_dir: STATE
"""


def _setup(tmp_path, monkeypatch, discovered):
    state = tmp_path / "st"
    cfg = tmp_path / "flexrouter.yaml"
    cfg.write_text(OLD.replace("STATE", str(state).replace("\\", "/")))

    async def fake_discover(provider, api_key):
        return discovered.get(provider.name, [])

    async def fake_score(models, aa_key):
        return [{**m, "score": 50} for m in models]

    monkeypatch.setattr(refresh, "discover_models", fake_discover)
    monkeypatch.setattr(refresh, "score_with_aa", fake_score)
    return str(cfg), str(state)


def test_diff_added_and_removed(tmp_path, monkeypatch):
    cfg, state = _setup(tmp_path, monkeypatch, {"groq": [{"id": "new-model", "context_length": 8192}]})
    result = refresh_config(cfg, state)
    assert "groq/new-model" in result.added
    assert "groq/old-model" in result.removed


def test_modality_filter_drops_junk(tmp_path, monkeypatch):
    cfg, state = _setup(tmp_path, monkeypatch, {"groq": [
        {"id": "good-chat"}, {"id": "whisper-large-v3"}]})
    result = refresh_config(cfg, state)
    written = yaml.safe_load(Path(cfg).read_text())
    models = [m["model"] for m in written["tiers"]["default"]]
    assert "good-chat" in models
    assert "whisper-large-v3" not in models


def test_keys_preserved(tmp_path, monkeypatch):
    cfg, state = _setup(tmp_path, monkeypatch, {"groq": [{"id": "x"}]})
    refresh_config(cfg, state)
    written = yaml.safe_load(Path(cfg).read_text())
    assert written["providers"]["groq"]["api_keys"] == [{"key": "test-key"}]


def test_backup_created_and_last_refresh_written(tmp_path, monkeypatch):
    cfg, state = _setup(tmp_path, monkeypatch, {"groq": [{"id": "x"}]})
    result = refresh_config(cfg, state)
    assert Path(result.backup_path).exists()
    assert (Path(state) / "last_refresh.json").exists()
    saved = json.loads((Path(state) / "last_refresh.json").read_text())
    assert saved["timestamp"] == result.timestamp


def test_discovery_failure_collected(tmp_path, monkeypatch):
    cfg, state = _setup(tmp_path, monkeypatch, {})

    async def boom(provider, api_key):
        raise RuntimeError("network down")

    monkeypatch.setattr(refresh, "discover_models", boom)
    result = refresh_config(cfg, state)
    assert any(e["provider"] == "groq" for e in result.provider_errors)
