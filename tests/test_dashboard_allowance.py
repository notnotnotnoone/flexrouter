"""The Allowance page: stacked free-tier headroom, per-provider meters."""
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


def test_the_stack_adds_up_daily_headroom(client):
    _use(3)
    stack = facts.allowance_stack(app_module.get_router())
    assert stack["left"] == 7 and stack["total"] == 10
    assert stack["providers"] == 1


def test_the_page_draws_meters_and_the_stack(client):
    _use(3)
    body = client.get("/allowance").text
    assert 'class="stacked' in body
    assert 'class="meter"' in body
    assert "your limit" in body          # shown uppercase by the stylesheet
    assert "7" in body and "free requests left today" in body


def test_countdowns_carry_a_timestamp_for_the_browser_to_tick(client):
    _use(4)
    assert "data-countdown=" in client.get("/allowance").text


def test_no_caps_set_says_how_to_get_a_stack(config_file):
    with TestClient(create_app(str(config_file))) as c:
        body = c.get("/allowance").text
    assert "No daily caps set" in body
