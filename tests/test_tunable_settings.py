"""Behaviour knobs that were hardcoded constants.

PLAN-V2.3.md Session 10 removed the decider's confidence knobs and
`quarantine_seconds` from Settings again - ErrorBrain's own class defaults
are now the only source (see test_error_brain.py), and the status system
replaced quarantine outright. What is left here is what Session 10 did not
touch: `probe_timeout_seconds`, `error_max_length`, `unscored_fallback_score`.

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
    assert cfg.probe_timeout_seconds == 15.0
    assert cfg.error_max_length == 300
    assert cfg.unscored_fallback_score == 50


def test_every_knob_can_be_set(tmp_path, minimal_config):
    cfg = _cfg(tmp_path, minimal_config, {
        "probe_timeout_seconds": 2.5,
        "error_max_length": 80,
        "unscored_fallback_score": 7,
    })
    assert cfg.probe_timeout_seconds == 2.5
    assert cfg.error_max_length == 80
    assert cfg.unscored_fallback_score == 7


def test_all_three_are_editable_from_the_dashboard():
    from flexrouter.overrides import ALLOWED_FIELDS
    for name in ("probe_timeout_seconds", "error_max_length", "unscored_fallback_score"):
        assert name in ALLOWED_FIELDS["settings"], name


def test_the_decider_confidence_knobs_are_no_longer_settings():
    """PLAN-V2.3.md Session 10: these are internal ErrorBrain constants now,
    not something the dashboard or a settings file can change."""
    from flexrouter.overrides import ALLOWED_FIELDS
    for name in ("decider_confidence_threshold", "decider_rule_prior_confidence",
                 "decider_confidence_ceiling", "decider_contested_statuses",
                 "quarantine_seconds", "retries", "backoff_seconds", "retry_policy",
                 "penalty_base_seconds", "penalty_max_seconds"):
        assert name not in ALLOWED_FIELDS["settings"], name


def _router(tmp_path, minimal_config, settings):
    from flexrouter import LocalRouter
    c = minimal_config
    c["settings"]["state_dir"] = str(tmp_path / ".flexrouter")
    c["settings"].update(settings)
    p = tmp_path / "flexrouter.yaml"
    p.write_text(yaml.dump(c))
    return LocalRouter(str(p))


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
