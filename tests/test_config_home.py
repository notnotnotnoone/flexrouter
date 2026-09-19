import warnings

import pytest
import yaml

from flexrouter import home
from flexrouter.config import FlexConfig, load_config, resolve_keys
from flexrouter.exceptions import ConfigError, ConfigFieldError
from flexrouter.keys import KeyRecord, add_key
from flexrouter.overrides import set_override

SETTINGS = {
    "settings": {"port": 4891},
    "providers": {
        "openrouter": {"base_url": "https://openrouter.ai/api/v1"},
    },
    "buckets": {
        "smart": [{"provider": "openrouter", "model": "deepseek-chat",
                   "score": 99, "rpm": 20, "tpm": 10000}],
    },
}


@pytest.fixture
def written_home(tmp_path, monkeypatch):
    monkeypatch.setenv("FLEXROUTER_HOME", str(tmp_path))
    home.ensure_home()
    home.config_path().write_text(yaml.dump(SETTINGS), encoding="utf-8")
    return tmp_path


def test_discover_config_is_gone():
    import flexrouter.config as config_module
    assert not hasattr(config_module, "discover_config")


def test_load_config_defaults_to_the_home(written_home):
    cfg = load_config()
    assert cfg.port == 4891
    assert "smart" in cfg.tiers


def test_load_config_accepts_the_buckets_spelling(written_home):
    assert [m.model for m in load_config().tiers["smart"]] == ["deepseek-chat"]


def test_load_config_still_accepts_the_tiers_spelling(written_home):
    home.config_path().write_text(
        yaml.dump({"settings": SETTINGS["settings"],
                   "providers": SETTINGS["providers"],
                   "tiers": SETTINGS["buckets"]}),
        encoding="utf-8")
    assert "smart" in load_config().tiers


def test_load_config_creates_a_starter_home_when_there_is_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("FLEXROUTER_HOME", str(tmp_path / "fresh"))
    cfg = load_config()
    assert cfg.port == 4891
    assert cfg.tiers == {"smart": [], "fast": [], "long": []}


def test_load_config_applies_an_override(written_home):
    set_override("models", "openrouter/deepseek-chat", "score", 42)
    assert load_config().tiers["smart"][0].score == 42


def test_load_config_never_rewrites_the_settings_file(written_home):
    before = home.config_path().read_bytes()
    set_override("settings", "port", None, 7000)
    load_config()
    assert home.config_path().read_bytes() == before


def test_state_dir_defaults_into_the_home(written_home):
    assert load_config().state_dir == str(home.state_dir())


def test_dashboard_port_is_still_accepted_as_an_alias(written_home):
    home.config_path().write_text(
        yaml.dump({"settings": {"dashboard_port": 7352},
                   "providers": SETTINGS["providers"],
                   "buckets": SETTINGS["buckets"]}), encoding="utf-8")
    cfg = load_config()
    assert cfg.port == 7352
    assert cfg.dashboard_port == 7352


def test_flexconfig_port_alias_neither_passed():
    cfg = FlexConfig(tiers={}, providers={})
    assert cfg.port == 4891
    assert cfg.dashboard_port == 4891


def test_flexconfig_port_alias_only_port_passed():
    cfg = FlexConfig(tiers={}, providers={}, port=9000)
    assert cfg.port == 9000
    assert cfg.dashboard_port == 9000


def test_flexconfig_port_alias_only_dashboard_port_passed():
    cfg = FlexConfig(tiers={}, providers={}, dashboard_port=9000)
    assert cfg.port == 9000
    assert cfg.dashboard_port == 9000


def test_flexconfig_port_alias_both_passed_and_equal():
    cfg = FlexConfig(tiers={}, providers={}, port=9000, dashboard_port=9000)
    assert cfg.port == 9000
    assert cfg.dashboard_port == 9000


def test_stored_key_beats_an_env_var(written_home, monkeypatch):
    add_key("openrouter", "from-vault")
    monkeypatch.setenv("OR_KEY", "from-env")
    cfg = load_config()
    assert cfg.providers["openrouter"].api_keys == ["from-vault"]
    assert cfg.providers["openrouter"].keys[0].source == "keys"


def test_env_var_is_used_when_the_vault_is_empty(written_home, monkeypatch):
    home.config_path().write_text(yaml.dump({
        "settings": SETTINGS["settings"],
        "providers": {"openrouter": {"base_url": "https://x/v1",
                                     "api_key_env": "OR_KEY"}},
        "buckets": SETTINGS["buckets"],
    }), encoding="utf-8")
    monkeypatch.setenv("OR_KEY", "from-env")
    cfg = load_config()
    assert cfg.providers["openrouter"].api_keys == ["from-env"]
    assert cfg.providers["openrouter"].keys[0].source == "env"


def test_disabled_stored_keys_are_not_offered(written_home):
    from flexrouter.keys import save_keys
    save_keys({"openrouter": [
        KeyRecord(id="a", secret="off", enabled=False),
        KeyRecord(id="b", secret="on"),
    ]})
    assert load_config().providers["openrouter"].api_keys == ["on"]


def test_an_inline_key_still_works_but_warns_loudly(written_home):
    home.config_path().write_text(yaml.dump({
        "settings": SETTINGS["settings"],
        "providers": {"openrouter": {"base_url": "https://x/v1",
                                     "api_key": "sk-inline-secret"}},
        "buckets": SETTINGS["buckets"],
    }), encoding="utf-8")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        cfg = load_config()
    assert cfg.providers["openrouter"].api_keys == ["sk-inline-secret"]
    assert cfg.providers["openrouter"].keys[0].source == "inline"
    message = str(caught[0].message)
    assert "config.yaml" in message
    assert "line" in message
    assert "sk-inline-secret" not in message


def test_a_provider_with_no_credentials_anywhere_loads_with_none(written_home):
    cfg = load_config()
    assert cfg.providers["openrouter"].api_keys == []


def test_resolve_keys_prefers_the_vault():
    vault = {"groq": [KeyRecord(id="groq-1", secret="v")]}
    got = resolve_keys("groq", {"api_key_env": "NOPE"}, vault, None)
    assert [r.secret for r in got] == ["v"]


def test_a_malformed_secret_never_reaches_the_configerror_message(written_home):
    # PyYAML's own parser error text embeds a literal snippet of the
    # offending source line. If a syntax error lands next to an inline
    # `api_key`, that snippet can contain the plaintext secret. load_config
    # must never let str(e) from the YAML parser into the ConfigError.
    home.config_path().write_text(
        "providers:\n"
        "  openrouter:\n"
        "    api_key: sk-or-v1: SUPERSECRET1234\n"
        "    base_url: https://openrouter.ai/api/v1\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError) as exc_info:
        load_config()
    message = str(exc_info.value)
    assert "SUPERSECRET1234" not in message
    assert "sk-or-v1" not in message
    # Still genuinely useful: names the file and, when PyYAML supplies it,
    # where the problem is.
    assert "config.yaml" in message
    assert "line" in message.lower()


def test_provider_missing_base_url_reports_the_field_not_unreadable(written_home):
    home.config_path().write_text(
        yaml.dump({
            "settings": SETTINGS["settings"],
            "providers": {"openrouter": {}},
            "buckets": SETTINGS["buckets"],
        }),
        encoding="utf-8",
    )
    with pytest.raises(ConfigFieldError) as exc_info:
        load_config()
    message = str(exc_info.value)
    assert "openrouter" in message
    assert "base_url" in message


def test_bucket_entry_missing_score_reports_the_field_not_unreadable(written_home):
    home.config_path().write_text(
        yaml.dump({
            "settings": SETTINGS["settings"],
            "providers": SETTINGS["providers"],
            "buckets": {"smart": [{"provider": "openrouter", "model": "m",
                                    "rpm": 20, "tpm": 10000}]},
        }),
        encoding="utf-8",
    )
    with pytest.raises(ConfigFieldError) as exc_info:
        load_config()
    message = str(exc_info.value)
    assert "smart" in message
    assert "score" in message
