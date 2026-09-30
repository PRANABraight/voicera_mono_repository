"""Tests for the Bhashini Dhruva websocket STT service."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import numpy as np
import pytest

from apps.providers.adapters.bhashini.stt import (
    BhashiniSTTService,
    BotSpeakingLatch,
    VADProcessor,
)


def _make_service(**overrides):
    kwargs = {"api_key": "key", "language": "hi"}
    kwargs.update(overrides)
    svc = BhashiniSTTService(**kwargs)
    svc._sample_rate = svc._init_sample_rate or 16000
    svc.push_frame = AsyncMock()
    svc.start_ttfb_metrics = AsyncMock()
    svc.stop_ttfb_metrics = AsyncMock()
    svc.start_processing_metrics = AsyncMock()
    svc.stop_processing_metrics = AsyncMock()
    return svc


def _silence_chunk(n_samples: int = 320) -> bytes:
    return np.zeros(n_samples, dtype=np.int16).tobytes()


def _loud_chunk(n_samples: int = 320, amplitude: int = 20000) -> bytes:
    return (np.ones(n_samples, dtype=np.int16) * amplitude).tobytes()


# --- BotSpeakingLatch ---


def test_bot_speaking_latch_on_started_sets_speaking():
    latch = BotSpeakingLatch()
    latch.on_started()
    assert latch.speaking is True


@pytest.mark.anyio
async def test_bot_speaking_latch_on_stopped_clears_after_delay():
    latch = BotSpeakingLatch(clear_delay_secs=0.01)
    latch.on_started()
    latch.on_stopped()
    assert latch.speaking is True  # not cleared immediately
    await asyncio.sleep(0.03)
    assert latch.speaking is False


@pytest.mark.anyio
async def test_bot_speaking_latch_restart_cancels_pending_clear():
    latch = BotSpeakingLatch(clear_delay_secs=0.01)
    latch.on_started()
    latch.on_stopped()
    latch.on_started()  # cancels the pending clear task
    await asyncio.sleep(0.03)
    assert latch.speaking is True


def test_bot_speaking_latch_reset():
    latch = BotSpeakingLatch()
    latch.on_started()
    latch.reset()
    assert latch.speaking is False
    assert latch._clear_task is None


# --- VADProcessor ---


def test_vad_processor_empty_chunk_is_idle():
    vad = VADProcessor()
    assert vad.process_chunk(b"") == "IDLE"


def test_vad_processor_silence_is_idle():
    vad = VADProcessor()
    assert vad.process_chunk(_silence_chunk()) == "IDLE"


def test_vad_processor_start_requires_min_speech_run():
    vad = VADProcessor(chunk_ms=200, min_speech_ms_idle=200)
    # First loud chunk only accumulates speech_run_ms, not yet past threshold
    # (min_speech_ms_idle=200 == chunk_ms=200, so a single chunk clears it).
    assert vad.process_chunk(_loud_chunk()) == "START"
    assert vad.is_speaking is True


def test_vad_processor_start_takes_multiple_chunks_when_bot_speaking():
    vad = VADProcessor(chunk_ms=200, min_speech_ms=400)
    vad.bot_speaking = True
    assert vad.process_chunk(_loud_chunk()) == "IDLE"  # not yet started
    assert vad.is_speaking is False
    assert vad.process_chunk(_loud_chunk()) == "START"
    assert vad.is_speaking is True


def test_vad_processor_continue_then_stop():
    vad = VADProcessor(chunk_ms=200, min_speech_ms_idle=200, min_pause_ms=200)
    assert vad.process_chunk(_loud_chunk()) == "START"
    assert vad.process_chunk(_loud_chunk()) == "CONTINUE"
    assert vad.process_chunk(_silence_chunk()) == "STOP"
    assert vad.is_speaking is False


def test_vad_processor_silence_resets_speech_run():
    vad = VADProcessor(chunk_ms=200, min_speech_ms_idle=400)
    vad.process_chunk(_loud_chunk())  # speech_run_ms = 200, not yet started
    assert vad.is_speaking is False
    vad.process_chunk(_silence_chunk())  # resets speech_run_ms to 0
    assert vad.process_chunk(_loud_chunk()) == "IDLE"  # back to square one
    assert vad.is_speaking is False


def test_vad_processor_brief_silence_does_not_stop():
    vad = VADProcessor(chunk_ms=200, min_speech_ms_idle=200, min_pause_ms=400)
    vad.process_chunk(_loud_chunk())
    assert vad.is_speaking is True
    result = vad.process_chunk(_silence_chunk())  # only 200ms silence, needs 400
    assert result == "CONTINUE"
    assert vad.is_speaking is True


# --- Service construction / small helpers ---


def test_requires_api_key():
    with pytest.raises(ValueError, match="requires api_key"):
        BhashiniSTTService(api_key="  ")


def test_can_generate_metrics_true():
    assert _make_service().can_generate_metrics() is True


def test_build_ws_url_no_existing_query():
    svc = _make_service(ws_url="wss://host/ws/v1/asr/stream")
    assert svc._build_ws_url() == "wss://host/ws/v1/asr/stream?api_key=key"


def test_build_ws_url_with_existing_query():
    svc = _make_service(ws_url="wss://host/ws/v1/asr/stream?foo=bar")
    assert svc._build_ws_url() == "wss://host/ws/v1/asr/stream?foo=bar&api_key=key"


def test_get_start_config_shape():
    svc = _make_service(language="hi", service_id="svc-1", chunk_ms=100)
    cfg = svc._get_start_config()
    assert cfg["language"]["sourceLanguage"] == "hi"
    assert cfg["config"]["serviceId"] == "svc-1"
    assert cfg["streamingConfig"]["chunkDurationMs"] == 100


def test_pcm16_to_float32_bytes_empty():
    svc = _make_service()
    assert svc._pcm16_to_float32_bytes(b"") == b""


def test_pcm16_to_float32_bytes_nonempty():
    svc = _make_service()
    pcm = np.array([16384, -16384], dtype=np.int16).tobytes()
    out = svc._pcm16_to_float32_bytes(pcm)
    floats = np.frombuffer(out, dtype=np.float32)
    assert floats[0] == pytest.approx(0.5, abs=0.01)
    assert floats[1] == pytest.approx(-0.5, abs=0.01)


@pytest.mark.anyio
async def test_set_language_updates_language():
    svc = _make_service()
    svc._language = "hi"
    await svc.set_language("or")
    assert svc._language == "or"


@pytest.mark.anyio
async def test_set_model_updates_service_id():
    svc = _make_service()
    await svc.set_model("new-service-id")
    assert svc._service_id == "new-service-id"


# --- Telemetry ---


@pytest.mark.anyio
async def test_emit_latency_metric_noop_without_callback():
    svc = _make_service()
    await svc._emit_latency_metric("ws_open_ms", 12.3)  # no callback: no-op


@pytest.mark.anyio
async def test_emit_latency_metric_calls_callback():
    calls = []

    async def callback(payload):
        calls.append(payload)

    svc = _make_service(telemetry_callback=callback)
    await svc._emit_latency_metric("ws_open_ms", 12.3, stage="ws_open")
    assert len(calls) == 1
    assert calls[0]["metric"] == "ws_open_ms"
    assert calls[0]["stage"] == "ws_open"


@pytest.mark.anyio
async def test_emit_latency_metric_swallows_callback_errors():
    async def callback(payload):
        raise RuntimeError("telemetry down")

    svc = _make_service(telemetry_callback=callback)
    await svc._emit_latency_metric("ws_open_ms", 1.0)  # error logged, not raised


# --- _send_json / _send_audio ---


@pytest.mark.anyio
async def test_send_json_raises_when_not_connected():
    svc = _make_service()
    with pytest.raises(RuntimeError, match="not connected"):
        await svc._send_json({"type": "end"})


@pytest.mark.anyio
async def test_send_audio_raises_when_not_connected():
    svc = _make_service()
    with pytest.raises(RuntimeError, match="not connected"):
        await svc._send_audio(_loud_chunk())


@pytest.mark.anyio
async def test_send_audio_resamples_when_rate_mismatched():
    svc = _make_service(input_sample_rate=8000)
    svc._websocket = AsyncMock()
    resampled = np.ones(10, dtype=np.int16).tobytes()

    async def fake_resample(audio, in_rate, out_rate):
        assert in_rate == 8000
        assert out_rate == 16000
        return resampled

    svc._resampler.resample = fake_resample
    await svc._send_audio(_loud_chunk())
    svc._websocket.send.assert_called_once()


@pytest.mark.anyio
async def test_send_audio_noop_when_resample_empties_audio():
    svc = _make_service(input_sample_rate=8000)
    svc._websocket = AsyncMock()

    async def fake_resample(audio, in_rate, out_rate):
        return b""

    svc._resampler.resample = fake_resample
    await svc._send_audio(_loud_chunk())
    svc._websocket.send.assert_not_called()


@pytest.mark.anyio
async def test_send_audio_sends_when_rate_matches():
    svc = _make_service(input_sample_rate=16000)
    svc._websocket = AsyncMock()
    await svc._send_audio(_loud_chunk())
    svc._websocket.send.assert_called_once()


# --- _open_websocket ---


@pytest.mark.anyio
async def test_open_websocket_noop_when_already_open():
    svc = _make_service()
    svc._websocket = object()
    assert await svc._open_websocket() is True


@pytest.mark.anyio
async def test_open_websocket_noop_when_disabled():
    svc = _make_service()
    svc._disabled = True
    assert await svc._open_websocket() is False


class _FakeWebSocket:
    def __init__(self, messages=None, *, stay_open=False):
        self._messages = messages or []
        self._stay_open = stay_open
        self.sent = []
        self.closed = False

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for m in self._messages:
            yield m
        if self._stay_open:
            await asyncio.Event().wait()

    async def send(self, data):
        self.sent.append(data)

    async def close(self):
        self.closed = True


@pytest.mark.anyio
async def test_open_websocket_success(monkeypatch):
    svc = _make_service()
    fake_ws = _FakeWebSocket(stay_open=True)

    async def fake_connect(uri, ping_interval=None):
        return fake_ws

    import apps.providers.adapters.bhashini.stt as stt_mod

    monkeypatch.setattr(stt_mod.websockets, "connect", fake_connect)
    assert await svc._open_websocket() is True
    assert svc._stream_started is True
    assert json.loads(fake_ws.sent[0])["type"] == "start"
    await svc._close_websocket()


@pytest.mark.anyio
async def test_open_websocket_failure_disables_service(monkeypatch):
    svc = _make_service()

    async def fake_connect(uri, ping_interval=None):
        raise RuntimeError("connect failed")

    import apps.providers.adapters.bhashini.stt as stt_mod

    monkeypatch.setattr(stt_mod.websockets, "connect", fake_connect)
    assert await svc._open_websocket() is False
    assert svc._disabled is True
    assert svc._websocket is None


# --- _receive_handler ---


@pytest.mark.anyio
async def test_receive_handler_skips_non_json_and_non_transcript():
    svc = _make_service()
    svc._websocket = _FakeWebSocket(
        ["not json", json.dumps({"type": "other"}), json.dumps({"type": "transcript", "output": []})]
    )
    await svc._receive_handler()
    svc.push_frame.assert_not_called()


@pytest.mark.anyio
async def test_receive_handler_skips_empty_text():
    svc = _make_service()
    svc._websocket = _FakeWebSocket(
        [json.dumps({"type": "transcript", "output": [{"source": "  "}]})]
    )
    await svc._receive_handler()
    svc.push_frame.assert_not_called()


@pytest.mark.anyio
async def test_receive_handler_interim_pushes_interim_frame():
    svc = _make_service()
    svc._websocket = _FakeWebSocket(
        [json.dumps({"type": "transcript", "output": [{"source": "hel"}], "isFinal": False})]
    )
    await svc._receive_handler()
    svc.push_frame.assert_awaited_once()
    frame = svc.push_frame.await_args.args[0]
    assert type(frame).__name__ == "InterimTranscriptionFrame"
    assert frame.text == "hel"


@pytest.mark.anyio
async def test_receive_handler_final_pushes_transcription_and_sets_event():
    svc = _make_service()
    svc._final_transcript_event = asyncio.Event()
    svc._speech_started_at = 0.0
    svc._segment_started_at = 0.0
    calls = []

    async def callback(payload):
        calls.append(payload["metric"])

    svc._telemetry_callback = callback
    svc._websocket = _FakeWebSocket(
        [json.dumps({"type": "transcript", "output": [{"source": "hello"}], "isFinal": True})]
    )
    await svc._receive_handler()
    assert svc._final_transcript_event.is_set()
    svc.push_frame.assert_awaited_once()
    frame = svc.push_frame.await_args.args[0]
    assert type(frame).__name__ == "TranscriptionFrame"
    assert frame.text == "hello"
    svc.stop_processing_metrics.assert_awaited_once()
    assert "first_transcript_ms" in calls
    assert "final_transcript_ms" in calls
    assert "segment_duration_ms" in calls


@pytest.mark.anyio
async def test_receive_handler_swallows_exception_when_closed():
    svc = _make_service()
    svc._closed = True

    class _Raising:
        def __aiter__(self):
            return self._iter()

        async def _iter(self):
            raise RuntimeError("boom")
            yield  # pragma: no cover

    svc._websocket = _Raising()
    await svc._receive_handler()
    svc.push_frame.assert_not_called()


@pytest.mark.anyio
async def test_receive_handler_pushes_error_when_not_closed():
    svc = _make_service()

    class _Raising:
        def __aiter__(self):
            return self._iter()

        async def _iter(self):
            raise RuntimeError("boom")
            yield  # pragma: no cover

    svc._websocket = _Raising()
    await svc._receive_handler()
    svc.push_frame.assert_awaited_once()
    frame = svc.push_frame.await_args.args[0]
    assert "receive loop failed" in frame.error


@pytest.mark.anyio
async def test_receive_handler_reraises_cancelled():
    svc = _make_service()

    class _Cancelling:
        def __aiter__(self):
            return self._iter()

        async def _iter(self):
            raise asyncio.CancelledError()
            yield  # pragma: no cover

    svc._websocket = _Cancelling()
    with pytest.raises(asyncio.CancelledError):
        await svc._receive_handler()


# --- _close_websocket ---


@pytest.mark.anyio
async def test_close_websocket_when_nothing_open():
    svc = _make_service()
    await svc._close_websocket()
    assert svc._closed is True


@pytest.mark.anyio
async def test_close_websocket_cancels_task_and_closes_socket():
    svc = _make_service()

    async def never_ending():
        await asyncio.Event().wait()

    svc._receiver_task = asyncio.create_task(never_ending())
    svc._websocket = _FakeWebSocket()
    await svc._close_websocket()
    assert svc._receiver_task is None
    assert svc._websocket is None


@pytest.mark.anyio
async def test_close_websocket_swallows_close_error():
    svc = _make_service()

    class _BadWs(_FakeWebSocket):
        async def close(self):
            raise RuntimeError("close failed")

    svc._websocket = _BadWs()
    await svc._close_websocket()
    assert svc._websocket is None


# --- _finalize_segment ---


@pytest.mark.anyio
async def test_finalize_segment_noop_when_not_active():
    svc = _make_service()
    svc._segment_active = False
    await svc._finalize_segment()  # nothing to send, nothing raised


@pytest.mark.anyio
async def test_finalize_segment_sends_end_and_returns_on_final_event():
    svc = _make_service()
    svc._websocket = AsyncMock()
    svc._segment_active = True
    svc._final_transcript_event = asyncio.Event()
    svc._final_transcript_event.set()
    await svc._finalize_segment()
    assert svc._segment_active is False
    svc._websocket.send.assert_called_once()


@pytest.mark.anyio
async def test_finalize_segment_timeout_short_text_is_dropped():
    svc = _make_service()
    svc._websocket = AsyncMock()
    svc._segment_active = True
    svc._final_transcript_event = asyncio.Event()  # never set -> times out
    svc._latest_transcript_text = "hi"  # 1 word, 2 chars: too short to promote

    import apps.providers.adapters.bhashini.stt as stt_mod

    async def fake_wait_for(coro, timeout):
        coro.close()
        raise asyncio.TimeoutError()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(stt_mod.asyncio, "wait_for", fake_wait_for)
        await svc._finalize_segment()
    svc.push_frame.assert_not_called()


@pytest.mark.anyio
async def test_finalize_segment_timeout_promotes_latest_interim():
    svc = _make_service()
    svc._websocket = AsyncMock()
    svc._segment_active = True
    svc._final_transcript_event = asyncio.Event()
    svc._latest_transcript_text = "a much longer interim transcript"
    svc._speech_started_at = 0.0

    import apps.providers.adapters.bhashini.stt as stt_mod

    async def fake_wait_for(coro, timeout):
        coro.close()
        raise asyncio.TimeoutError()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(stt_mod.asyncio, "wait_for", fake_wait_for)
        await svc._finalize_segment()
    svc.push_frame.assert_awaited_once()
    frame = svc.push_frame.await_args.args[0]
    assert frame.text == "a much longer interim transcript"
    svc.stop_processing_metrics.assert_awaited_once()


# --- _handle_audio_chunk / process_frame ---


@pytest.mark.anyio
async def test_handle_audio_chunk_start_failed_when_open_fails():
    svc = _make_service()
    svc._open_websocket = AsyncMock(return_value=False)
    result = await svc._handle_audio_chunk(_loud_chunk())
    assert result == "START_FAILED"


@pytest.mark.anyio
async def test_handle_audio_chunk_start_success_sends_preroll_and_audio():
    svc = _make_service()
    svc._open_websocket = AsyncMock(return_value=True)
    svc._send_audio = AsyncMock()
    result = await svc._handle_audio_chunk(_loud_chunk(), pre_roll_bytes=b"\x00\x01")
    assert result == "START"
    assert svc._segment_active is True
    assert svc._send_audio.await_count == 2


@pytest.mark.anyio
async def test_handle_audio_chunk_continue_sends_audio_when_active():
    svc = _make_service()
    svc._vad.is_speaking = True
    svc._segment_active = True
    svc._send_audio = AsyncMock()
    result = await svc._handle_audio_chunk(_loud_chunk())
    assert result == "CONTINUE"
    svc._send_audio.assert_awaited_once()


@pytest.mark.anyio
async def test_handle_audio_chunk_stop_finalizes_and_closes():
    svc = _make_service()
    svc._vad.is_speaking = True
    svc._vad.silence_run_ms = 10_000  # force STOP on next silent chunk
    svc._finalize_segment = AsyncMock()
    svc._close_websocket = AsyncMock()
    result = await svc._handle_audio_chunk(_silence_chunk())
    assert result == "STOP"
    svc._finalize_segment.assert_awaited_once()
    svc._close_websocket.assert_awaited_once()


@pytest.mark.anyio
async def test_handle_audio_chunk_idle_passthrough():
    svc = _make_service()
    result = await svc._handle_audio_chunk(_silence_chunk())
    assert result == "IDLE"


@pytest.mark.anyio
async def test_process_frame_bot_started_and_stopped():
    from pipecat.frames.frames import BotStartedSpeakingFrame, BotStoppedSpeakingFrame
    from pipecat.processors.frame_processor import FrameDirection

    svc = _make_service()
    await svc.process_frame(BotStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    assert svc._bot_latch.speaking is True
    svc._bot_latch._clear_delay_secs = 0.01
    await svc.process_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    await asyncio.sleep(0.03)
    assert svc._bot_latch.speaking is False


# --- start / stop / cancel ---


@pytest.mark.anyio
async def test_start_resets_state():
    from pipecat.frames.frames import StartFrame

    svc = _make_service()
    svc._segment_active = True
    await svc.start(StartFrame())
    assert svc._closed is False
    assert svc._segment_active is False


@pytest.mark.anyio
async def test_stop_no_websocket_still_closes():
    from pipecat.frames.frames import EndFrame

    svc = _make_service()
    svc._close_websocket = AsyncMock()
    await svc.stop(EndFrame())
    svc._close_websocket.assert_awaited_once()


@pytest.mark.anyio
async def test_stop_with_segment_active_finalizes():
    from pipecat.frames.frames import EndFrame

    svc = _make_service()
    svc._websocket = object()
    svc._stream_started = True
    svc._segment_active = True
    svc._finalize_segment = AsyncMock()
    svc._close_websocket = AsyncMock()
    await svc.stop(EndFrame())
    svc._finalize_segment.assert_awaited_once()


@pytest.mark.anyio
async def test_stop_without_active_segment_sends_end():
    from pipecat.frames.frames import EndFrame

    svc = _make_service()
    svc._websocket = AsyncMock()
    svc._stream_started = True
    svc._segment_active = False
    svc._close_websocket = AsyncMock()
    await svc.stop(EndFrame())
    svc._websocket.send.assert_called_once()


@pytest.mark.anyio
async def test_cancel_with_segment_active_finalizes():
    from pipecat.frames.frames import CancelFrame

    svc = _make_service()
    svc._websocket = object()
    svc._stream_started = True
    svc._segment_active = True
    svc._finalize_segment = AsyncMock()
    svc._close_websocket = AsyncMock()
    await svc.cancel(CancelFrame())
    svc._finalize_segment.assert_awaited_once()


@pytest.mark.anyio
async def test_cancel_without_active_segment_sends_end():
    from pipecat.frames.frames import CancelFrame

    svc = _make_service()
    svc._websocket = AsyncMock()
    svc._stream_started = True
    svc._segment_active = False
    svc._close_websocket = AsyncMock()
    await svc.cancel(CancelFrame())
    svc._websocket.send.assert_called_once()


# --- run_stt ---


async def _collect(async_gen):
    out = []
    async for item in async_gen:
        out.append(item)
    return out


@pytest.mark.anyio
async def test_run_stt_empty_or_disabled_yields_nothing():
    svc = _make_service()
    assert await _collect(svc.run_stt(b"")) == []
    svc._disabled = True
    assert await _collect(svc.run_stt(_loud_chunk())) == []


@pytest.mark.anyio
async def test_run_stt_yields_start_and_stop_frames():
    svc = _make_service(chunk_ms=200, input_sample_rate=16000)
    svc._recompute_audio_params()

    states = iter(["START", "CONTINUE", "STOP"])

    async def fake_handle(chunk, pre_roll_bytes=b""):
        return next(states)

    svc._handle_audio_chunk = fake_handle
    # 3 chunks worth of audio so the while loop runs 3 times.
    audio = _loud_chunk(svc._chunk_samples * 3)
    frames = await _collect(svc.run_stt(audio))
    frame_types = [type(f).__name__ for f in frames]
    assert "UserStartedSpeakingFrame" in frame_types
    assert "UserStoppedSpeakingFrame" in frame_types


@pytest.mark.anyio
async def test_run_stt_suppresses_vad_frames_when_configured():
    svc = _make_service(suppress_vad_frames=True)
    svc._recompute_audio_params()
    svc._handle_audio_chunk = AsyncMock(return_value="START")
    audio = _loud_chunk(svc._chunk_samples)
    frames = await _collect(svc.run_stt(audio))
    assert frames == []


@pytest.mark.anyio
async def test_run_stt_handles_normal_close_error_quietly():
    svc = _make_service()
    svc._recompute_audio_params()

    async def raise_normal_close(chunk, pre_roll_bytes=b""):
        raise RuntimeError("received 1000 (OK); then sent 1000 (OK)")

    svc._handle_audio_chunk = raise_normal_close
    audio = _loud_chunk(svc._chunk_samples)
    frames = await _collect(svc.run_stt(audio))
    assert frames == []


@pytest.mark.anyio
async def test_run_stt_yields_error_frame_on_unexpected_exception():
    svc = _make_service()
    svc._recompute_audio_params()

    async def raise_error(chunk, pre_roll_bytes=b""):
        raise RuntimeError("boom")

    svc._handle_audio_chunk = raise_error
    audio = _loud_chunk(svc._chunk_samples)
    frames = await _collect(svc.run_stt(audio))
    assert len(frames) == 1
    assert "processing failed" in frames[0].error


@pytest.mark.anyio
async def test_run_stt_preroll_buffer_trims_overflow():
    svc = _make_service(chunk_ms=200, input_sample_rate=16000)
    svc._pre_roll_ms = 200  # small pre-roll window
    svc._recompute_audio_params()
    svc._handle_audio_chunk = AsyncMock(return_value="CONTINUE")
    # Feed enough chunks that the pre-roll buffer would overflow and get trimmed.
    audio = _loud_chunk(svc._chunk_samples * 3)
    await _collect(svc.run_stt(audio))
    assert len(svc._pre_roll_buffer) <= svc._pre_roll_bytes


@pytest.mark.anyio
async def test_run_stt_clears_preroll_buffer_when_disabled():
    svc = _make_service(chunk_ms=200, input_sample_rate=16000)
    svc._pre_roll_ms = 0  # disables pre-roll accumulation
    svc._recompute_audio_params()
    svc._pre_roll_buffer.extend(b"\x00\x01")
    svc._handle_audio_chunk = AsyncMock(return_value="CONTINUE")
    audio = _loud_chunk(svc._chunk_samples)
    await _collect(svc.run_stt(audio))
    assert svc._pre_roll_buffer == bytearray()
