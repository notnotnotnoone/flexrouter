# tests/test_sampler.py
import time
from flexrouter.sampler import PassiveSampler

def test_sample_once_pipes_snapshot_to_sink():
    sunk = []
    s = PassiveSampler(lambda: {"models": {"a": 1}}, sunk.append, interval_seconds=60)
    s.sample_once()
    assert sunk == [{"models": {"a": 1}}]

def test_start_samples_then_stop_halts():
    sunk = []
    s = PassiveSampler(lambda: {"n": len(sunk)}, sunk.append, interval_seconds=1)
    # interval of 0.05s via override for a fast test
    s._interval = 0.05
    s.start()
    time.sleep(0.17)
    s.stop()
    count_at_stop = len(sunk)
    assert count_at_stop >= 2
    time.sleep(0.12)
    assert len(sunk) == count_at_stop  # no more samples after stop

def test_sink_exception_does_not_kill_thread():
    calls = []
    def bad_sink(_):
        calls.append(1)
        raise RuntimeError("disk full")
    s = PassiveSampler(lambda: {}, bad_sink, interval_seconds=1)
    s._interval = 0.05
    s.start()
    time.sleep(0.17)
    s.stop()
    assert len(calls) >= 2  # kept going despite exceptions
