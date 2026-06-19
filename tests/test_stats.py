# tests/test_stats.py
import csv
from flexrouter.audit import AuditLogger
from flexrouter.dashboard.stats import compute_stats

def _seed(tmp_path):
    log = AuditLogger(str(tmp_path))
    log.log("default", "groq", "llama", 100, 50, 0.0, 200, "ok")
    log.log("default", "groq", "llama", 100, 50, 0.0, 400, "ok")
    log.log("default", "openrouter", "kimi", 100, 50, 0.0, 600, "ok")
    log.log("default", "groq", "llama", 0, 0, 0.0, 100, "rate_limited")
    return tmp_path

def test_totals(tmp_path):
    s = compute_stats(str(_seed(tmp_path)))
    assert s["totals"]["requests"] == 4

def test_per_model_latency_percentiles(tmp_path):
    s = compute_stats(str(_seed(tmp_path)))
    llama = next(m for m in s["latency"]["per_model"] if m["model"] == "groq/llama")
    assert llama["count"] == 2  # latency percentiles over successful requests only
    assert llama["p50"] >= 200

def test_error_breakdown(tmp_path):
    s = compute_stats(str(_seed(tmp_path)))
    by_type = {e["status"]: e["count"] for e in s["errors"]["by_type"]}
    assert by_type["rate_limited"] == 1
    assert by_type["ok"] == 3

def test_distribution_by_provider(tmp_path):
    s = compute_stats(str(_seed(tmp_path)))
    by_prov = {d["provider"]: d["requests"] for d in s["distribution"]["by_provider"]}
    assert by_prov["groq"] == 3
    assert by_prov["openrouter"] == 1

def test_empty_state_returns_zeros(tmp_path):
    s = compute_stats(str(tmp_path))
    assert s["totals"]["requests"] == 0
    assert s["latency"]["per_model"] == []
