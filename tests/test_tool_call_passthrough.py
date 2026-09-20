import json

from fastapi.testclient import TestClient

from flexrouter.app import create_app
from flexrouter.client import StreamChunk
from flexrouter.config import FlexConfig, ModelConfig, ProviderConfig


def _cfg(tmp_path):
    return FlexConfig(
        tiers={"smart": [ModelConfig(provider="alpha", model="big", score=99,
                                      rpm=60, tpm=60000)]},
        providers={"alpha": ProviderConfig(base_url="https://alpha.test/v1",
                                           api_keys=["k"])},
        state_dir=str(tmp_path / "state"),
    )


# A captured tool-call delta carrying an "index", a "type", and a
# provider-specific "cache_control" that the four-field flattening throws away.
RAW_DELTA = {
    "index": 0,
    "id": "call_abc123",
    "type": "function",
    "function": {"name": "get_weather", "arguments": ""},
    "cache_control": {"type": "ephemeral"},
}
RAW_ARGS = {"index": 0, "function": {"arguments": '{"city":'}}


def _client(tmp_path, monkeypatch):
    monkeypatch.setattr("flexrouter._router.load_config", lambda _p: _cfg(tmp_path))

    async def fake_stream(self, route, messages, **kwargs):
        yield StreamChunk(tool_call_delta=RAW_DELTA)
        yield StreamChunk(tool_call_delta=RAW_ARGS)
        yield StreamChunk(usage={"total_tokens": 9})

    monkeypatch.setattr("flexrouter.client.AsyncClient.stream_chat", fake_stream)
    from flexrouter import app as app_mod
    app_mod.state.router = None
    return TestClient(create_app(str(tmp_path / "config.yaml")))


def _chunks(client):
    with client.stream("POST", "/v1/chat/completions", json={
            "model": "smart", "stream": True,
            "messages": [{"role": "user", "content": "weather?"}]}) as r:
        body = "".join(r.iter_text())
    out = []
    for line in body.splitlines():
        if line.startswith("data: ") and line[6:].strip() != "[DONE]":
            out.append(json.loads(line[6:]))
    return out


def _tool_deltas(chunks):
    return [c["choices"][0]["delta"]["tool_calls"][0]
            for c in chunks if c.get("choices")
            and c["choices"][0]["delta"].get("tool_calls")]


def test_the_providers_own_tool_call_dict_is_forwarded_untouched(tmp_path, monkeypatch):
    deltas = _tool_deltas(_chunks(_client(tmp_path, monkeypatch)))
    assert deltas[0] == RAW_DELTA
    assert deltas[1] == RAW_ARGS


def test_nothing_the_provider_sent_is_dropped(tmp_path, monkeypatch):
    first = _tool_deltas(_chunks(_client(tmp_path, monkeypatch)))[0]
    assert first["cache_control"] == {"type": "ephemeral"}
    assert first["type"] == "function"


def test_the_argument_delta_keeps_its_index_without_an_id(tmp_path, monkeypatch):
    second = _tool_deltas(_chunks(_client(tmp_path, monkeypatch)))[1]
    assert second["index"] == 0
    assert "id" not in second


def test_mutating_the_forwarded_delta_does_not_corrupt_the_source_fixture(tmp_path, monkeypatch):
    # Capture the actual dict handed to _sse before it is serialized to JSON
    # text: a JSON round trip would hide a shallow copy, since it always
    # produces fresh objects on the way back in. Mutating the pre-serialize
    # dict is what proves whether app.py's copy of `event.raw` is deep.
    from flexrouter import app as app_mod
    captured = []
    real_sse = app_mod._sse

    def spy_sse(payload):
        captured.append(payload)
        return real_sse(payload)

    monkeypatch.setattr(app_mod, "_sse", spy_sse)

    _chunks(_client(tmp_path, monkeypatch))

    tool_call_payloads = [p for p in captured
                           if p.get("choices") and p["choices"][0]["delta"].get("tool_calls")]
    first_delta = tool_call_payloads[0]["choices"][0]["delta"]["tool_calls"][0]
    first_delta["function"]["name"] = "corrupted"
    first_delta["cache_control"]["type"] = "corrupted"
    assert RAW_DELTA["function"]["name"] == "get_weather"
    assert RAW_DELTA["cache_control"] == {"type": "ephemeral"}
