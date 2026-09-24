# tests/test_events.py
import csv

import pytest

from flexrouter import redact
from flexrouter.events import EventLogger


@pytest.fixture(autouse=True)
def _heuristic_redaction_on():
    """redact_errors defaults to off (2026-09-24) - see test_error_envelope.py."""
    redact.set_enabled(True)
    yield
    redact.set_enabled(False)

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

def test_a_key_shaped_detail_is_scrubbed_before_it_reaches_disk(tmp_path):
    from flexrouter.events import EventLogger

    leaked = "sk-proj-AAAABBBBCCCCDDDDEEEE1234"
    logger = EventLogger(str(tmp_path))
    logger.record("groq", "llama", "rate_limited", detail=f"429: key {leaked} throttled")

    on_disk = (tmp_path / "events.csv").read_text(encoding="utf-8")
    assert leaked not in on_disk

def test_events_csv_round_trips_as_utf8(tmp_path):
    from flexrouter.events import EventLogger

    logger = EventLogger(str(tmp_path))
    logger.record("groq", "llama", "rate_limited", detail="took …1234 characters")

    raw = (tmp_path / "events.csv").read_bytes()
    raw.decode("utf-8")  # must not raise
