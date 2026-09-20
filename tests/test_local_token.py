from fastapi.testclient import TestClient

from flexrouter import home
from flexrouter.app import create_app
from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig


def _cfg(tmp_path, token=None):
    return FlexConfig(
        tiers={"smart": [ModelConfig(provider="alpha", model="big", score=99,
                                     rpm=60, tpm=60000)]},
        providers={"alpha": ProviderConfig(base_url="https://alpha.test/v1",
                                           api_keys=["k"])},
        state_dir=str(tmp_path / "state"),
        auth_token=token,
    )


def _client(tmp_path, monkeypatch, token):
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: _cfg(tmp_path, token))
    from flexrouter import app as app_mod
    app_mod.state.router = None
    return TestClient(create_app(str(tmp_path / "config.yaml")))


SECRET = "flx-AAAABBBBCCCCDDDDEEEEFFFF1234"


def test_no_token_configured_means_no_guard(tmp_path, monkeypatch):
    assert _client(tmp_path, monkeypatch, None).get("/v1/models").status_code == 200


def test_a_configured_token_is_required(tmp_path, monkeypatch):
    r = _client(tmp_path, monkeypatch, SECRET).get("/v1/models")
    assert r.status_code == 401
    err = r.json()["error"]
    assert err["type"] == "invalid_request_error"
    assert err["code"] == "invalid_api_key"


def test_the_right_token_gets_in(tmp_path, monkeypatch):
    r = _client(tmp_path, monkeypatch, SECRET).get(
        "/v1/models", headers={"Authorization": f"Bearer {SECRET}"})
    assert r.status_code == 200


def test_the_wrong_token_does_not(tmp_path, monkeypatch):
    r = _client(tmp_path, monkeypatch, SECRET).get(
        "/v1/models", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_the_rejection_never_repeats_the_token_back(tmp_path, monkeypatch):
    nearly = SECRET[:-4] + "xxxx"
    r = _client(tmp_path, monkeypatch, SECRET).get(
        "/v1/models", headers={"Authorization": f"Bearer {nearly}"})
    assert SECRET not in r.text
    assert SECRET[:-4] not in r.text


def test_chat_is_guarded_too(tmp_path, monkeypatch):
    r = _client(tmp_path, monkeypatch, SECRET).post("/v1/chat/completions", json={
        "model": "smart", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 401


def test_the_dashboard_stays_open_on_loopback(tmp_path, monkeypatch):
    # A browser pointed at the dashboard cannot carry a bearer header, and the
    # whole surface is bound to 127.0.0.1. ADR 0009.
    assert _client(tmp_path, monkeypatch, SECRET).get("/api/status").status_code == 200


def test_the_rejection_message_is_readable(tmp_path, monkeypatch):
    # openai_error scrubs every message by default, which is right for a
    # provider's own words but wrong for a fixed string this codebase wrote:
    # scrub()'s cue-word window used to eat "Authorization", "Bearer" and
    # "auth_token" out of this exact sentence, since it treats them as cues
    # a credential might follow. There is no key in a constant string, so
    # nothing is bought by scrubbing it - only the instructions are lost.
    r = _client(tmp_path, monkeypatch, SECRET).get("/v1/models")
    message = r.json()["error"]["message"]
    assert "Authorization" in message
    assert "Bearer" in message
    assert "auth_token" in message
    assert message == (
        "This flexrouter needs a key. Send it as an Authorization "
        "header: Bearer <your key>. It is the auth_token line in "
        "your settings.")


# --- Fix round 1: CORS preflight must not be guarded ---
#
# `@app.middleware("http")` wraps whatever middleware was already registered,
# so it matters which one is added first. A CORS preflight (an OPTIONS
# carrying Access-Control-Request-Method) can never carry a bearer header, so
# if the guard sees it before CORSMiddleware does, it 401s every browser
# client of /v1 the moment a key is set - and even a real request's 401
# response loses its CORS headers if the guard's response never passes back
# through CORSMiddleware on the way out.

def test_a_cors_preflight_is_not_guarded_and_carries_the_allow_origin_header(
        tmp_path, monkeypatch):
    r = _client(tmp_path, monkeypatch, SECRET).options(
        "/v1/chat/completions",
        headers={"Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST"})
    assert r.status_code != 401
    assert r.headers.get("access-control-allow-origin")


def test_a_real_cross_origin_request_with_the_wrong_key_still_gets_401(
        tmp_path, monkeypatch):
    r = _client(tmp_path, monkeypatch, SECRET).get(
        "/v1/models",
        headers={"Origin": "http://localhost:3000",
                "Authorization": "Bearer nope"})
    assert r.status_code == 401
    assert r.headers.get("access-control-allow-origin")


# --- Fix round 1: a non-string auth_token must not 500 ---
#
# `auth_token` is hand-written in the settings file, and YAML parses a bare
# scalar like `12345678` as an int, or a quoted value with accented
# characters as a non-ASCII str. Neither used to survive: the int broke
# `hmac.compare_digest`'s type check, and the non-ASCII string broke it too
# (it only accepts ASCII str, or bytes). Both must be usable as a real key,
# both a right and a wrong guess, without ever surfacing a bare 500.

def _real_client(tmp_path, monkeypatch, settings_line):
    monkeypatch.setenv("FLEXROUTER_HOME", str(tmp_path / "home"))
    home.ensure_home()
    home.config_path().write_text(f"""\
settings:
  {settings_line}

providers:
  alpha:
    base_url: https://alpha.test/v1
    api_keys:
      - key: k

buckets:
  smart:
    - provider: alpha
      model: big
      score: 99
      rpm: 60
      tpm: 6000
""", encoding="utf-8")
    from flexrouter import app as app_mod
    app_mod.state.router = None
    return TestClient(create_app())


def test_a_numeric_auth_token_is_accepted_and_a_wrong_one_still_gets_401(
        tmp_path, monkeypatch):
    client = _real_client(tmp_path, monkeypatch, "auth_token: 12345678")
    ok = client.get("/v1/models", headers={"Authorization": "Bearer 12345678"})
    assert ok.status_code == 200
    wrong = client.get("/v1/models", headers={"Authorization": "Bearer nope"})
    assert wrong.status_code == 401
    assert wrong.json()["error"]["code"] == "invalid_api_key"


def _request_with_raw_header(name: bytes, value: bytes):
    """A Starlette Request carrying an exact header byte string.

    `TestClient` cannot be used for this: httpx decodes the header bytes we
    hand it into a `str` internally, then Starlette's own TestClient
    re-encodes that `str` with plain `.encode()` (utf-8) when building the
    ASGI scope (see `starlette/testclient.py`, `headers += [(key.lower()
    .encode(), value.encode()) ...]`) - a double round trip that mangles any
    byte above 127 before it ever reaches the app. A real ASGI server
    decodes the one raw byte string straight off the wire, so this builds
    the scope directly to get the same effect without that harness quirk.
    """
    from starlette.requests import Request as StarletteRequest
    scope = {"type": "http", "method": "GET", "path": "/v1/models",
             "headers": [(name, value)]}
    return StarletteRequest(scope)


def test_a_non_ascii_auth_token_is_accepted_and_a_wrong_one_still_gets_401(
        tmp_path, monkeypatch):
    # é is within Latin-1, which is what HTTP header values are actually
    # encoded as on the wire (and what a browser's fetch/XHR Headers API
    # restricts itself to) - so this is what a real client sends.
    from flexrouter.app import _check_token

    token = "flx-héllo-1234"
    monkeypatch.setattr("flexrouter._router.load_config",
                        lambda _p: _cfg(tmp_path, token))
    from flexrouter import app as app_mod
    app_mod.state.router = None
    app_mod.state.config_path = str(tmp_path / "config.yaml")

    ok_req = _request_with_raw_header(
        b"authorization", f"Bearer {token}".encode("latin-1"))
    assert _check_token(ok_req) is None

    wrong_req = _request_with_raw_header(b"authorization", b"Bearer nope")
    denied = _check_token(wrong_req)
    assert denied is not None
    assert denied.status_code == 401
    import json
    assert json.loads(denied.body)["error"]["code"] == "invalid_api_key"
