import pytest

from flexrouter import service_keys


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("FLEXROUTER_HOME", str(tmp_path))
    monkeypatch.delenv("AA_API_KEY", raising=False)
    return tmp_path


def test_resolve_is_none_when_nothing_is_set():
    assert service_keys.resolve("aa") is None


def test_resolve_falls_back_to_the_environment_variable(monkeypatch):
    monkeypatch.setenv("AA_API_KEY", "env-value")
    assert service_keys.resolve("aa") == "env-value"


def test_a_dashboard_typed_key_wins_over_the_environment_variable(monkeypatch):
    monkeypatch.setenv("AA_API_KEY", "env-value")
    service_keys.set_key("aa", "dashboard-value")
    assert service_keys.resolve("aa") == "dashboard-value"


def test_set_key_replaces_rather_than_accumulates():
    service_keys.set_key("aa", "first")
    service_keys.set_key("aa", "second")
    from flexrouter.keys import load_keys
    assert len(load_keys()["aa"]) == 1
    assert service_keys.resolve("aa") == "second"


def test_clear_key_removes_it():
    service_keys.set_key("aa", "first")
    assert service_keys.clear_key("aa") is True
    assert service_keys.resolve("aa") is None


def test_clear_key_on_nothing_returns_false():
    assert service_keys.clear_key("aa") is False


def test_current_record_is_none_when_unset():
    assert service_keys.current_record("aa") is None


def test_current_record_reflects_the_saved_secret():
    service_keys.set_key("aa", "abcd1234")
    assert service_keys.current_record("aa").secret == "abcd1234"
