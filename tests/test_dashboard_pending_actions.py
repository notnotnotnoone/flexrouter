import pytest

from flexrouter._router import LocalRouter
from flexrouter.dashboard import pending_actions as pa
from flexrouter.overrides import load_overrides
from flexrouter.store import write_json


@pytest.fixture
def router(config_file):
    r = LocalRouter(str(config_file))
    yield r
    r.close()


def _write_pending(router, data):
    write_json(router._cfg.state_dir + "/catalog_pending.json", data)


def test_accept_appeared_adds_the_model_and_clears_it_from_pending(router):
    _write_pending(router, {
        "groq": {"checked_at": "now",
                 "appeared": [{"model": "brand-new", "score": 70, "rpm": 30,
                               "tpm": 6000, "context_window": 131072}],
                 "vanished": [], "changed": []},
    })
    pa.accept_appeared(router._cfg.state_dir, "groq", "brand-new", "low")

    ov = load_overrides()
    assert ov["new_models"]["low"][0]["model"] == "brand-new"

    remaining = pa._load(router._cfg.state_dir)
    assert remaining["groq"]["appeared"] == []


def test_accept_appeared_fills_in_defaults_when_the_catalogue_omitted_them(router):
    _write_pending(router, {
        "groq": {"checked_at": "now", "appeared": [{"model": "brand-new"}],
                 "vanished": [], "changed": []},
    })
    pa.accept_appeared(router._cfg.state_dir, "groq", "brand-new", "low")
    entry = load_overrides()["new_models"]["low"][0]
    assert entry["score"] == 50
    assert entry["rpm"] == 60
    assert entry["tpm"] == 60000


def test_accept_appeared_raises_for_an_unknown_model(router):
    _write_pending(router, {"groq": {"appeared": [], "vanished": [], "changed": []}})
    with pytest.raises(pa.PendingActionError):
        pa.accept_appeared(router._cfg.state_dir, "groq", "no-such-model", "low")


def test_reject_appeared_just_clears_it(router):
    _write_pending(router, {
        "groq": {"checked_at": "now",
                 "appeared": [{"model": "brand-new", "score": 70, "rpm": 30, "tpm": 6000}],
                 "vanished": [], "changed": []},
    })
    pa.reject_appeared(router._cfg.state_dir, "groq", "brand-new")
    assert load_overrides() == {}
    assert pa._load(router._cfg.state_dir)["groq"]["appeared"] == []


def test_accept_vanished_disables_the_model_and_clears_it(router):
    _write_pending(router, {
        "groq": {"checked_at": "now", "appeared": [],
                 "vanished": ["llama-3.1-8b-instant"], "changed": []},
    })
    pa.accept_vanished(router._cfg.state_dir, "groq", "llama-3.1-8b-instant")

    ov = load_overrides()
    assert ov["models"]["groq/llama-3.1-8b-instant"]["enabled"] is False
    assert pa._load(router._cfg.state_dir)["groq"]["vanished"] == []


def test_accept_vanished_raises_for_a_model_not_pending(router):
    _write_pending(router, {"groq": {"appeared": [], "vanished": [], "changed": []}})
    with pytest.raises(pa.PendingActionError):
        pa.accept_vanished(router._cfg.state_dir, "groq", "llama-3.1-8b-instant")


def test_reject_vanished_just_clears_it(router):
    _write_pending(router, {
        "groq": {"checked_at": "now", "appeared": [],
                 "vanished": ["llama-3.1-8b-instant"], "changed": []},
    })
    pa.reject_vanished(router._cfg.state_dir, "groq", "llama-3.1-8b-instant")
    assert load_overrides() == {}
    assert pa._load(router._cfg.state_dir)["groq"]["vanished"] == []


def test_accept_changed_applies_the_new_value_and_clears_it(router):
    _write_pending(router, {
        "groq": {"checked_at": "now", "appeared": [], "vanished": [],
                 "changed": [{"model": "llama-3.1-8b-instant", "field": "rpm",
                             "old": 60, "new": 120}]},
    })
    pa.accept_changed(router._cfg.state_dir, "groq", "llama-3.1-8b-instant", "rpm")

    ov = load_overrides()
    assert ov["models"]["groq/llama-3.1-8b-instant"]["rpm"] == 120
    assert pa._load(router._cfg.state_dir)["groq"]["changed"] == []


def test_accept_changed_raises_for_a_change_not_pending(router):
    _write_pending(router, {"groq": {"appeared": [], "vanished": [], "changed": []}})
    with pytest.raises(pa.PendingActionError):
        pa.accept_changed(router._cfg.state_dir, "groq", "llama-3.1-8b-instant", "rpm")


def test_reject_changed_just_clears_it(router):
    _write_pending(router, {
        "groq": {"checked_at": "now", "appeared": [], "vanished": [],
                 "changed": [{"model": "llama-3.1-8b-instant", "field": "rpm",
                             "old": 60, "new": 120}]},
    })
    pa.reject_changed(router._cfg.state_dir, "groq", "llama-3.1-8b-instant", "rpm")
    assert load_overrides() == {}
    assert pa._load(router._cfg.state_dir)["groq"]["changed"] == []
