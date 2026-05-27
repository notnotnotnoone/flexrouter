import time
import pytest
from flexrouter.window import SlidingWindow

def test_empty_window_is_available():
    w = SlidingWindow(window_seconds=60)
    assert w.available(rpm_limit=10, tpm_limit=10000) is True

def test_records_increment_rpm():
    w = SlidingWindow(window_seconds=60)
    w.record(tokens=100)
    w.record(tokens=200)
    assert w.current_rpm() == 2
    assert w.current_tpm() == 300

def test_rpm_limit_exhausted():
    w = SlidingWindow(window_seconds=60)
    for _ in range(5):
        w.record(tokens=0)
    assert w.available(rpm_limit=5, tpm_limit=999999) is False

def test_tpm_limit_exhausted():
    w = SlidingWindow(window_seconds=60)
    w.record(tokens=5000)
    assert w.available(rpm_limit=999, tpm_limit=4999) is False

def test_old_records_expire():
    import collections
    now = time.monotonic()
    w = SlidingWindow(window_seconds=60)
    w._requests = collections.deque([now - 61])
    w._tokens = collections.deque([(now - 61, 500)])
    assert w.current_rpm() == 0
    assert w.current_tpm() == 0

def test_seconds_until_available_zero_when_free():
    w = SlidingWindow(window_seconds=60)
    assert w.seconds_until_available(rpm_limit=10, tpm_limit=10000) == 0.0

def test_seconds_until_available_positive_when_full():
    import collections
    now = time.monotonic()
    w = SlidingWindow(window_seconds=60)
    w._requests = collections.deque([now - 30])
    w._tokens = collections.deque([(now - 30, 0)])
    secs = w.seconds_until_available(rpm_limit=1, tpm_limit=9999)
    assert 29 <= secs <= 31
