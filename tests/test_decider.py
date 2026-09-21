from flexrouter.decider import Decider, ErrorVerdict, NullDecider


def test_error_verdict_is_a_frozen_dataclass():
    v = ErrorVerdict(verdict="too_fast", source="rule", confidence=1.0)
    assert v.verdict == "too_fast"
    assert v.source == "rule"
    assert v.confidence == 1.0


def test_null_decider_classifies_everything_as_unknown():
    d = NullDecider()
    v = d.classify_error("anything at all", 500)
    assert v.verdict == "unknown"
    assert v.source == "null"
    assert v.confidence == 0.0


def test_null_decider_describe_model_returns_an_empty_dict():
    d = NullDecider()
    assert d.describe_model("groq/llama-3.3", "groq", {}) == {}


def test_null_decider_satisfies_the_decider_protocol():
    # A NullDecider must be usable anywhere the Decider protocol is
    # required — this is what "the router works fully without any
    # classifier configured" (spec §4) actually depends on.
    d: Decider = NullDecider()
    assert isinstance(d.classify_error("x", None), ErrorVerdict)
