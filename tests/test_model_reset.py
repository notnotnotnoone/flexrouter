"""Danger zone: reset one provider's (or every provider's) models and
everything flexrouter learned about them - and nothing else."""
import json

import pytest
from fastapi.testclient import TestClient

import flexrouter.app as app_module
from flexrouter import model_reset
from flexrouter.app import create_app


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def _read(path):
    return json.loads(path.read_text())


@pytest.fixture
def home(tmp_path):
    state = tmp_path / "state"
    ov = tmp_path / "overrides.json"
    _write(ov, {
        "models": {"groq/a": {"score": 50}, "mistral/b": {"score": 60}},
        "new_models": {"smart": [{"provider": "groq", "model": "a"},
                                 {"provider": "mistral", "model": "b"}],
                       "fast": [{"provider": "groq", "model": "c"}]},
        "new_buckets": ["Unclassified"],
        "new_providers": {"groq": {"base_url": "x"}},
        "settings": {"decider_model": "m"},
    })
    for name in ("score_facts", "rate_limit_facts", "model_facts", "rate_limits",
                 "status", "parked_models"):
        _write(state / f"{name}.json", {"groq/a": {"x": 1}, "groq/c/d": {"x": 1},
                                        "mistral/b": {"x": 2}})
    _write(state / "catalog_pending.json", {"groq": {"appeared": []}, "mistral": {"appeared": []}})
    _write(state / "key_state.json", {"groq": {"k1": {"state": "ok"}}})
    (state / "traces.jsonl").write_text('{"id": "t1"}\n')
    return state, ov


def test_resetting_one_provider_removes_only_its_models_and_facts(home):
    state, ov = home
    result = model_reset.reset(str(state), ov, "groq")
    o = _read(ov)
    assert o["new_models"] == {"smart": [{"provider": "mistral", "model": "b"}], "fast": []}
    assert o["models"] == {"mistral/b": {"score": 60}}
    for name in ("score_facts", "rate_limit_facts", "model_facts", "rate_limits",
                 "status", "parked_models"):
        assert _read(state / f"{name}.json") == {"mistral/b": {"x": 2}}, name
    assert _read(state / "catalog_pending.json") == {"mistral": {"appeared": []}}
    assert result.counts["models"] == 2


def test_a_provider_whose_name_prefixes_another_is_left_alone(home):
    state, ov = home
    _write(state / "score_facts.json", {"groq/a": {}, "groqcloud/a": {}})
    model_reset.reset(str(state), ov, "groq")
    assert _read(state / "score_facts.json") == {"groqcloud/a": {}}


def test_resetting_all_providers_empties_every_model_store(home):
    state, ov = home
    model_reset.reset(str(state), ov, None)
    o = _read(ov)
    assert o["new_models"] == {"smart": [], "fast": []}
    assert o["models"] == {}
    assert _read(state / "score_facts.json") == {}
    assert _read(state / "catalog_pending.json") == {}


def test_reset_never_touches_keys_providers_settings_buckets_or_history(home):
    state, ov = home
    before = {p: p.read_bytes() for p in (state / "key_state.json", state / "traces.jsonl")}
    model_reset.reset(str(state), ov, None)
    assert {p: p.read_bytes() for p in before} == before
    o = _read(ov)
    assert o["new_buckets"] == ["Unclassified"]
    assert o["new_providers"] == {"groq": {"base_url": "x"}}
    assert o["settings"] == {"decider_model": "m"}


def test_reset_backs_up_every_file_it_changes_first(home):
    state, ov = home
    original = ov.read_bytes()
    result = model_reset.reset(str(state), ov, "groq")
    assert (result.backup_dir / "overrides.json").read_bytes() == original
    assert _read(result.backup_dir / "score_facts.json")["groq/a"] == {"x": 1}
    assert not (result.backup_dir / "key_state.json").exists()


def test_preview_counts_without_changing_anything(home):
    state, ov = home
    original = ov.read_bytes()
    counts = model_reset.preview(str(state), ov, "groq")
    assert counts["models"] == 2
    assert counts["facts"] > 0
    assert ov.read_bytes() == original


def test_missing_files_are_fine(tmp_path):
    result = model_reset.reset(str(tmp_path / "state"), tmp_path / "overrides.json", "groq")
    assert result.counts["models"] == 0


# --- dashboard ------------------------------------------------------------------

@pytest.fixture
def client(config_file):
    with TestClient(create_app(str(config_file))) as c:
        yield c


def _add_groq_model(client):
    from flexrouter import overrides as ov
    ov.add_model("low", {"provider": "groq", "model": "extra", "score": 50,
                         "rpm": 60, "tpm": 60000})
    router = app_module.get_router()
    router._status.set_needs_you("groq", "extra", "gone", kind="gone", action="remove")


def test_provider_page_has_a_danger_zone(client):
    body = client.get("/providers/groq").text
    assert "Danger zone" in body
    assert 'action="/providers/groq/reset"' in body


def test_providers_page_has_a_reset_all_danger_zone(client):
    body = client.get("/providers").text
    assert "Danger zone" in body
    assert 'action="/providers/reset-all"' in body


def test_a_wrong_confirmation_resets_nothing(client):
    _add_groq_model(client)
    r = client.post("/providers/groq/reset", data={"confirm": "grok"}, follow_redirects=False)
    assert "ok=0" in r.headers["location"]
    router = app_module.get_router()
    assert router._status.get("groq", "extra").value == "needs_you"


def test_resetting_a_provider_clears_its_models_and_live_status(client):
    _add_groq_model(client)
    r = client.post("/providers/groq/reset", data={"confirm": "groq"}, follow_redirects=False)
    assert "ok=1" in r.headers["location"]
    router = app_module.get_router()
    assert router._status.get("groq", "extra").value == "ready"
    from flexrouter import overrides as ov
    assert not any(m.get("provider") == "groq"
                   for ms in ov.load_overrides().get("new_models", {}).values() for m in ms)


def test_reset_all_needs_the_exact_phrase(client):
    _add_groq_model(client)
    r = client.post("/providers/reset-all", data={"confirm": "yes"}, follow_redirects=False)
    assert "ok=0" in r.headers["location"]
    r = client.post("/providers/reset-all", data={"confirm": "reset all"}, follow_redirects=False)
    assert "ok=1" in r.headers["location"]
    assert app_module.get_router()._status.get("groq", "extra").value == "ready"
