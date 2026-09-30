"""Tests for the local Indic Orpheus TTS service (OpenAI-compatible client)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from openai import BadRequestError

from apps.providers.local.indic_orpheus.tts import IndicOrpheusTTSService


def _make_service(**overrides):
    kwargs = {
        "api_key": "key",
        "base_url": "http://model-server:8100/v1",
        "voice": "Amit",
    }
    kwargs.update(overrides)
    svc = IndicOrpheusTTSService(**kwargs)
    svc._sample_rate = svc._init_sample_rate or 24000
    return svc


async def _collect(async_gen):
    out = []
    async for item in async_gen:
        out.append(item)
    return out


class _FakeStreamingResponse:
    def __init__(self, status_code: int, chunks: list[bytes], error_text: str = ""):
        self.status_code = status_code
        self._chunks = chunks
        self._error_text = error_text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def text(self):
        return self._error_text

    def iter_bytes(self, chunk_size):
        async def _agen():
            for c in self._chunks:
                yield c

        return _agen()


def test_can_generate_metrics_true():
    assert _make_service().can_generate_metrics() is True


def test_init_sets_style_and_warns_on_unsupported_rate():
    svc = _make_service(sample_rate=16000, style="  news  ")
    assert svc._init_sample_rate == 16000
    assert svc._style == "news"


def test_init_style_none_stays_none():
    svc = _make_service(style=None)
    assert svc._style is None


@pytest.mark.anyio
async def test_run_tts_empty_text_yields_nothing():
    svc = _make_service()
    frames = await _collect(svc.run_tts("  ", "ctx-1"))
    assert frames == []


@pytest.mark.anyio
async def test_run_tts_missing_voice_yields_error():
    svc = _make_service(voice="")
    frames = await _collect(svc.run_tts("hello", "ctx-2"))
    assert len(frames) == 1
    assert type(frames[0]).__name__ == "ErrorFrame"
    assert "voice must be specified" in frames[0].error


@pytest.mark.anyio
async def test_run_tts_streams_pcm_on_200():
    svc = _make_service()
    response = _FakeStreamingResponse(200, [b"\x01\x02", b"\x03\x04"])
    svc._client = MagicMock()
    svc._client.audio.speech.with_streaming_response.create = MagicMock(
        return_value=response
    )
    svc.start_tts_usage_metrics = AsyncMock()

    frames = await _collect(svc.run_tts("hello", "ctx-3"))
    audio_frames = [f for f in frames if type(f).__name__ == "TTSAudioRawFrame"]
    assert b"".join(f.audio for f in audio_frames) == b"\x01\x02\x03\x04"


@pytest.mark.anyio
async def test_run_tts_non_200_yields_error():
    svc = _make_service()
    response = _FakeStreamingResponse(400, [], error_text="bad request")
    svc._client = MagicMock()
    svc._client.audio.speech.with_streaming_response.create = MagicMock(
        return_value=response
    )

    frames = await _collect(svc.run_tts("hello", "ctx-4"))
    assert len(frames) == 1
    assert "status: 400" in frames[0].error
    assert "bad request" in frames[0].error


@pytest.mark.anyio
async def test_run_tts_bad_request_error_yields_error_frame():
    svc = _make_service()

    def raise_bad_request(**kwargs):
        raise BadRequestError(
            message="invalid voice",
            response=MagicMock(status_code=400, headers={}, request=MagicMock()),
            body=None,
        )

    svc._client = MagicMock()
    svc._client.audio.speech.with_streaming_response.create = raise_bad_request

    frames = await _collect(svc.run_tts("hello", "ctx-5"))
    assert len(frames) == 1
    assert "Unknown error occurred" in frames[0].error


@pytest.mark.anyio
async def test_run_tts_unexpected_error_yields_error_frame():
    svc = _make_service()

    def raise_runtime_error(**kwargs):
        raise RuntimeError("network exploded")

    svc._client = MagicMock()
    svc._client.audio.speech.with_streaming_response.create = raise_runtime_error

    frames = await _collect(svc.run_tts("hello", "ctx-6"))
    assert len(frames) == 1
    assert "network exploded" in frames[0].error
