"""How a classifier gets configured, and what happens when it isn't.

The secret and the non-secret settings deliberately travel by different
routes. base_url and model are ordinary settings. The key is not: config.py
keeps auth_token out of ALLOWED_FIELDS on the grounds that anything able to
reach the dashboard could otherwise set a credential, so the decider's key
goes through service_keys.py (masked keys.json), which already reserves a
"decider" slot for exactly this.
"""
import os
import yaml

from flexrouter.config import load_config
from flexrouter.decider import HttpDecider, NullDecider


def _write(tmp_path, minimal_config, decider_settings):
    cfg = minimal_config
    cfg["settings"]["state_dir"] = str(tmp_path / ".flexrouter")
    if decider_settings is not None:
        cfg["settings"].update(decider_settings)
    p = tmp_path / "flexrouter.yaml"
    p.write_text(yaml.dump(cfg))
    return p


def test_no_decider_settings_means_no_classifier(tmp_path, minimal_config):
    cfg = load_config(_write(tmp_path, minimal_config, None))
    assert cfg.decider.base_url is None
    assert cfg.decider.enabled is False


def test_decider_settings_are_read_from_the_config(tmp_path, minimal_config):
    cfg = load_config(_write(tmp_path, minimal_config, {
        "decider_base_url": "https://classifier.example/v1",
        "decider_model": "some/classifier-1",
    }))
    assert cfg.decider.base_url == "https://classifier.example/v1"
    assert cfg.decider.model == "some/classifier-1"
    assert cfg.decider.enabled is True


def test_build_decider_returns_null_when_nothing_is_configured(tmp_path, minimal_config):
    from flexrouter.decider import build_decider
    cfg = load_config(_write(tmp_path, minimal_config, None))
    assert isinstance(build_decider(cfg.decider, key=None), NullDecider)


def test_build_decider_returns_null_without_a_key(tmp_path, minimal_config):
    """Configured but keyless is not usable. Falling back to Null keeps the
    router working rather than firing unauthenticated calls at every novel
    error and getting a 401 back each time."""
    from flexrouter.decider import build_decider
    cfg = load_config(_write(tmp_path, minimal_config, {
        "decider_base_url": "https://classifier.example/v1", "decider_model": "m"}))
    assert isinstance(build_decider(cfg.decider, key=None), NullDecider)


def test_build_decider_returns_a_real_classifier_when_fully_configured(tmp_path, minimal_config):
    from flexrouter.decider import build_decider
    cfg = load_config(_write(tmp_path, minimal_config, {
        "decider_base_url": "https://classifier.example/v1", "decider_model": "m"}))
    d = build_decider(cfg.decider, key="secret")
    assert isinstance(d, HttpDecider)
    assert d.configured is True


def _router_with(tmp_path, minimal_config, decider_settings, monkeypatch, key):
    from flexrouter import service_keys
    monkeypatch.setattr(service_keys, "resolve",
                        lambda name, path=None: key if name == "decider" else None)
    from flexrouter import LocalRouter
    return LocalRouter(str(_write(tmp_path, minimal_config, decider_settings)))


def test_router_runs_without_a_classifier_by_default(tmp_path, minimal_config, monkeypatch):
    r = _router_with(tmp_path, minimal_config, None, monkeypatch, None)
    assert isinstance(r._error_brain._decider, NullDecider)


def test_router_builds_the_classifier_from_settings_and_the_service_key(
        tmp_path, minimal_config, monkeypatch):
    r = _router_with(tmp_path, minimal_config,
                     {"decider_base_url": "https://classifier.example/v1", "decider_model": "m"},
                     monkeypatch, "secret")
    assert isinstance(r._error_brain._decider, HttpDecider)


def test_decider_settings_are_editable_from_the_dashboard(tmp_path, minimal_config, monkeypatch):
    """The endpoint and model id are ordinary settings. The key is not -- it
    stays in keys.json via service_keys, so nothing reaching the dashboard can
    write a credential into the shareable settings file."""
    from flexrouter.overrides import ALLOWED_FIELDS
    assert "decider_base_url" in ALLOWED_FIELDS["settings"]
    assert "decider_model" in ALLOWED_FIELDS["settings"]
    assert "decider_api_key" not in ALLOWED_FIELDS["settings"]
    assert "auth_token" not in ALLOWED_FIELDS["settings"]


def test_settings_page_shows_the_live_decider_values(tmp_path, minimal_config, monkeypatch):
    from flexrouter.dashboard import facts
    r = _router_with(tmp_path, minimal_config,
                     {"decider_base_url": "https://classifier.example/v1",
                      "decider_model": "m"}, monkeypatch, "secret")
    shown = {f.name: f.value for f in facts.settings_fields(r)}
    assert shown["decider_base_url"] == "https://classifier.example/v1"
    assert shown["decider_model"] == "m"
