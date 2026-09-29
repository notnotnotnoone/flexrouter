from flexrouter import overrides as ov


def _raw():
    return {"buckets": {
        "fast": [{"provider": "a", "model": "m"}, {"provider": "b", "model": "n"}],
        "smart": [{"provider": "a", "model": "m"}],
    }}


def test_remove_model_only_leaves_that_bucket(tmp_path):
    p = tmp_path / "o.json"
    ov.remove_model("fast", "a/m", p)
    merged = ov.apply_overrides(_raw(), ov.load_overrides(p))
    assert [m["model"] for m in merged["buckets"]["fast"]] == ["n"]
    assert [m["model"] for m in merged["buckets"]["smart"]] == ["m"]


def test_restore_and_readd_clear_the_tombstone(tmp_path):
    p = tmp_path / "o.json"
    ov.remove_model("fast", "a/m", p)
    ov.restore_model("fast", "a/m", p)
    assert "removed_models" not in ov.load_overrides(p)
    ov.remove_model("fast", "a/m", p)
    ov.add_model("fast", {"provider": "a", "model": "m", "score": 1, "rpm": 1, "tpm": 1}, p)
    assert "removed_models" not in ov.load_overrides(p)


def test_remove_model_rejects_non_identifiers(tmp_path):
    import pytest
    with pytest.raises(ValueError):
        ov.remove_model("fast", "nope", tmp_path / "o.json")
