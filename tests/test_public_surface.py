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
