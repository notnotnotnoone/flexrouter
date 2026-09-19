import json
from pathlib import Path
import yaml
import flexrouter.refresh as refresh
from flexrouter.refresh import refresh_config

OLD = """\
# This is the owner's own comment. It must survive a refresh untouched.
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


def test_settings_file_is_never_rewritten(tmp_path, monkeypatch):
    """The gap this stage closes: refresh must not touch config.yaml at all,
    not even to rebuild it with the same content — the owner's comments and
    layout have to survive byte-for-byte."""
    cfg, state = _setup(tmp_path, monkeypatch, {"groq": [
        {"id": "good-chat"}, {"id": "whisper-large-v3"}]})
    before = Path(cfg).read_bytes()
    refresh_config(cfg, state)
    after = Path(cfg).read_bytes()
    assert after == before
    assert "# This is the owner's own comment." in after.decode()


def test_modality_filter_drops_junk_from_pending(tmp_path, monkeypatch):
    cfg, state = _setup(tmp_path, monkeypatch, {"groq": [
        {"id": "good-chat"}, {"id": "whisper-large-v3"}]})
    refresh_config(cfg, state)
    pending = json.loads((Path(state) / "catalog_pending.json").read_text())
    appeared = [m["model"] for m in pending["groq"]["appeared"]]
    assert "good-chat" in appeared
    assert "whisper-large-v3" not in appeared


def test_keys_and_overrides_untouched(tmp_path, monkeypatch):
    cfg, state = _setup(tmp_path, monkeypatch, {"groq": [{"id": "x"}]})
    refresh_config(cfg, state)
    written = yaml.safe_load(Path(cfg).read_text())
    assert written["providers"]["groq"]["api_keys"] == ["test-key"]
    overrides_path = Path(state).parent / "overrides.json"
    assert not overrides_path.exists()


def test_pending_file_shape(tmp_path, monkeypatch):
    cfg, state = _setup(tmp_path, monkeypatch, {"groq": [{"id": "new-model", "context_length": 8192}]})
    result = refresh_config(cfg, state)
    pending_path = Path(state) / "catalog_pending.json"
    assert pending_path.exists()
    assert result.pending_path == str(pending_path)
    pending = json.loads(pending_path.read_text())

    groq = pending["groq"]
    assert groq["checked_at"] == result.timestamp
    assert groq["vanished"] == ["old-model"]
    assert groq["changed"] == []

    [appeared] = groq["appeared"]
    assert appeared["model"] == "new-model"
    assert appeared["free"] is True
    assert appeared["score"] == 50
    assert appeared["context_window"] == 8192
    assert "rpm" in appeared and "tpm" in appeared


def test_last_refresh_written_no_backup(tmp_path, monkeypatch):
    cfg, state = _setup(tmp_path, monkeypatch, {"groq": [{"id": "x"}]})
    result = refresh_config(cfg, state)
    assert not hasattr(result, "backup_path")
    assert not (Path(state) / "backups").exists()
    assert (Path(state) / "last_refresh.json").exists()
    saved = json.loads((Path(state) / "last_refresh.json").read_text())
    assert saved["timestamp"] == result.timestamp
    assert saved["pending_path"] == result.pending_path


def test_discovery_failure_collected(tmp_path, monkeypatch):
    cfg, state = _setup(tmp_path, monkeypatch, {})

    async def boom(provider, api_key):
        raise RuntimeError("network down")

    monkeypatch.setattr(refresh, "discover_models", boom)
    result = refresh_config(cfg, state)
    assert any(e["provider"] == "groq" for e in result.provider_errors)
