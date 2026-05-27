# tests/test_audit.py
import csv, json, pytest
from pathlib import Path
from flexrouter.audit import AuditLogger

def test_creates_state_dir(tmp_path):
    state = tmp_path / ".flexrouter"
    logger = AuditLogger(state_dir=str(state))
    logger.log(tier="low", provider="groq", model="llama", prompt_tokens=10,
               completion_tokens=5, cost_usd=0.001, latency_ms=300, status="ok")
    assert state.exists()

def test_appends_csv_row(tmp_path):
    logger = AuditLogger(str(tmp_path))
    logger.log("low", "groq", "llama", 10, 5, 0.001, 300, "ok")
    logger.log("low", "groq", "llama", 20, 10, 0.002, 400, "ok")
    rows = list(csv.DictReader((tmp_path / "audit.csv").open()))
    assert len(rows) == 2
    assert rows[0]["provider"] == "groq"
    assert rows[1]["prompt_tokens"] == "20"

def test_csv_has_correct_headers(tmp_path):
    logger = AuditLogger(str(tmp_path))
    logger.log("low", "groq", "llama", 0, 0, 0, 0, "ok")
    headers = list(csv.DictReader((tmp_path / "audit.csv").open()).fieldnames)
    assert headers == ["timestamp", "tier", "provider", "model",
                       "prompt_tokens", "completion_tokens", "cost_usd", "latency_ms", "status"]

def test_health_json_updated(tmp_path):
    logger = AuditLogger(str(tmp_path))
    logger.log("low", "groq", "llama", 10, 5, 0.005, 300, "ok")
    health = json.loads((tmp_path / "health.json").read_text())
    assert health["total_cost_usd"] == pytest.approx(0.005)
    assert "groq" in health["providers"]

def test_health_json_accumulates_cost(tmp_path):
    logger = AuditLogger(str(tmp_path))
    logger.log("low", "groq", "llama", 10, 5, 0.005, 300, "ok")
    logger.log("low", "openai", "gpt-4o", 100, 50, 0.020, 1200, "ok")
    health = json.loads((tmp_path / "health.json").read_text())
    assert health["total_cost_usd"] == pytest.approx(0.025)

def test_last_50_logs(tmp_path):
    logger = AuditLogger(str(tmp_path))
    for i in range(60):
        logger.log("low", "groq", "llama", i, 0, 0, 100, "ok")
    entries = logger.recent_logs(50)
    assert len(entries) == 50
