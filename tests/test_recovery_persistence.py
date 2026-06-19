# tests/test_recovery_persistence.py
from flexrouter.recovery import PenaltyBox

def test_persists_across_instances(tmp_path):
    pb = PenaltyBox(30, 1800, state_dir=str(tmp_path))
    pb.penalize("groq", "llama")
    # New instance simulates a process restart
    pb2 = PenaltyBox(30, 1800, state_dir=str(tmp_path))
    assert pb2.is_penalized("groq", "llama") is True

def test_emits_penalized_event():
    events = []
    pb = PenaltyBox(30, 1800, on_event=lambda p, m, t, s: events.append((p, m, t, s)))
    pb.penalize("groq", "llama")
    assert events[0][0:3] == ("groq", "llama", "penalized")
    assert events[0][3] > 0

def test_emits_recovered_event_on_clear():
    events = []
    pb = PenaltyBox(30, 1800, on_event=lambda p, m, t, s: events.append((p, m, t)))
    pb.penalize("groq", "llama")
    pb.clear("groq", "llama")
    assert ("groq", "llama", "recovered") in events

def test_expired_penalty_not_loaded(tmp_path):
    pb = PenaltyBox(1, 10, state_dir=str(tmp_path))
    pb.penalize_short("groq", "llama", seconds=0)  # already expired
    pb2 = PenaltyBox(1, 10, state_dir=str(tmp_path))
    assert pb2.is_penalized("groq", "llama") is False
