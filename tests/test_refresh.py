import json
from pathlib import Path

import yaml

import flexrouter.refresh as refresh
from flexrouter import home
from flexrouter.keys import add_key
from flexrouter.refresh import refresh_config

# The shape a correct installation has: `buckets:`, and no credential
# anywhere near the settings file. Every test below uses this unless it is
# specifically about backward compatibility.
MODERN = """\
# This is the owner's own comment. It must survive a refresh untouched.
providers:
  groq:
    base_url: https://api.groq.com/openai/v1
buckets:
  default:
    - {provider: groq, model: old-model, score: 50, rpm: 30, tpm: 6000, context_window: 131072}
settings:
  state_dir: STATE
"""

# The shape the stage tells owners never to have: `tiers:`, key typed inline.
# Kept to prove refresh still reads it.
LEGACY = """\
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


def _stub_discovery(monkeypatch, discovered):
    async def fake_discover(provider, api_key):
        return discovered.get(provider.name, [])

    async def fake_score(models, aa_key, **kw):
        return [{**m, "score": 50} for m in models]

    monkeypatch.setattr(refresh, "discover_models", fake_discover)
    monkeypatch.setattr(refresh, "score_with_aa", fake_score)


def _write(tmp_path, template):
    state = tmp_path / "st"
    cfg = tmp_path / "flexrouter.yaml"
    cfg.write_text(template.replace("STATE", str(state).replace("\\", "/")))
    return str(cfg), str(state)


def _setup(tmp_path, monkeypatch, discovered, template=MODERN, saved_key=True):
    """A correct installation: modern settings, credential in the key store."""
    cfg, state = _write(tmp_path, template)
    if saved_key:
        add_key("groq", "gsk-saved-key")
    _stub_discovery(monkeypatch, discovered)
    return cfg, state


# ---------------------------------------------------------------------------
# Credential resolution (spec §1 order: saved key, env var, inline)
# ---------------------------------------------------------------------------


def test_finds_models_with_buckets_and_a_saved_key(tmp_path, monkeypatch):
    """The configuration this stage creates. Before the fix, refresh read
    credentials only from inline `api_keys`, found none, and reported
    '0 new, 0 gone' without having asked the provider anything."""
    cfg, state = _setup(tmp_path, monkeypatch,
                        {"groq": [{"id": "new-model", "context_length": 8192}]})
    result = refresh_config(cfg, state)
    assert "groq/new-model" in result.added
    assert "groq/old-model" in result.removed


def test_finds_models_with_buckets_and_an_environment_variable(tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_KEY_FOR_TEST", "gsk-from-the-environment")
    settings = MODERN.replace(
        "    base_url: https://api.groq.com/openai/v1",
        "    base_url: https://api.groq.com/openai/v1\n"
        "    api_key_env: GROQ_KEY_FOR_TEST")
    cfg, state = _setup(tmp_path, monkeypatch,
                        {"groq": [{"id": "new-model", "context_length": 8192}]},
                        template=settings, saved_key=False)
    result = refresh_config(cfg, state)
    assert "groq/new-model" in result.added
    assert "groq/old-model" in result.removed


def test_a_saved_key_wins_over_one_typed_into_the_settings_file(tmp_path, monkeypatch):
    seen = {}

    async def fake_discover(provider, api_key):
        seen[provider.name] = api_key
        return []

    async def fake_score(models, aa_key, **kw):
        return models

    monkeypatch.setattr(refresh, "discover_models", fake_discover)
    monkeypatch.setattr(refresh, "score_with_aa", fake_score)
    cfg, state = _write(tmp_path, LEGACY)
    add_key("groq", "gsk-saved-key")
    refresh_config(cfg, state)
    assert seen["groq"] == "gsk-saved-key"


def test_still_reads_the_legacy_shape(tmp_path, monkeypatch):
    """Backward compatibility: `tiers:` plus a key typed straight into the
    settings file still resolves, even though nothing should look like this."""
    cfg, state = _setup(tmp_path, monkeypatch,
                        {"groq": [{"id": "new-model", "context_length": 8192}]},
                        template=LEGACY, saved_key=False)
    result = refresh_config(cfg, state)
    assert "groq/new-model" in result.added
    assert "groq/old-model" in result.removed


def test_a_provider_with_no_credential_at_all_is_not_checked(tmp_path, monkeypatch):
    cfg, state = _setup(tmp_path, monkeypatch, {"groq": [{"id": "x"}]}, saved_key=False)
    result = refresh_config(cfg, state)
    assert result.added == []
    assert result.removed == []
    assert json.loads((Path(state) / "catalog_pending.json").read_text()) == {}


# ---------------------------------------------------------------------------
# The settings file is never written
# ---------------------------------------------------------------------------


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


def test_keys_and_overrides_untouched(tmp_path, monkeypatch):
    cfg, state = _setup(tmp_path, monkeypatch, {"groq": [{"id": "x"}]},
                        template=LEGACY, saved_key=False)
    refresh_config(cfg, state)
    written = yaml.safe_load(Path(cfg).read_text())
    assert written["providers"]["groq"]["api_keys"] == ["test-key"]
    assert not home.overrides_path().exists()


# ---------------------------------------------------------------------------
# What gets recorded
# ---------------------------------------------------------------------------


def test_modality_filter_drops_junk_from_pending(tmp_path, monkeypatch):
    cfg, state = _setup(tmp_path, monkeypatch, {"groq": [
        {"id": "good-chat"}, {"id": "whisper-large-v3"}]})
    refresh_config(cfg, state)
    pending = json.loads((Path(state) / "catalog_pending.json").read_text())
    appeared = [m["model"] for m in pending["groq"]["appeared"]]
    assert "good-chat" in appeared
    assert "whisper-large-v3" not in appeared


def test_pending_file_shape(tmp_path, monkeypatch):
    cfg, state = _setup(tmp_path, monkeypatch,
                        {"groq": [{"id": "new-model", "context_length": 8192}]})
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


# ---------------------------------------------------------------------------
# A provider that could not be reached
# ---------------------------------------------------------------------------


def test_discovery_failure_collected(tmp_path, monkeypatch):
    cfg, state = _setup(tmp_path, monkeypatch, {})

    async def boom(provider, api_key):
        raise RuntimeError("network down")

    monkeypatch.setattr(refresh, "discover_models", boom)
    result = refresh_config(cfg, state)
    assert any(e["provider"] == "groq" for e in result.provider_errors)


def test_a_provider_that_errored_is_not_recorded_as_checked(tmp_path, monkeypatch):
    """A provider being down must not read as 'checked just now, all clear'."""
    cfg, state = _setup(tmp_path, monkeypatch, {})

    async def boom(provider, api_key):
        raise RuntimeError("network down")

    monkeypatch.setattr(refresh, "discover_models", boom)
    result = refresh_config(cfg, state)

    assert result.removed == []  # not "everything vanished"
    pending = json.loads((Path(state) / "catalog_pending.json").read_text())
    assert "groq" not in pending


def test_earlier_findings_survive_a_run_that_could_not_reach_the_provider(
        tmp_path, monkeypatch):
    cfg, state = _setup(tmp_path, monkeypatch, {"groq": [{"id": "still-here"}]})
    first = refresh_config(cfg, state)
    assert first.removed == ["groq/old-model"]

    async def boom(provider, api_key):
        raise RuntimeError("network down")

    monkeypatch.setattr(refresh, "discover_models", boom)
    refresh_config(cfg, state)

    pending = json.loads((Path(state) / "catalog_pending.json").read_text())
    assert pending["groq"]["vanished"] == ["old-model"]
    assert pending["groq"]["checked_at"] == first.timestamp


def test_provider_error_text_never_carries_a_credential(tmp_path, monkeypatch):
    """Some providers echo the submitted key back in their error body, and
    that text is stored in state/ and shown on the dashboard."""
    cfg, state = _setup(tmp_path, monkeypatch, {})

    async def leaky(provider, api_key):
        raise RuntimeError(f"401 invalid key: {api_key}")

    monkeypatch.setattr(refresh, "discover_models", leaky)
    result = refresh_config(cfg, state)

    [err] = result.provider_errors
    assert "gsk-saved-key" not in err["error"]
    assert "…-key" in err["error"]
    stored = (Path(state) / "last_refresh.json").read_text()
    assert "gsk-saved-key" not in stored
