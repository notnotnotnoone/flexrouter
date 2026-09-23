import json

import pytest

from flexrouter.dashboard import prefs


def test_missing_file_gives_defaults(tmp_path):
    p = prefs.load(tmp_path / "dashboard.json")
    assert p == prefs.Prefs()
    assert p.motion == "full"


def test_default_path_is_in_the_flexrouter_home(tmp_path, monkeypatch):
    monkeypatch.setenv("FLEXROUTER_HOME", str(tmp_path))
    prefs.save(prefs.Prefs(motion="reduced"))
    assert json.loads((tmp_path / "dashboard.json").read_text())["motion"] == "reduced"
    assert prefs.load().motion == "reduced"


def test_round_trip(tmp_path):
    path = tmp_path / "dashboard.json"
    prefs.save(prefs.Prefs(motion="off", refresh_seconds=30), path)
    assert prefs.load(path).motion == "off"
    assert prefs.load(path).refresh_seconds == 30


def test_a_corrupt_file_falls_back_to_defaults(tmp_path):
    path = tmp_path / "dashboard.json"
    path.write_text("{not json")
    assert prefs.load(path) == prefs.Prefs()


def test_unknown_keys_and_bad_values_are_ignored_on_load(tmp_path):
    path = tmp_path / "dashboard.json"
    path.write_text(json.dumps({"motion": "wild", "refresh_seconds": 30, "x": 1}))
    p = prefs.load(path)
    assert p.motion == "full"
    assert p.refresh_seconds == 30


@pytest.mark.parametrize("bad", [
    prefs.Prefs(motion="wild"),
    prefs.Prefs(refresh_seconds=1),
    prefs.Prefs(default_range="3y"),
    prefs.Prefs(timezone=""),
])
def test_save_refuses_bad_values(tmp_path, bad):
    with pytest.raises(ValueError):
        prefs.save(bad, tmp_path / "dashboard.json")


def test_range_keys_match_the_pages_ranges():
    from flexrouter.dashboard.pages import RANGES
    assert prefs.RANGE_KEYS == tuple(r[0] for r in RANGES)
