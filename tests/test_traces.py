import json
from datetime import datetime, timedelta, timezone

from flexrouter.traces import TraceWriter, new_trace_id


def test_new_trace_id_has_the_req_prefix():
    tid = new_trace_id()
    assert tid.startswith("req_")
    assert len(tid) == len("req_") + 24


def test_two_trace_ids_differ():
    assert new_trace_id() != new_trace_id()


def test_write_appends_one_json_line(tmp_path):
    w = TraceWriter(str(tmp_path))
    w.write({"id": "req_1", "ok": True})
    w.write({"id": "req_2", "ok": False})
    lines = (tmp_path / "traces.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["id"] == "req_1"
    assert json.loads(lines[1])["id"] == "req_2"


def test_provider_message_is_scrubbed_on_the_way_in(tmp_path):
    w = TraceWriter(str(tmp_path))
    leaked = "sk-proj-AAAABBBBCCCCDDDDEEEE1234"
    w.write({
        "id": "req_1",
        "attempts": [{"n": 1, "provider": "openrouter", "model": "m",
                      "provider_message": f"Incorrect API key provided: {leaked}"}],
    })
    on_disk = (tmp_path / "traces.jsonl").read_text(encoding="utf-8")
    assert leaked not in on_disk
    assert "…1234" in on_disk


def test_skipped_detail_is_scrubbed_too(tmp_path):
    w = TraceWriter(str(tmp_path))
    leaked = "sk-proj-AAAABBBBCCCCDDDDEEEE1234"
    w.write({
        "id": "req_1",
        "skipped": [{"provider": "groq", "model": "m", "reason": "quarantined",
                     "detail": f"bad key {leaked}"}],
    })
    on_disk = (tmp_path / "traces.jsonl").read_text(encoding="utf-8")
    assert leaked not in on_disk


def test_write_is_utf8(tmp_path):
    w = TraceWriter(str(tmp_path))
    w.write({"id": "req_1", "attempts": [
        {"n": 1, "provider": "p", "model": "m", "provider_message": "…tail"}]})
    raw = (tmp_path / "traces.jsonl").read_bytes()
    raw.decode("utf-8")  # must not raise


def test_rotation_moves_yesterdays_file_to_a_dated_name(tmp_path):
    w = TraceWriter(str(tmp_path))
    w.write({"id": "req_1"})
    live = tmp_path / "traces.jsonl"
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    import os
    os.utime(live, (yesterday.timestamp(), yesterday.timestamp()))

    w.write({"id": "req_2"})

    dated = tmp_path / f"traces-{yesterday.date().isoformat()}.jsonl"
    assert dated.exists()
    assert json.loads(dated.read_text(encoding="utf-8").strip())["id"] == "req_1"
    assert json.loads(live.read_text(encoding="utf-8").strip())["id"] == "req_2"


def test_compact_deletes_dated_files_past_retention(tmp_path):
    w = TraceWriter(str(tmp_path), retention_days=30)
    now = datetime.now(timezone.utc)
    old = tmp_path / f"traces-{(now - timedelta(days=31)).date().isoformat()}.jsonl"
    recent = tmp_path / f"traces-{(now - timedelta(days=5)).date().isoformat()}.jsonl"
    old.write_text('{"id": "old"}\n', encoding="utf-8")
    recent.write_text('{"id": "recent"}\n', encoding="utf-8")

    w.compact(now=now)

    assert not old.exists()
    assert recent.exists()


def test_compact_ignores_files_that_do_not_match_the_dated_pattern(tmp_path):
    w = TraceWriter(str(tmp_path))
    junk = tmp_path / "traces-not-a-date.jsonl"
    junk.write_text("{}\n", encoding="utf-8")
    w.compact()  # must not raise
    assert junk.exists()
