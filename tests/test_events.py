# tests/test_events.py
import csv
from flexrouter.events import EventLogger

def test_creates_csv_with_headers(tmp_path):
    log = EventLogger(str(tmp_path))
    log.record("groq", "llama", "penalized", detail="429", penalty_seconds=30)
    with (tmp_path / "events.csv").open() as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["event_type"] == "penalized"
    assert rows[0]["provider"] == "groq"
    assert rows[0]["penalty_seconds"] == "30"

def test_appends_rows(tmp_path):
    log = EventLogger(str(tmp_path))
    log.record("groq", "llama", "penalized")
    log.record("groq", "llama", "recovered")
    assert len(log.recent()) == 2
    assert log.recent()[-1]["event_type"] == "recovered"

def test_rejects_unknown_event_type(tmp_path):
    log = EventLogger(str(tmp_path))
    try:
        log.record("groq", "llama", "exploded")
        assert False, "should have raised"
    except ValueError:
        pass
