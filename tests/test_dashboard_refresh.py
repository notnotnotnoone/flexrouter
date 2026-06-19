import json
from flexrouter.dashboard import api


def test_get_last_refresh_default(tmp_path):
    assert api.get_last_refresh(str(tmp_path)) == {"timestamp": None}


def test_get_last_refresh_reads_file(tmp_path):
    (tmp_path / "last_refresh.json").write_text(json.dumps({"timestamp": "t", "added": ["a"]}))
    out = api.get_last_refresh(str(tmp_path))
    assert out["added"] == ["a"]
