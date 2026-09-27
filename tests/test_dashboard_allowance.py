"""The Allowance page: per-provider groups, one row per real limit (§19)."""
import time

import pytest
import yaml
from fastapi.testclient import TestClient

import flexrouter.app as app_module
from flexrouter.app import create_app
from flexrouter.dashboard import facts


@pytest.fixture
def capped_config(tmp_path, minimal_config):
    cfg = minimal_config
    cfg["settings"]["state_dir"] = str(tmp_path / ".flexrouter")
    for mc in next(iter(cfg["tiers"].values())):
        mc["quotas"] = {"rpd": 10, "rph": 4}
    p = tmp_path / "flexrouter.yaml"
    p.write_text(yaml.dump(cfg))
    import os
    os.environ["GROQ_API_KEY"] = "test-key"
    yield p
    os.environ.pop("GROQ_API_KEY", None)


@pytest.fixture
def client(capped_config):
    with TestClient(create_app(str(capped_config))) as c:
        yield c


def _use(n):
    router = app_module.get_router()
    mc = next(iter(router._cfg.tiers.values()))[0]
    for _ in range(n):
        router._quota_tracker.record(mc.provider, mc.model)
    return mc


def test_limits_count_what_was_used(client):
    mc = _use(3)
    groups = facts.allowance_groups(app_module.get_router())
    [g] = [g for g in groups if g["provider"] == mc.provider]
    limits = {lim["window"]: lim for m in g["models"] for lim in m["limits"]}
    assert limits["day"]["used"] == 3 and limits["day"]["limit"] == 10
    assert limits["hour"]["used"] == 3 and limits["hour"]["limit"] == 4


def test_a_full_limit_says_when_it_frees_up(client):
    _use(4)
    groups = facts.allowance_groups(app_module.get_router())
    hour = [lim for g in groups for m in g["models"] for lim in m["limits"]
            if lim["window"] == "hour"][0]
    assert hour["used"] == 4
    assert 0 < hour["frees_at"] - time.time() <= 3600


def test_there_is_no_grand_total(client):
    """§19: no single inflated number across providers."""
    _use(3)
    body = client.get("/allowance").text
    assert 'class="stacked' not in body and "free requests left today" not in body
    assert "allow-group" in body and 'class="meter"' in body


def test_each_group_says_how_it_resets_and_what_it_counts(client):
    _use(1)
    body = client.get("/allowance").text
    assert "Rolling: each request frees up a day after it was sent." in body
    assert "Counts only what flexrouter sent." in body
    assert "console.groq.com/settings/limits" in body


def test_failed_attempts_are_counted_and_shown(client):
    router = app_module.get_router()
    mc = _use(1)
    router._quota_tracker.record(mc.provider, mc.model, 0, failed=True)
    groups = facts.allowance_groups(router)
    day = [lim for g in groups for m in g["models"] for lim in m["limits"]
           if lim["window"] == "day"][0]
    assert day["used"] == 2 and day["failed"] == 1
    assert "1 failed" in client.get("/allowance").text


def test_countdowns_carry_a_timestamp_for_the_browser_to_tick(client):
    _use(4)
    assert "data-countdown=" in client.get("/allowance").text


def test_no_caps_set_says_how_to_get_numbers(config_file):
    with TestClient(create_app(str(config_file))) as c:
        body = c.get("/allowance").text
    assert "No limits known for these models yet" in body
