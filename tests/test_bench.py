import pytest

from flexrouter.audit import AuditLogger
from flexrouter.bench import check_response_rates
from flexrouter.recovery import PenaltyBox


def _seed(tmp_path, ok_count, bad_count, provider="groq", model="llama"):
    log = AuditLogger(str(tmp_path))
    for _ in range(ok_count):
        log.log("default", provider, model, 100, 50, 0.0, 200, "ok")
    for _ in range(bad_count):
        log.log("default", provider, model, 0, 0, 0.0, 100, "rate_limited")
    return tmp_path


def test_benches_a_model_below_threshold(tmp_path):
    _seed(tmp_path, ok_count=1, bad_count=10)  # 11 requests, ~9% response rate
    pb = PenaltyBox(base_seconds=30, max_seconds=1800)
    benched = check_response_rates(str(tmp_path), pb)
    assert benched == [("groq", "llama")]
    assert pb.is_quarantined("groq", "llama") is True


def test_does_not_bench_a_model_above_threshold(tmp_path):
    _seed(tmp_path, ok_count=8, bad_count=3)  # 11 requests, ~73% response rate
    pb = PenaltyBox(base_seconds=30, max_seconds=1800)
    benched = check_response_rates(str(tmp_path), pb)
    assert benched == []
    assert pb.is_quarantined("groq", "llama") is False


def test_does_not_bench_at_exactly_the_request_floor(tmp_path):
    _seed(tmp_path, ok_count=1, bad_count=9)  # 10 requests, 10% response rate
    pb = PenaltyBox(base_seconds=30, max_seconds=1800)
    benched = check_response_rates(str(tmp_path), pb)
    assert benched == []


def test_skips_a_model_already_quarantined(tmp_path):
    _seed(tmp_path, ok_count=1, bad_count=10)
    pb = PenaltyBox(base_seconds=30, max_seconds=1800)
    pb.quarantine("groq", "llama", "manually set aside", seconds=3600)
    benched = check_response_rates(str(tmp_path), pb)
    assert benched == []
    assert pb.quarantine_reason("groq", "llama") == "manually set aside"


def test_quarantine_reason_names_rate_and_request_count(tmp_path):
    _seed(tmp_path, ok_count=1, bad_count=10)
    pb = PenaltyBox(base_seconds=30, max_seconds=1800)
    check_response_rates(str(tmp_path), pb)
    reason = pb.quarantine_reason("groq", "llama")
    assert "9%" in reason
    assert "11" in reason


def test_quarantines_for_a_week_by_default(tmp_path):
    _seed(tmp_path, ok_count=1, bad_count=10)
    pb = PenaltyBox(base_seconds=30, max_seconds=1800)
    check_response_rates(str(tmp_path), pb)
    import time
    entry = pb.quarantined()["groq/llama"]
    assert entry["until"] > time.time() + 6 * 24 * 3600


def test_leaves_models_with_no_requests_alone(tmp_path):
    pb = PenaltyBox(base_seconds=30, max_seconds=1800)
    benched = check_response_rates(str(tmp_path), pb)
    assert benched == []
