import json

from flexrouter.store import harden, read_json, write_json


def test_read_json_returns_default_when_missing(tmp_path):
    assert read_json(tmp_path / "nope.json") == {}
    assert read_json(tmp_path / "nope.json", default=[]) == []


def test_read_json_returns_default_when_corrupt(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json at all", encoding="utf-8")
    assert read_json(p) == {}


def test_write_json_creates_parent_directories(tmp_path):
    p = tmp_path / "a" / "b" / "c.json"
    write_json(p, {"hello": "world"})
    assert json.loads(p.read_text(encoding="utf-8")) == {"hello": "world"}


def test_write_json_round_trips(tmp_path):
    p = tmp_path / "x.json"
    write_json(p, {"n": 1})
    write_json(p, {"n": 2})
    assert read_json(p) == {"n": 2}


def test_write_json_leaves_no_temp_files_behind(tmp_path):
    p = tmp_path / "x.json"
    write_json(p, {"n": 1})
    assert [f.name for f in tmp_path.iterdir()] == ["x.json"]


def test_harden_is_safe_on_a_missing_file(tmp_path):
    harden(tmp_path / "absent.json")  # must not raise


def test_harden_leaves_the_file_readable_by_us(tmp_path):
    p = tmp_path / "secret.json"
    write_json(p, {"secret": "s"})
    harden(p)
    assert read_json(p) == {"secret": "s"}
