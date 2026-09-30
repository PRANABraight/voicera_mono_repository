"""Tests for the Bhashini NVCF gRPC TTS service (apps.providers.adapters.bhashini.tts)."""

from __future__ import annotations

import grpc
import numpy as np
import pytest

from apps.providers.adapters.bhashini.tts import BhashiniTTSService


def _make_service(**overrides):
    kwargs = {
        "auth_token": "tok",
        "function_id": "fn",
        "voice": "Divya",
        "language": "hi",
    }
    kwargs.update(overrides)
    return BhashiniTTSService(**kwargs)


def test_requires_auth_token():
    with pytest.raises(ValueError, match="requires auth_token"):
        BhashiniTTSService(auth_token="  ", function_id="fn")


def test_requires_function_id():
    with pytest.raises(ValueError, match="requires function_id"):
        BhashiniTTSService(auth_token="tok", function_id="   ")


def test_full_description_prefixes_voice():
    svc = _make_service(voice="Divya", description="calm voice")
    assert svc._full_description() == "Divya calm voice"


def test_full_description_without_voice():
    svc = _make_service(voice="  ", description="calm voice")
    assert svc._full_description() == "calm voice"


def test_can_generate_metrics_true():
    assert _make_service().can_generate_metrics() is True


def test_to_pcm16_bytes_from_float():
    audio = np.array([0.5, -0.5, 2.0, -2.0], dtype=np.float32)
    out = BhashiniTTSService._to_pcm16_bytes(audio)
    assert isinstance(out, bytes)
    assert len(out) == len(audio) * 2


def test_to_pcm16_bytes_from_int16_passthrough():
    audio = np.array([1, 2, 3], dtype=np.int16)
    out = BhashiniTTSService._to_pcm16_bytes(audio)
    assert out == audio.tobytes()


def test_to_pcm16_bytes_from_other_int_dtype_clips():
    audio = np.array([40000, -40000], dtype=np.int32)
    out = BhashiniTTSService._to_pcm16_bytes(audio)
    assert len(out) == 4


def test_to_pcm16_bytes_from_unexpected_dtype_falls_back():
    audio = np.array([True, False], dtype=np.bool_)
    out = BhashiniTTSService._to_pcm16_bytes(audio)
    assert len(out) == 4


class _FakeResponse:
    def __init__(self, kind: str, *, sample_rate: int = 0, pcm_data: bytes = b""):
        self._kind = kind
        self.meta = type("Meta", (), {"sample_rate": sample_rate})()
        self.audio = type("Audio", (), {"pcm_data": pcm_data})()

    def WhichOneof(self, _name: str) -> str:
        return self._kind


class _FakeStub:
    def __init__(self, responses):
        self._responses = responses

    def Synthesize(self, request, metadata=None):
        responses = self._responses

        async def _agen():
            for resp in responses:
                yield resp

        return _agen()


class _FakeSecureChannel:
    def __init__(self, stub):
        self._stub = stub

    async def __aenter__(self):
        return self._stub

    async def __aexit__(self, *exc_info):
        return False


async def _collect(async_gen):
    out = []
    async for item in async_gen:
        out.append(item)
    return out


@pytest.mark.anyio
async def test_run_tts_empty_text_yields_nothing():
    svc = _make_service()
    frames = await _collect(svc.run_tts("   ", "ctx-1"))
    assert frames == []


@pytest.mark.anyio
async def test_run_tts_streams_audio_frames(monkeypatch):
    import apps.providers.adapters.bhashini.tts as tts_mod

    pcm = np.array([0.1, 0.2], dtype=np.float32).tobytes()
    responses = [
        _FakeResponse("meta", sample_rate=22050),
        _FakeResponse("audio", pcm_data=pcm),
        _FakeResponse("done"),
    ]
    fake_stub = _FakeStub(responses)

    monkeypatch.setattr(tts_mod.grpc, "ssl_channel_credentials", lambda: object())
    monkeypatch.setattr(
        tts_mod.grpc.aio,
        "secure_channel",
        lambda url, creds: _FakeSecureChannel(fake_stub),
    )
    monkeypatch.setattr(
        tts_mod.tts_pb2_grpc, "TTSServiceStub", lambda channel: fake_stub
    )

    svc = _make_service()
    frames = await _collect(svc.run_tts("hello", "ctx-2"))

    audio_frames = [f for f in frames if type(f).__name__ == "TTSAudioRawFrame"]
    assert len(audio_frames) == 1
    assert audio_frames[0].sample_rate == 22050


@pytest.mark.anyio
async def test_run_tts_grpc_error_yields_error_frame(monkeypatch):
    import apps.providers.adapters.bhashini.tts as tts_mod

    class _FakeRpcError(grpc.aio.AioRpcError):
        def __init__(self):
            pass

        def code(self):
            return "UNAVAILABLE"

        def details(self):
            return "server down"

    class _RaisingStub:
        def Synthesize(self, request, metadata=None):
            async def _agen():
                raise _FakeRpcError()
                yield  # pragma: no cover - unreachable, makes this an async generator

            return _agen()

    fake_stub = _RaisingStub()
    monkeypatch.setattr(tts_mod.grpc, "ssl_channel_credentials", lambda: object())
    monkeypatch.setattr(
        tts_mod.grpc.aio,
        "secure_channel",
        lambda url, creds: _FakeSecureChannel(fake_stub),
    )
    monkeypatch.setattr(
        tts_mod.tts_pb2_grpc, "TTSServiceStub", lambda channel: fake_stub
    )

    svc = _make_service()
    frames = await _collect(svc.run_tts("hello", "ctx-3"))
    assert len(frames) == 1
    assert type(frames[0]).__name__ == "ErrorFrame"
    assert "gRPC error" in frames[0].error


@pytest.mark.anyio
async def test_run_tts_unexpected_error_yields_error_frame(monkeypatch):
    import apps.providers.adapters.bhashini.tts as tts_mod

    class _RaisingStub:
        def Synthesize(self, request, metadata=None):
            async def _agen():
                raise RuntimeError("boom")
                yield  # pragma: no cover

            return _agen()

    fake_stub = _RaisingStub()
    monkeypatch.setattr(tts_mod.grpc, "ssl_channel_credentials", lambda: object())
    monkeypatch.setattr(
        tts_mod.grpc.aio,
        "secure_channel",
        lambda url, creds: _FakeSecureChannel(fake_stub),
    )
    monkeypatch.setattr(
        tts_mod.tts_pb2_grpc, "TTSServiceStub", lambda channel: fake_stub
    )

    svc = _make_service()
    frames = await _collect(svc.run_tts("hello", "ctx-4"))
    assert len(frames) == 1
    assert type(frames[0]).__name__ == "ErrorFrame"
    assert "TTS error: boom" in frames[0].error
