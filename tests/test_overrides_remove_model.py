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


def test_the_remove_route_takes_a_model_out_and_restore_puts_it_back(config_file):
    from fastapi.testclient import TestClient
    from flexrouter.app import create_app
    with TestClient(create_app(str(config_file))) as c:
        r = c.post("/buckets/low/models/remove", data={"id": "groq/llama-3.1-8b-instant"})
        assert r.json() == {"ok": True}
        assert ov.load_overrides()["removed_models"]["low"] == ["groq/llama-3.1-8b-instant"]
        c.post("/buckets/low/models/remove",
               data={"id": "groq/llama-3.1-8b-instant", "restore": "1"})
        assert "removed_models" not in ov.load_overrides()
        assert c.post("/buckets/low/models/remove", data={"id": "bad"}).status_code == 400
