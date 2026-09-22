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
    with (tmp_path / "audit.csv").open() as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2
    assert rows[0]["provider"] == "groq"
    assert rows[1]["prompt_tokens"] == "20"

def test_csv_has_correct_headers(tmp_path):
    logger = AuditLogger(str(tmp_path))
    logger.log("low", "groq", "llama", 0, 0, 0, 0, "ok")
    with (tmp_path / "audit.csv").open() as f:
        headers = list(csv.DictReader(f).fieldnames)
    assert headers == ["timestamp", "tier", "provider", "model",
                       "prompt_tokens", "completion_tokens", "cost_usd",
                       "latency_ms", "status", "request_id"]


def test_request_id_ties_several_attempts_to_one_request(tmp_path):
    # This is what makes a failover countable: two rows, one request.
    logger = AuditLogger(str(tmp_path))
    logger.log("low", "groq", "llama", 0, 0, 0, 90, "rate_limited",
               request_id="req_abc")
    logger.log("low", "cerebras", "glm", 10, 5, 0, 300, "ok",
               request_id="req_abc")
    with (tmp_path / "audit.csv").open() as f:
        rows = list(csv.DictReader(f))
    assert {r["request_id"] for r in rows} == {"req_abc"}


def test_a_caller_that_forgets_the_request_id_still_logs(tmp_path):
    # Losing a statistic is acceptable; raising mid-request is not.
    logger = AuditLogger(str(tmp_path))
    logger.log("low", "groq", "llama", 1, 1, 0, 10, "ok")
    with (tmp_path / "audit.csv").open() as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["request_id"] == ""


def test_an_older_audit_file_is_migrated_not_corrupted(tmp_path):
    """A log written before `request_id` existed keeps its rows and gains
    the column, instead of new rows landing under a short header."""
    legacy = ["timestamp", "tier", "provider", "model", "prompt_tokens",
              "completion_tokens", "cost_usd", "latency_ms", "status"]
    with (tmp_path / "audit.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=legacy)
        w.writeheader()
        w.writerow({"timestamp": "2026-01-01T00:00:00+00:00", "tier": "low",
                    "provider": "groq", "model": "llama", "prompt_tokens": 1,
                    "completion_tokens": 1, "cost_usd": 0.0, "latency_ms": 5,
                    "status": "ok"})

    logger = AuditLogger(str(tmp_path))
    logger.log("low", "groq", "llama", 2, 2, 0, 6, "ok", request_id="req_new")

    with (tmp_path / "audit.csv").open() as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames[-1] == "request_id"
        rows = list(reader)
    assert len(rows) == 2
    assert rows[0]["provider"] == "groq"        # the old row survived
    assert rows[0]["request_id"] == ""          # and tells the truth about itself
    assert rows[1]["request_id"] == "req_new"
    assert None not in rows[1]                  # nothing fell off the end


def test_migration_leaves_an_unfamiliar_file_alone(tmp_path):
    (tmp_path / "audit.csv").write_text("not,our,columns\n1,2,3\n", encoding="utf-8")
    AuditLogger(str(tmp_path))
    assert (tmp_path / "audit.csv").read_text(encoding="utf-8").startswith("not,our,columns")

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
