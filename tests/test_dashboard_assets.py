import re

import pytest
from fastapi.testclient import TestClient

from flexrouter.app import create_app
from flexrouter.dashboard import assets


@pytest.fixture
def client(config_file):
    with TestClient(create_app(str(config_file))) as c:
        yield c


def test_asset_url_is_cache_busted_by_content():
    url = assets.asset_url("vendor/htmx.min.js")
    assert re.fullmatch(r"/static/vendor/htmx\.min\.js\?v=[0-9a-f]{10}", url)


def test_asset_url_refuses_a_missing_file():
    with pytest.raises(FileNotFoundError):
        assets.asset_url("vendor/nope.js")


@pytest.mark.parametrize("rel", [
    "vendor/htmx.min.js", "vendor/idiomorph-ext.min.js", "vendor/motion.js",
    "fonts/Geist.woff2", "fonts/GeistMono.woff2",
])
def test_vendored_files_are_served(client, rel):
    r = client.get(f"/static/{rel}")
    assert r.status_code == 200
    assert len(r.content) > 1000


def test_every_vendored_file_ships_with_its_licence():
    names = {p.stem for p in (assets.STATIC_DIR / "vendor" / "LICENSES").iterdir()}
    assert {"htmx", "idiomorph", "motion", "geist"} <= names


def test_the_old_react_build_is_gone():
    assert not (assets.STATIC_DIR / "index.html").exists()
    assert not (assets.STATIC_DIR / "assets").exists()
