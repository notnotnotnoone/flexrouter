"""The library, over a real socket, against the real service."""
import threading
import time

import pytest
import uvicorn

from flexrouter import FlexRouter, ServiceNotRunning
from flexrouter.app import create_app
from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig

PORT = 4899


def _cfg(tmp_path):
    return FlexConfig(
        tiers={"smart": [ModelConfig(provider="alpha", model="big", score=99,
                                     rpm=60, tpm=60000)]},
        providers={"alpha": ProviderConfig(base_url="https://alpha.test/v1",
                                           api_keys=["k"])},
        state_dir=str(tmp_path / "state"),
        port=PORT,
    )


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: _cfg(tmp_path))

    async def fake_chat(self, route, messages, **kwargs):
        return {"choices": [{"message": {"role": "assistant", "content": "pong"},
                             "finish_reason": "stop"}],
                "usage": {"total_tokens": 2}}

    monkeypatch.setattr("flexrouter.client.AsyncClient.chat", fake_chat)
    from flexrouter import app as app_mod
    app_mod.state.router = None
    server = uvicorn.Server(uvicorn.Config(
        create_app(str(tmp_path / "config.yaml")), host="127.0.0.1", port=PORT,
        log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    assert server.started, "the service did not come up"
    yield f"http://127.0.0.1:{PORT}"
    server.should_exit = True
    thread.join(timeout=5)
    app_mod.state.router = None


def test_the_library_reaches_the_service(service):
    router = FlexRouter(base_url=service)
    out = router.generate([{"role": "user", "content": "ping"}], "smart")
    assert out["choices"][0]["message"]["content"] == "pong"
    router.close()


def test_nothing_listening_says_how_to_start_it():
    router = FlexRouter(base_url="http://127.0.0.1:4898")
    with pytest.raises(ServiceNotRunning) as exc:
        router.generate([{"role": "user", "content": "ping"}], "smart")
    assert "flexrouter serve" in str(exc.value)
    router.close()
