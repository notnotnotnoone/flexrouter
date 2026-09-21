import pytest

from flexrouter.key_state import KeyStateStore
from flexrouter.keys import KeyRecord
from flexrouter.scheduler import RoundRobinCounters, pick_key


def _keys(*ids, weight=1, allow=("*",)):
    return [KeyRecord(id=i, secret=f"secret-{i}", weight=weight, allow_models=list(allow))
            for i in ids]


def test_returns_none_when_no_candidates():
    store = KeyStateStore.__new__(KeyStateStore)  # not exercised, unused here
    assert pick_key([], "openrouter", "openrouter/m", "most_headroom",
                    KeyStateStoreStub(), cap=4) is None


class KeyStateStoreStub:
    """A minimal stand-in that satisfies pick_key's interface without touching disk."""
    def __init__(self):
        self._active = {}
        self._available = {}

    def is_available(self, provider, key_id, now=None):
        return self._available.get(key_id, True)

    def get(self, provider, key_id):
        from flexrouter.key_state import KeyState
        s = KeyState()
        s.active_requests = self._active.get(key_id, 0)
        return s


def test_unavailable_keys_are_filtered_out():
    store = KeyStateStoreStub()
    store._available["k1"] = False
    chosen = pick_key(_keys("k1", "k2"), "openrouter", "openrouter/m", "most_headroom", store, cap=4)
    assert chosen.id == "k2"


def test_saturated_keys_are_filtered_out():
    store = KeyStateStoreStub()
    store._active["k1"] = 10
    chosen = pick_key(_keys("k1", "k2"), "openrouter", "openrouter/m", "most_headroom", store, cap=4)
    assert chosen.id == "k2"


def test_allow_models_filters_before_scheduling():
    store = KeyStateStoreStub()
    keys = _keys("k1", allow=["other/*"]) + _keys("k2", allow=["*"])
    chosen = pick_key(keys, "openrouter", "openrouter/m", "most_headroom", store, cap=4)
    assert chosen.id == "k2"


def test_nothing_survives_filtering_returns_none():
    store = KeyStateStoreStub()
    store._available["k1"] = False
    chosen = pick_key(_keys("k1"), "openrouter", "openrouter/m", "most_headroom", store, cap=4)
    assert chosen is None


def test_most_headroom_prefers_fewer_active_requests():
    store = KeyStateStoreStub()
    store._active["k1"] = 3
    store._active["k2"] = 0
    chosen = pick_key(_keys("k1", "k2"), "openrouter", "openrouter/m", "most_headroom", store, cap=4)
    assert chosen.id == "k2"


def test_round_robin_cycles_and_requires_counters():
    store = KeyStateStoreStub()
    with pytest.raises(ValueError):
        pick_key(_keys("k1", "k2"), "openrouter", "openrouter/m", "round_robin", store, cap=4)

    counters = RoundRobinCounters()
    seen = [pick_key(_keys("k1", "k2"), "openrouter", "openrouter/m", "round_robin",
                     store, cap=4, counters=counters).id for _ in range(4)]
    assert seen == ["k1", "k2", "k1", "k2"]


def test_fastest_prefers_lower_latency(tmp_path):
    store = KeyStateStoreStub()
    from flexrouter.key_state import KeyState
    fast, slow = KeyState(), KeyState()
    fast.ema_latency_ms, slow.ema_latency_ms = 100.0, 900.0

    class Store2(KeyStateStoreStub):
        def get(self, provider, key_id):
            return fast if key_id == "k1" else slow

    chosen = pick_key(_keys("k1", "k2"), "openrouter", "openrouter/m", "fastest", Store2(), cap=4)
    assert chosen.id == "k1"


def test_fastest_treats_unmeasured_keys_as_best():
    store = KeyStateStoreStub()

    class Store2(KeyStateStoreStub):
        def get(self, provider, key_id):
            from flexrouter.key_state import KeyState
            s = KeyState()
            if key_id == "k2":
                s.ema_latency_ms = 50.0
            return s  # k1 has no measurement yet

    chosen = pick_key(_keys("k1", "k2"), "openrouter", "openrouter/m", "fastest", Store2(), cap=4)
    assert chosen.id == "k1"


def test_weighted_never_picks_a_zero_weight_key_when_a_positive_one_exists():
    store = KeyStateStoreStub()
    keys = _keys("k1", weight=0) + _keys("k2", weight=5)
    for _ in range(20):
        chosen = pick_key(keys, "openrouter", "openrouter/m", "weighted", store, cap=4)
        assert chosen.id == "k2"


def test_unknown_strategy_falls_back_to_most_headroom():
    store = KeyStateStoreStub()
    store._active["k1"] = 3
    store._active["k2"] = 0
    chosen = pick_key(_keys("k1", "k2"), "openrouter", "openrouter/m", "not_a_real_strategy", store, cap=4)
    assert chosen.id == "k2"
