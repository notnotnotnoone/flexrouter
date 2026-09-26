"""refresh_known_model_ids (PLAN-V2.3.md Session 5, grill-decisions.md §12):
the always-on, read-only half of discovery. It caches each provider's real
model IDs for catalogue.is_real/did_you_mean and flags configured models the
provider no longer lists - it never touches config.yaml, overrides.json, or
catalog_pending.json.
"""
import json
from pathlib import Path

import flexrouter.refresh as refresh
from flexrouter.catalogue import KNOWN_MODEL_IDS_FILENAME, is_real
from flexrouter.keys import add_key
from flexrouter.refresh import refresh_known_model_ids

MODERN = """\
providers:
  groq:
    base_url: https://api.groq.com/openai/v1
buckets:
  default:
    - {provider: groq, model: old-model, score: 50, rpm: 30, tpm: 6000, context_window: 131072}
settings:
  state_dir: STATE
"""


def _write(tmp_path, template=MODERN):
    state = tmp_path / "st"
    cfg = tmp_path / "flexrouter.yaml"
    cfg.write_text(template.replace("STATE", str(state).replace("\\", "/")))
    return str(cfg), str(state)


def _stub_ids(monkeypatch, ids_by_provider):
    async def fake_list_model_ids(provider, api_key):
        return ids_by_provider.get(provider.name, [])

    monkeypatch.setattr(refresh, "list_model_ids", fake_list_model_ids)


def test_caches_the_real_ids_it_reads(tmp_path, monkeypatch):
    cfg, state = _write(tmp_path)
    add_key("groq", "gsk-saved-key")
    _stub_ids(monkeypatch, {"groq": ["old-model", "brand-new-model"]})

    cache = refresh_known_model_ids(cfg, state)

    assert cache["groq"]["ids"] == ["brand-new-model", "old-model"]
    on_disk = json.loads((Path(state) / KNOWN_MODEL_IDS_FILENAME).read_text())
    assert on_disk["groq"]["ids"] == ["brand-new-model", "old-model"]
    assert is_real("groq", "brand-new-model", state) is True


def test_flags_a_configured_model_the_provider_no_longer_lists(tmp_path, monkeypatch):
    cfg, state = _write(tmp_path)
    add_key("groq", "gsk-saved-key")
    _stub_ids(monkeypatch, {"groq": ["some-other-model"]})

    refresh_known_model_ids(cfg, state)

    unknown = json.loads((Path(state) / "unknown_configured_models.json").read_text())
    assert unknown["models"] == ["groq/old-model"]
    assert is_real("groq", "old-model", state) is False


def test_never_writes_a_pending_or_config_file(tmp_path, monkeypatch):
    """This is the read-only half of discovery - it must never stage
    anything for the owner to accept, unlike the full refresh_config."""
    cfg, state = _write(tmp_path)
    add_key("groq", "gsk-saved-key")
    _stub_ids(monkeypatch, {"groq": ["old-model", "brand-new-model"]})

    refresh_known_model_ids(cfg, state)

    assert not (Path(state) / "catalog_pending.json").exists()
    assert not (Path(state) / "last_refresh.json").exists()


def test_a_provider_that_fails_to_answer_keeps_its_last_known_ids(tmp_path, monkeypatch):
    cfg, state = _write(tmp_path)
    add_key("groq", "gsk-saved-key")
    _stub_ids(monkeypatch, {"groq": ["old-model"]})
    refresh_known_model_ids(cfg, state)

    async def broken_list_model_ids(provider, api_key):
        raise RuntimeError("groq is down")

    monkeypatch.setattr(refresh, "list_model_ids", broken_list_model_ids)
    refresh_known_model_ids(cfg, state)

    assert is_real("groq", "old-model", state) is True


def test_a_provider_with_no_resolvable_key_is_left_unchecked(tmp_path, monkeypatch):
    cfg, state = _write(tmp_path)
    # No add_key(), no env var - groq has no credential to check with.
    _stub_ids(monkeypatch, {"groq": ["old-model"]})

    refresh_known_model_ids(cfg, state)

    assert not (Path(state) / KNOWN_MODEL_IDS_FILENAME).exists() or json.loads(
        (Path(state) / KNOWN_MODEL_IDS_FILENAME).read_text()) == {}
    # Never checked - benefit of the doubt, not "not real".
    assert is_real("groq", "old-model", state) is True
