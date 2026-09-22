"""Behaviour knobs that were hardcoded constants.

Each of these changed how the router behaves but could only be altered by
editing source. The most consequential was ErrorBrain's confidence_threshold:
read at nine places in _router to decide whether to act on a verdict at all,
yet only ever a constructor default nothing passed.

Defaults are preserved exactly, so exposing them changes nothing until
somebody sets one.
"""
import yaml

from flexrouter.config import load_config


def _cfg(tmp_path, minimal_config, settings=None):
    c = minimal_config
    c["settings"]["state_dir"] = str(tmp_path / ".flexrouter")
    c["settings"].update(settings or {})
    p = tmp_path / "flexrouter.yaml"
    p.write_text(yaml.dump(c))
    return load_config(p)


def test_defaults_are_unchanged(tmp_path, minimal_config):
    cfg = _cfg(tmp_path, minimal_config)
    assert cfg.decider.confidence_threshold == 0.80
    assert cfg.decider.rule_prior_confidence == 0.6
    assert cfg.decider.confidence_ceiling == 0.95
    assert cfg.decider.contested_statuses == (400, 403, 429)
    assert cfg.quarantine_seconds == 86400
    assert cfg.probe_timeout_seconds == 15.0
    assert cfg.error_max_length == 300
    assert cfg.unscored_fallback_score == 50


def test_every_knob_can_be_set(tmp_path, minimal_config):
    cfg = _cfg(tmp_path, minimal_config, {
        "decider_confidence_threshold": 0.5,
        "decider_rule_prior_confidence": 0.25,
        "decider_confidence_ceiling": 0.7,
        "decider_contested_statuses": "429, 418",
        "quarantine_seconds": 60,
        "probe_timeout_seconds": 2.5,
        "error_max_length": 80,
        "unscored_fallback_score": 7,
    })
    assert cfg.decider.confidence_threshold == 0.5
    assert cfg.decider.rule_prior_confidence == 0.25
    assert cfg.decider.confidence_ceiling == 0.7
    assert cfg.decider.contested_statuses == (429, 418)
    assert cfg.quarantine_seconds == 60
    assert cfg.probe_timeout_seconds == 2.5
    assert cfg.error_max_length == 80
    assert cfg.unscored_fallback_score == 7


def test_all_eight_are_editable_from_the_dashboard():
    from flexrouter.overrides import ALLOWED_FIELDS
    for name in ("decider_confidence_threshold", "decider_rule_prior_confidence",
                 "decider_confidence_ceiling", "decider_contested_statuses",
                 "quarantine_seconds", "probe_timeout_seconds",
                 "error_max_length", "unscored_fallback_score"):
        assert name in ALLOWED_FIELDS["settings"], name


def test_the_settings_page_shows_every_knob(tmp_path, minimal_config, monkeypatch):
    from flexrouter.dashboard import facts
    from flexrouter import LocalRouter
    c = minimal_config
    c["settings"]["state_dir"] = str(tmp_path / ".flexrouter")
    c["settings"]["decider_confidence_threshold"] = 0.42
    p = tmp_path / "flexrouter.yaml"
    p.write_text(yaml.dump(c))
    r = LocalRouter(str(p))
    shown = {f.name: f.value for f in facts.settings_fields(r)}
    assert shown["decider_confidence_threshold"] == 0.42
    assert shown["quarantine_seconds"] == 86400


def test_the_threshold_actually_reaches_the_error_brain(tmp_path, minimal_config):
    """The hole this whole change exists to close: the number was read in nine
    places but nothing ever set it."""
    from flexrouter import LocalRouter
    c = minimal_config
    c["settings"]["state_dir"] = str(tmp_path / ".flexrouter")
    c["settings"]["decider_confidence_threshold"] = 0.33
    p = tmp_path / "flexrouter.yaml"
    p.write_text(yaml.dump(c))
    r = LocalRouter(str(p))
    assert r._error_brain.confidence_threshold == 0.33


def test_contested_statuses_reach_the_error_brain(tmp_path, minimal_config):
    from flexrouter import LocalRouter
    c = minimal_config
    c["settings"]["state_dir"] = str(tmp_path / ".flexrouter")
    c["settings"]["decider_contested_statuses"] = "418"
    p = tmp_path / "flexrouter.yaml"
    p.write_text(yaml.dump(c))
    r = LocalRouter(str(p))
    assert r._error_brain.contested_statuses == (418,)


def _router(tmp_path, minimal_config, settings):
    from flexrouter import LocalRouter
    c = minimal_config
    c["settings"]["state_dir"] = str(tmp_path / ".flexrouter")
    c["settings"].update(settings)
    p = tmp_path / "flexrouter.yaml"
    p.write_text(yaml.dump(c))
    return LocalRouter(str(p))


def test_quarantine_seconds_changes_how_long_a_route_is_sidelined(tmp_path, minimal_config):
    import time
    r = _router(tmp_path, minimal_config, {"quarantine_seconds": 30})
    r._penalties.quarantine("groq", "some-model", "gone")
    entry = r._penalties._quarantine[r._penalties._key("groq", "some-model")]
    assert 25 < entry["until"] - time.time() <= 30


def test_an_explicit_quarantine_length_still_wins(tmp_path, minimal_config):
    import time
    r = _router(tmp_path, minimal_config, {"quarantine_seconds": 30})
    r._penalties.quarantine("groq", "some-model", "gone", seconds=300)
    entry = r._penalties._quarantine[r._penalties._key("groq", "some-model")]
    assert entry["until"] - time.time() > 200


def test_error_max_length_changes_where_provider_text_is_clipped(tmp_path, minimal_config):
    from flexrouter import errors
    before = errors.MAX_LENGTH
    try:
        _router(tmp_path, minimal_config, {"error_max_length": 40})
        assert errors.MAX_LENGTH == 40
    finally:
        errors.set_max_length(before)


def test_unscored_fallback_score_is_used_when_there_is_no_aa_key():
    import asyncio
    from flexrouter.catalogue import score_with_aa
    scored = asyncio.run(score_with_aa([{"id": "x/y"}], aa_key=None, unscored_fallback=7))
    assert scored[0]["score"] == 7
