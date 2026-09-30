"""Tests for the Bhashini Orpheus NVCF HTTP TTS service.

Uses the established httpx.MockTransport + patched httpx.AsyncClient pattern
(see apps/telephony/tests/test_clients.py) since this service streams via
``client.stream(...)`` rather than one-shot request/response.
"""

from __future__ import annotations

from typing import Any, Callable

import httpx
import pytest

from apps.providers.adapters.bhashini.orpheus_tts import BhashiniOrpheusTTSService


def _make_service(**overrides):
    kwargs = {
        "auth_token": "tok",
        "function_id": "fn-123",
        "voice": "Amit",
    }
    kwargs.update(overrides)
    svc = BhashiniOrpheusTTSService(**kwargs)
    # Pipecat only finalizes `_sample_rate` (and thus `chunk_size`) inside
    # `start()`, which needs a fully wired TaskManager/pipeline. Tests here
    # exercise `run_tts` directly, so set it the same way `start()` would.
    svc._sample_rate = svc._init_sample_rate or 24000
    return svc


def _patch_client(monkeypatch: pytest.MonkeyPatch, handler: Callable):
    transport = httpx.MockTransport(handler)

    class _PatchedAsyncClient(httpx.AsyncClient):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(
        "apps.providers.adapters.bhashini.orpheus_tts.httpx.AsyncClient",
        _PatchedAsyncClient,
    )


async def _collect(async_gen):
    out = []
    async for item in async_gen:
        out.append(item)
    return out


def test_requires_auth_token():
    with pytest.raises(ValueError, match="requires auth_token"):
        BhashiniOrpheusTTSService(auth_token=" ", function_id="fn")


def test_requires_function_id():
    with pytest.raises(ValueError, match="requires function_id"):
        BhashiniOrpheusTTSService(auth_token="tok", function_id=" ")


def test_init_warns_on_unsupported_sample_rate():
    svc = _make_service(sample_rate=16000)
    assert svc._init_sample_rate == 16000


def test_can_generate_metrics_true():
    assert _make_service().can_generate_metrics() is True


def test_build_body_includes_style_when_set():
    svc = _make_service(style="news")
    body = svc._build_body("hello")
    assert body == {
        "input": "hello",
        "model": "orpheus",
        "voice": "Amit",
        "response_format": "pcm",
        "style": "news",
    }


def test_build_body_omits_style_when_none():
    svc = _make_service(style=None)
    body = svc._build_body("hello")
    assert "style" not in body


def test_headers_include_bearer_token():
    svc = _make_service()
    assert svc._headers()["Authorization"] == "Bearer tok"


@pytest.mark.anyio
async def test_run_tts_empty_text_yields_nothing():
    svc = _make_service()
    frames = await _collect(svc.run_tts("   ", "ctx-1"))
    assert frames == []


@pytest.mark.anyio
async def test_run_tts_missing_voice_yields_error():
    svc = _make_service(voice="")
    frames = await _collect(svc.run_tts("hello", "ctx-2"))
    assert len(frames) == 1
    assert type(frames[0]).__name__ == "ErrorFrame"
    assert "voice must be specified" in frames[0].error


@pytest.mark.anyio
async def test_run_tts_streams_pcm_on_200(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v1/audio/speech"
        return httpx.Response(200, content=b"\x01\x02\x03\x04")

    _patch_client(monkeypatch, handler)
    svc = _make_service()
    frames = await _collect(svc.run_tts("hello", "ctx-3"))
    audio_frames = [f for f in frames if type(f).__name__ == "TTSAudioRawFrame"]
    assert audio_frames
    assert b"".join(f.audio for f in audio_frames) == b"\x01\x02\x03\x04"


@pytest.mark.anyio
async def test_run_tts_400_error_yields_error_frame(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, content=b"bad request")

    _patch_client(monkeypatch, handler)
    svc = _make_service()
    frames = await _collect(svc.run_tts("hello", "ctx-4"))
    assert len(frames) == 1
    assert type(frames[0]).__name__ == "ErrorFrame"
    assert "status: 400" in frames[0].error


@pytest.mark.anyio
async def test_run_tts_202_without_reqid_header_yields_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(202, content=b"")

    _patch_client(monkeypatch, handler)
    svc = _make_service()
    frames = await _collect(svc.run_tts("hello", "ctx-5"))
    assert len(frames) == 1
    assert "no NVCF-REQID" in frames[0].error


@pytest.mark.anyio
async def test_run_tts_202_then_poll_success(monkeypatch):
    calls = {"poll": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/audio/speech":
            return httpx.Response(
                202, headers={"NVCF-REQID": "req-1"}, content=b""
            )
        # Poll endpoint: 202 once, then 200 with pcm bytes.
        calls["poll"] += 1
        if calls["poll"] == 1:
            return httpx.Response(202, content=b"")
        return httpx.Response(200, content=b"\x05\x06")

    _patch_client(monkeypatch, handler)
    svc = _make_service(poll_interval_s=0.001)
    frames = await _collect(svc.run_tts("hello", "ctx-6"))
    audio_frames = [f for f in frames if type(f).__name__ == "TTSAudioRawFrame"]
    assert b"".join(f.audio for f in audio_frames) == b"\x05\x06"
    assert calls["poll"] >= 2


@pytest.mark.anyio
async def test_run_tts_202_then_poll_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/audio/speech":
            return httpx.Response(
                202, headers={"NVCF-REQID": "req-1"}, content=b""
            )
        return httpx.Response(500, content=b"server error")

    _patch_client(monkeypatch, handler)
    svc = _make_service(poll_interval_s=0.001)
    frames = await _collect(svc.run_tts("hello", "ctx-7"))
    assert len(frames) == 1
    assert "status: 500" in frames[0].error


@pytest.mark.anyio
async def test_run_tts_202_poll_times_out(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/audio/speech":
            return httpx.Response(
                202, headers={"NVCF-REQID": "req-1"}, content=b""
            )
        return httpx.Response(202, content=b"")

    _patch_client(monkeypatch, handler)
    svc = _make_service(timeout_s=0.0, poll_interval_s=0.001)
    frames = await _collect(svc.run_tts("hello", "ctx-8"))
    assert len(frames) == 1
    assert type(frames[0]).__name__ == "ErrorFrame"
    assert "TTS error" in frames[0].error


@pytest.mark.anyio
async def test_run_tts_unexpected_exception_yields_error_frame(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise RuntimeError("network exploded")

    _patch_client(monkeypatch, handler)
    svc = _make_service()
    frames = await _collect(svc.run_tts("hello", "ctx-9"))
    assert len(frames) == 1
    assert "network exploded" in frames[0].error


@pytest.mark.anyio
async def test_get_client_reuses_open_client():
    svc = _make_service()
    client1 = await svc._get_client()
    client2 = await svc._get_client()
    assert client1 is client2
    await svc.cleanup()


@pytest.mark.anyio
async def test_cleanup_closes_client():
    svc = _make_service()
    client = await svc._get_client()
    assert not client.is_closed
    await svc.cleanup()
    assert svc._client is None


@pytest.mark.anyio
async def test_cleanup_noop_when_no_client():
    svc = _make_service()
    await svc.cleanup()
    assert svc._client is None
