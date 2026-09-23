"""Regression test for the flat-score bug (see .scratch/model-reliability/issues/01-fix-flat-model-scores.md).

Every model in the shipped flexrouter.yaml's `default` tier used to have the
identical `score: 50`, which made `RoutingEngine._pick`'s 80%-of-best
threshold always include the entire tier, so `random.choice` picked
uniformly at random across all 55 models instead of preferring
faster/more-reliable providers. This test asserts the scores are actually
differentiated so the bug can't silently regress.

flexrouter.yaml is gitignored (it holds live provider API keys) — these
tests only produce meaningful results when run against a real, local copy
of the file, the same way they always have.
"""
from pathlib import Path

import pytest

from flexrouter.config import load_config

REPO_ROOT = Path(__file__).resolve().parent.parent
SHIPPED_CONFIG = REPO_ROOT / "flexrouter.yaml"

pytestmark = pytest.mark.skipif(
    not SHIPPED_CONFIG.exists(),
    reason="needs the local, gitignored flexrouter.yaml (it holds live keys)")


def test_shipped_config_default_tier_scores_are_not_all_identical():
    cfg = load_config(SHIPPED_CONFIG)
    scores = [m.score for m in cfg.tiers["default"]]
    assert len(scores) > 0
    assert len(set(scores)) > 1, (
        "All default-tier models have the same score, which means "
        "RoutingEngine._pick's 80%-of-best threshold will always include "
        "the entire tier and pick uniformly at random instead of "
        "preferring faster/more-reliable providers."
    )


def test_shipped_config_every_model_has_a_positive_score():
    cfg = load_config(SHIPPED_CONFIG)
    for tier_name, models in cfg.tiers.items():
        for m in models:
            assert m.score > 0, (
                f"{tier_name}/{m.provider}/{m.model} has a non-positive score"
            )


def test_shipped_config_default_tier_prefers_googleai_over_cerebras_and_groq():
    cfg = load_config(SHIPPED_CONFIG)
    scores_by_provider: dict[str, list[int]] = {}
    for m in cfg.tiers["default"]:
        scores_by_provider.setdefault(m.provider, []).append(m.score)
    assert min(scores_by_provider["googleai"]) > max(scores_by_provider["cerebras"])
    assert min(scores_by_provider["googleai"]) > max(scores_by_provider["groq"])


def test_shipped_config_has_a_quick_tier_of_groq_and_cerebras():
    cfg = load_config(SHIPPED_CONFIG)
    assert "quick" in cfg.tiers
    assert len(cfg.tiers["quick"]) > 0
    providers = {m.provider for m in cfg.tiers["quick"]}
    assert providers == {"groq", "cerebras"}
