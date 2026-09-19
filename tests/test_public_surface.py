import importlib

import pytest


def test_start_server_is_gone():
    import flexrouter
    assert "start_server" not in flexrouter.__all__
    assert not hasattr(flexrouter, "start_server")


def test_the_old_server_modules_are_gone():
    for name in ("flexrouter.server", "flexrouter.dashboard.server"):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(name)


def test_the_onboarding_wizard_is_gone():
    """`flexrouter init` wrote a settings file full of plaintext keys into the
    current directory — both faults the shared home exists to kill (ADR 0007)."""
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("flexrouter.onboard")


def test_the_cli_has_no_init_command():
    from flexrouter.cli import cli
    assert "init" not in cli.commands
