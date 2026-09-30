"""Two websocket paths the streaming suites don't touch yet:

* `/v1/realtime`, the OpenAI-Realtime-shaped sibling of `/v1/asr/ws` -- same
  refuse/relay shape, on its own route so it keeps its own error frame.
* `/{slot}/{path:path}` websocket catch-all, the generic relay any other
  model's socket endpoint falls through to.

Also covers what `relay_ws` does when the upstream is enabled but genuinely
unreachable (as opposed to the "not deployed" refuse-before-dialling path
those other suites exercise), forwarded auth headers, and bytes flowing
upstream-to-client (the streaming suites so far only send bytes the other way).
"""
from __future__ import annotations

import asyncio

import pytest
import websockets
from conftest import free_port, serve
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

upstream = FastAPI()


@upstream.get("/health")
def _health():
    return {"status": "ok"}


@upstream.websocket("/v1/realtime")
async def _realtime(ws: WebSocket):
    await ws.accept()
    await ws.send_json({"type": "ready", "authorization": ws.headers.get("authorization", "")})
    try:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                return
            if (data := message.get("bytes")) is not None:
                await ws.send_bytes(data)                 # echoed, so a bytes frame flows back
            elif (text := message.get("text")) is not None:
                await ws.send_text(f"echo:{text}")
    except WebSocketDisconnect:
        return


@upstream.websocket("/foo/bar")
async def _generic(ws: WebSocket):
    await ws.accept()
    await ws.send_text("hi from foo/bar")


async def recv(ws, timeout: float = 5.0):
    return await asyncio.wait_for(ws.recv(), timeout=timeout)


def build_gateway(*, stt_model: str = "", tts_model: str = "", upstream_port: int | None = None):
    from app.config import Settings, Upstream
    from app.main import create_app

    gw_port = free_port()
    url = f"http://127.0.0.1:{upstream_port}" if upstream_port else ""
    settings = Settings(
        stt=Upstream("stt", url if stt_model else "", stt_model),
        tts=Upstream("tts", url if tts_model else "", tts_model),
        llm=Upstream("llm", "", ""),
    )
    serve(create_app(settings), gw_port)
    return f"ws://127.0.0.1:{gw_port}"


@pytest.fixture(scope="module")
def up_port():
    port = free_port()
    serve(upstream, port)
    return port


@pytest.fixture(scope="module")
def realtime_on(up_port):
    return build_gateway(stt_model="test-realtime", upstream_port=up_port)


@pytest.fixture(scope="module")
def realtime_off():
    return build_gateway()


@pytest.mark.asyncio
async def test_realtime_explains_itself_when_no_model_is_deployed(realtime_off):
    async with websockets.connect(f"{realtime_off}/v1/realtime") as ws:
        import json
        body = json.loads(await recv(ws))
    assert body["reason"] == "upstream_not_configured"
    assert "STT_MODEL" in body["error"]


@pytest.mark.asyncio
async def test_realtime_relays_query_headers_and_bytes(realtime_on):
    import json
    headers = {"Authorization": "Bearer secret-token"}
    async with websockets.connect(
        f"{realtime_on}/v1/realtime?intent=transcription", additional_headers=headers,
    ) as ws:
        ready = json.loads(await recv(ws))
        assert ready["type"] == "ready"
        assert ready["authorization"] == "Bearer secret-token", \
            "the authorization header was not forwarded upstream"

        payload = b"\x01\x02\x03\x04"
        await ws.send(payload)
        echoed = await recv(ws)
        assert echoed == payload, "a bytes frame from upstream did not reach the client"

        await ws.send("ping")
        text_echo = await recv(ws)
        assert text_echo == "echo:ping"


@pytest.mark.asyncio
async def test_realtime_upstream_unreachable_is_explained_not_hung():
    """Enabled but nothing is listening -- the genuinely-unreachable path in
    relay_ws, distinct from the not-deployed refuse-before-dialling path."""
    import json
    dead_port = free_port()          # nothing bound here
    gw = build_gateway(stt_model="test-realtime", upstream_port=dead_port)
    async with websockets.connect(f"{gw}/v1/realtime") as ws:
        body = json.loads(await recv(ws))
    assert body["reason"] == "upstream_unreachable"
    assert "not reachable" in body["error"]


@pytest.mark.asyncio
async def test_slot_ws_passthrough_relays_to_a_generic_socket_route(up_port):
    gw = build_gateway(tts_model="test-generic", upstream_port=up_port)
    async with websockets.connect(f"{gw}/tts/foo/bar") as ws:
        assert await recv(ws) == "hi from foo/bar"


@pytest.mark.asyncio
async def test_slot_ws_passthrough_refuses_an_empty_slot():
    import json
    gw = build_gateway()              # tts filled with nothing
    async with websockets.connect(f"{gw}/tts/foo/bar") as ws:
        body = json.loads(await recv(ws))
    assert body["reason"] == "upstream_not_configured"
    assert "tts" in body["error"]


@pytest.mark.asyncio
async def test_slot_ws_passthrough_refuses_an_unknown_slot():
    import json
    gw = build_gateway()
    async with websockets.connect(f"{gw}/nonexistent/foo/bar") as ws:
        body = json.loads(await recv(ws))
    assert body["reason"] == "upstream_not_configured"
    assert "nonexistent" in body["error"]
