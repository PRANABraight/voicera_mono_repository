"""Tests for the Bhashini Bhili NVCF Triton gRPC streaming STT service."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import numpy as np
import pytest

from apps.providers.adapters.bhashini import bhili_stt as bhili_stt_mod
from apps.providers.adapters.bhashini.bhili_stt import (
    BhashiniBhiliSTTService,
    build_triton_inputs,
)


def _make_service(**overrides):
    kwargs = {"auth_token": "tok", "function_id": "fn-1", "language": "bhb"}
    kwargs.update(overrides)
    svc = BhashiniBhiliSTTService(**kwargs)
    svc._sample_rate = svc._init_sample_rate or 16000
    svc.push_frame = AsyncMock()
    svc.start_processing_metrics = AsyncMock()
    svc.stop_processing_metrics = AsyncMock()
    return svc


def _loud_chunk(n_samples: int = 320, amplitude: int = 20000) -> bytes:
    return (np.ones(n_samples, dtype=np.int16) * amplitude).tobytes()


def _silence_chunk(n_samples: int = 320) -> bytes:
    return np.zeros(n_samples, dtype=np.int16).tobytes()


class _OneShotQueue:
    """Replaces ``asyncio.Queue`` for receive-handler tests: yields queued
    items once, then raises ``TimeoutError`` (matching a real empty queue
    after ``asyncio.wait_for``'s timeout) so the handler's loop exits
    deterministically after processing the given items."""

    def __init__(self, items):
        self._items = list(items)

    async def get(self):
        if self._items:
            return self._items.pop(0)
        raise asyncio.TimeoutError()


class _FakeResult:
    def __init__(self, transcript: str, closed: bool):
        self._transcript = transcript.encode("utf-8")
        self._closed = closed

    def as_numpy(self, name: str):
        if name == "PARTIAL_TRANSCRIPT":
            return np.array([self._transcript], dtype=object)
        if name == "SESSION_CLOSED":
            return np.array([self._closed])
        raise KeyError(name)


# --- module-level helpers ---


def test_build_triton_inputs_shape():
    chunk = np.zeros((10,), dtype=np.float32)
    inputs = build_triton_inputs(
        chunk, lang_id="bhb", session_id="s1", is_final=True, hotwords=["a", "b"]
    )
    names = [i.name() for i in inputs]
    assert names == [
        "AUDIO_CHUNK",
        "LANG_ID",
        "SESSION_ID",
        "IS_FINAL",
        "HOTWORD_LIST",
        "HOTWORD_WEIGHT",
        "ALPHA",
        "BETA",
    ]


def test_build_triton_inputs_default_hotwords():
    chunk = np.zeros((4,), dtype=np.float32)
    inputs = build_triton_inputs(chunk, lang_id="bhb", session_id="s1", is_final=False)
    assert inputs is not None  # hotwords=None -> falls back to [""]


# --- construction ---


def test_requires_triton_available(monkeypatch):
    monkeypatch.setattr(bhili_stt_mod, "_TRITON_AVAILABLE", False)
    with pytest.raises(ImportError, match="tritonclient"):
        BhashiniBhiliSTTService(auth_token="tok", function_id="fn")


def test_requires_auth_token():
    with pytest.raises(ValueError, match="requires auth_token"):
        BhashiniBhiliSTTService(auth_token="  ", function_id="fn")


def test_requires_function_id():
    with pytest.raises(ValueError, match="requires function_id"):
        BhashiniBhiliSTTService(auth_token="tok", function_id="  ")


def test_can_generate_metrics_true():
    assert _make_service().can_generate_metrics() is True


@pytest.mark.anyio
async def test_emit_latency_metric_noop_without_callback():
    svc = _make_service()
    await svc._emit_latency_metric("x", 1.0)


@pytest.mark.anyio
async def test_emit_latency_metric_calls_callback():
    calls = []

    async def callback(payload):
        calls.append(payload)

    svc = _make_service(telemetry_callback=callback)
    await svc._emit_latency_metric("first_transcript_ms", 5.0, stage="s")
    assert len(calls) == 1


@pytest.mark.anyio
async def test_emit_latency_metric_swallows_errors():
    async def callback(payload):
        raise RuntimeError("telemetry down")

    svc = _make_service(telemetry_callback=callback)
    await svc._emit_latency_metric("x", 1.0)


def test_grpc_headers_without_version_id():
    svc = _make_service()
    headers = svc._grpc_headers()
    assert headers["authorization"] == "Bearer tok"
    assert "function-version-id" not in headers


def test_grpc_headers_with_version_id():
    svc = _make_service(function_version_id="v2")
    headers = svc._grpc_headers()
    assert headers["function-version-id"] == "v2"


def test_pcm16_to_float32_empty():
    svc = _make_service()
    out = svc._pcm16_to_float32(b"")
    assert out.size == 0


def test_pcm16_to_float32_nonempty():
    svc = _make_service()
    pcm = np.array([16384], dtype=np.int16).tobytes()
    out = svc._pcm16_to_float32(pcm)
    assert out[0] == pytest.approx(0.5, abs=0.01)


@pytest.mark.anyio
async def test_prepare_audio_resamples_when_rate_mismatched():
    svc = _make_service(input_sample_rate=8000)
    resampled = np.array([1000], dtype=np.int16).tobytes()

    async def fake_resample(audio, in_rate, out_rate):
        return resampled

    svc._resampler.resample = fake_resample
    out = await svc._prepare_audio(_loud_chunk())
    assert out.size == 1


@pytest.mark.anyio
async def test_prepare_audio_passthrough_when_rate_matches():
    svc = _make_service(input_sample_rate=16000)
    out = await svc._prepare_audio(_loud_chunk(4))
    assert out.size == 4


# --- _on_stream_result / _stream_lock_or_create ---


@pytest.mark.anyio
async def test_on_stream_result_noop_without_loop_or_queue():
    svc = _make_service()
    svc._on_stream_result("result", None)  # no loop/queue set: silently ignored


@pytest.mark.anyio
async def test_on_stream_result_enqueues_when_ready():
    svc = _make_service()
    svc._loop = asyncio.get_event_loop()
    svc._result_queue = asyncio.Queue()
    svc._on_stream_result("res", None)
    await asyncio.sleep(0)  # let call_soon_threadsafe run
    item = await svc._result_queue.get()
    assert item == ("res", None)


def test_stream_lock_or_create_reuses_lock():
    svc = _make_service()
    lock1 = svc._stream_lock_or_create()
    lock2 = svc._stream_lock_or_create()
    assert lock1 is lock2


# --- _tear_down_stream ---


@pytest.mark.anyio
async def test_tear_down_stream_without_client_or_receiver():
    svc = _make_service()
    await svc._tear_down_stream()
    assert svc._closed is True
    assert svc._stream_broken is True


@pytest.mark.anyio
async def test_tear_down_stream_swallows_stop_stream_error():
    svc = _make_service()

    class _BadClient:
        def stop_stream(self):
            raise RuntimeError("stop failed")

    svc._client = _BadClient()
    await svc._tear_down_stream(cancel_receiver=False)
    assert svc._client is None


@pytest.mark.anyio
async def test_tear_down_stream_cancels_receiver_task():
    svc = _make_service()

    async def never_ending():
        await asyncio.Event().wait()

    svc._receiver_task = asyncio.create_task(never_ending())
    await svc._tear_down_stream(cancel_receiver=True)
    assert svc._receiver_task is None


@pytest.mark.anyio
async def test_tear_down_stream_skips_cancel_when_receiver_is_current_task():
    svc = _make_service()

    async def self_tear_down():
        await svc._tear_down_stream(cancel_receiver=True)

    task = asyncio.create_task(self_tear_down())
    svc._receiver_task = task
    await task
    # The task completed normally (didn't cancel itself mid-flight).
    assert task.cancelled() is False


# --- _open_stream ---


@pytest.mark.anyio
async def test_open_stream_returns_false_when_disabled():
    svc = _make_service()
    svc._disabled = True
    assert await svc._open_stream() is False


@pytest.mark.anyio
async def test_open_stream_returns_true_when_already_open():
    svc = _make_service()
    svc._client = object()
    svc._stream_broken = False
    assert await svc._open_stream() is True


@pytest.mark.anyio
async def test_open_stream_tears_down_broken_stream_before_reopening(monkeypatch):
    svc = _make_service()
    svc._client = object()
    svc._stream_broken = True

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        def start_stream(self, callback, headers):
            pass

    monkeypatch.setattr(bhili_stt_mod, "grpcclient", type("G", (), {"InferenceServerClient": _FakeClient}))
    assert await svc._open_stream() is True
    assert svc._stream_broken is False


@pytest.mark.anyio
async def test_open_stream_success(monkeypatch):
    svc = _make_service()

    class _FakeClient:
        def __init__(self, *a, **k):
            self.started = False

        def start_stream(self, callback, headers):
            self.started = True

        def stop_stream(self):
            pass

    monkeypatch.setattr(bhili_stt_mod, "grpcclient", type("G", (), {"InferenceServerClient": _FakeClient}))
    assert await svc._open_stream() is True
    assert svc._client is not None
    assert svc._session_id
    await svc._tear_down_stream()


@pytest.mark.anyio
async def test_open_stream_failure_tears_down_and_returns_false(monkeypatch):
    svc = _make_service()

    class _FailingClient:
        def __init__(self, *a, **k):
            raise RuntimeError("cannot connect")

    monkeypatch.setattr(
        bhili_stt_mod, "grpcclient", type("G", (), {"InferenceServerClient": _FailingClient})
    )
    assert await svc._open_stream() is False
    assert svc._client is None


# --- _receive_handler ---


@pytest.mark.anyio
async def test_receive_handler_timeout_breaks_loop(monkeypatch):
    svc = _make_service()
    svc._result_queue = asyncio.Queue()

    async def fake_wait_for(coro, timeout):
        coro.close()
        raise asyncio.TimeoutError()

    monkeypatch.setattr(bhili_stt_mod.asyncio, "wait_for", fake_wait_for)
    await svc._receive_handler()
    svc.push_frame.assert_not_called()


@pytest.mark.anyio
async def test_receive_handler_error_result_pushes_error_and_tears_down():
    svc = _make_service()
    svc._result_queue = asyncio.Queue()
    await svc._result_queue.put((None, "server-side error"))
    svc._tear_down_stream = AsyncMock()
    await svc._receive_handler()
    svc.push_frame.assert_awaited_once()
    frame = svc.push_frame.await_args.args[0]
    assert "receive loop failed" in frame.error
    svc._tear_down_stream.assert_awaited_once()


@pytest.mark.anyio
async def test_receive_handler_error_result_swallowed_when_closed():
    svc = _make_service()
    svc._result_queue = asyncio.Queue()
    svc._closed = True
    await svc._result_queue.put((None, "server-side error"))
    await svc._receive_handler()
    svc.push_frame.assert_not_called()


@pytest.mark.anyio
async def test_receive_handler_empty_transcript_closed_sets_event():
    svc = _make_service()
    svc._final_transcript_event = asyncio.Event()
    svc._result_queue = _OneShotQueue([(_FakeResult("", True), None)])
    await svc._receive_handler()
    assert svc._final_transcript_event.is_set()
    svc.push_frame.assert_not_called()


@pytest.mark.anyio
async def test_receive_handler_interim_transcript_pushes_frame():
    svc = _make_service()
    svc._result_queue = _OneShotQueue([(_FakeResult("hel", False), None)])
    await svc._receive_handler()
    svc.push_frame.assert_awaited_once()
    frame = svc.push_frame.await_args.args[0]
    assert type(frame).__name__ == "InterimTranscriptionFrame"


@pytest.mark.anyio
async def test_receive_handler_final_transcript_pushes_frame_and_metrics():
    svc = _make_service()
    svc._final_transcript_event = asyncio.Event()
    svc._speech_started_at = 0.0
    svc._segment_started_at = 0.0
    calls = []

    async def callback(payload):
        calls.append(payload["metric"])

    svc._telemetry_callback = callback
    svc._result_queue = _OneShotQueue([(_FakeResult("hello world", True), None)])
    await svc._receive_handler()
    assert svc._final_transcript_event.is_set()
    svc.push_frame.assert_awaited_once()
    frame = svc.push_frame.await_args.args[0]
    assert type(frame).__name__ == "TranscriptionFrame"
    assert frame.text == "hello world"
    svc.stop_processing_metrics.assert_awaited_once()
    assert "first_transcript_ms" in calls
    assert "final_transcript_ms" in calls


@pytest.mark.anyio
async def test_receive_handler_reraises_cancelled(monkeypatch):
    svc = _make_service()
    svc._result_queue = asyncio.Queue()

    async def fake_wait_for(coro, timeout):
        coro.close()
        raise asyncio.CancelledError()

    monkeypatch.setattr(bhili_stt_mod.asyncio, "wait_for", fake_wait_for)
    with pytest.raises(asyncio.CancelledError):
        await svc._receive_handler()


# --- _send_chunk ---


@pytest.mark.anyio
async def test_send_chunk_raises_when_stream_broken():
    svc = _make_service()
    svc._stream_broken = True
    with pytest.raises(RuntimeError, match="not connected"):
        await svc._send_chunk(_loud_chunk(), is_final=False)


@pytest.mark.anyio
async def test_send_chunk_raises_when_no_client():
    svc = _make_service()
    with pytest.raises(RuntimeError, match="not connected"):
        await svc._send_chunk(_loud_chunk(), is_final=False)


@pytest.mark.anyio
async def test_send_chunk_noop_when_prepared_audio_empty():
    svc = _make_service()
    svc._client = AsyncMock()

    async def fake_prepare(chunk):
        return np.array([], dtype=np.float32)

    svc._prepare_audio = fake_prepare
    await svc._send_chunk(_loud_chunk(), is_final=False)
    assert svc._chunk_seq == 0


@pytest.mark.anyio
async def test_send_chunk_sends_via_async_stream_infer():
    svc = _make_service()

    class _FakeClient:
        def __init__(self):
            self.calls = []

        def async_stream_infer(self, **kwargs):
            self.calls.append(kwargs)

    svc._client = _FakeClient()
    await svc._send_chunk(_loud_chunk(), is_final=True)
    assert svc._chunk_seq == 1
    assert svc._client.calls[0]["model_name"] == svc._model


@pytest.mark.anyio
async def test_send_chunk_tears_down_on_invalid_state_error():
    svc = _make_service()

    class _FailingClient:
        def async_stream_infer(self, **kwargs):
            raise RuntimeError("stream is no longer in valid state")

    svc._client = _FailingClient()
    svc._tear_down_stream = AsyncMock()
    with pytest.raises(RuntimeError):
        await svc._send_chunk(_loud_chunk(), is_final=False)
    svc._tear_down_stream.assert_awaited_once()


@pytest.mark.anyio
async def test_send_chunk_reraises_other_errors_without_teardown():
    svc = _make_service()

    class _FailingClient:
        def async_stream_infer(self, **kwargs):
            raise RuntimeError("totally different error")

    svc._client = _FailingClient()
    svc._tear_down_stream = AsyncMock()
    with pytest.raises(RuntimeError, match="totally different"):
        await svc._send_chunk(_loud_chunk(), is_final=False)
    svc._tear_down_stream.assert_not_called()


@pytest.mark.anyio
async def test_send_chunk_raises_when_stream_broken_inside_lock():
    # Simulates the stream breaking between the outer check and acquiring
    # the lock (a real race under concurrent chunk sends).
    svc = _make_service()
    svc._client = object()

    async def fake_prepare(chunk):
        svc._stream_broken = True
        return np.array([1.0], dtype=np.float32)

    svc._prepare_audio = fake_prepare
    with pytest.raises(RuntimeError, match="not connected"):
        await svc._send_chunk(_loud_chunk(), is_final=False)


# --- _close_stream / _finalize_segment ---


@pytest.mark.anyio
async def test_close_stream_resets_flags():
    svc = _make_service()
    svc._stream_broken = True
    svc._closed = True
    await svc._close_stream()
    assert svc._stream_broken is False
    assert svc._closed is False


@pytest.mark.anyio
async def test_finalize_segment_noop_when_not_active():
    svc = _make_service()
    await svc._finalize_segment()


@pytest.mark.anyio
async def test_finalize_segment_returns_on_final_event_set():
    svc = _make_service()
    svc._segment_active = True
    svc._client = object()
    svc._final_transcript_event = asyncio.Event()
    svc._final_transcript_event.set()
    await svc._finalize_segment()
    assert svc._segment_active is False


@pytest.mark.anyio
async def test_finalize_segment_timeout_short_text_dropped():
    svc = _make_service()
    svc._segment_active = True
    svc._client = object()
    svc._final_transcript_event = asyncio.Event()
    svc._latest_transcript_text = "hi"

    async def fake_wait_for(coro, timeout):
        coro.close()
        raise asyncio.TimeoutError()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(bhili_stt_mod.asyncio, "wait_for", fake_wait_for)
        await svc._finalize_segment()
    svc.push_frame.assert_not_called()


@pytest.mark.anyio
async def test_finalize_segment_timeout_promotes_interim():
    svc = _make_service()
    svc._segment_active = True
    svc._client = object()
    svc._final_transcript_event = asyncio.Event()
    svc._latest_transcript_text = "a much longer interim transcript"
    svc._speech_started_at = 0.0

    async def fake_wait_for(coro, timeout):
        coro.close()
        raise asyncio.TimeoutError()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(bhili_stt_mod.asyncio, "wait_for", fake_wait_for)
        await svc._finalize_segment()
    svc.push_frame.assert_awaited_once()
    svc.stop_processing_metrics.assert_awaited_once()


# --- _handle_audio_chunk ---


@pytest.mark.anyio
async def test_handle_audio_chunk_start_failed_resets_vad():
    svc = _make_service()
    svc._open_stream = AsyncMock(return_value=False)
    result = await svc._handle_audio_chunk(_loud_chunk())
    assert result == "START_FAILED"


@pytest.mark.anyio
async def test_handle_audio_chunk_start_success_sends_preroll_and_audio():
    svc = _make_service()
    svc._open_stream = AsyncMock(return_value=True)
    svc._send_chunk = AsyncMock()
    result = await svc._handle_audio_chunk(_loud_chunk(), pre_roll_bytes=b"\x00\x01")
    assert result == "START"
    assert svc._segment_active is True
    assert svc._send_chunk.await_count == 2


@pytest.mark.anyio
async def test_handle_audio_chunk_continue_when_active_and_not_broken():
    svc = _make_service()
    svc._vad.is_speaking = True
    svc._segment_active = True
    svc._stream_broken = False
    svc._send_chunk = AsyncMock()
    result = await svc._handle_audio_chunk(_loud_chunk())
    assert result == "CONTINUE"
    svc._send_chunk.assert_awaited_once()


@pytest.mark.anyio
async def test_handle_audio_chunk_stop_sends_final_chunk_and_tears_down():
    svc = _make_service()
    svc._vad.is_speaking = True
    svc._vad.silence_run_ms = 10_000
    svc._segment_active = True
    svc._client = object()
    svc._stream_broken = False
    svc._send_chunk = AsyncMock()
    svc._finalize_segment = AsyncMock()
    svc._close_stream = AsyncMock()
    result = await svc._handle_audio_chunk(_silence_chunk())
    assert result == "STOP"
    svc._send_chunk.assert_awaited_once()
    svc._finalize_segment.assert_awaited_once()
    svc._close_stream.assert_awaited_once()


@pytest.mark.anyio
async def test_handle_audio_chunk_stop_skips_final_chunk_when_not_active():
    svc = _make_service()
    svc._vad.is_speaking = True
    svc._vad.silence_run_ms = 10_000
    svc._segment_active = False
    svc._send_chunk = AsyncMock()
    svc._finalize_segment = AsyncMock()
    svc._close_stream = AsyncMock()
    result = await svc._handle_audio_chunk(_silence_chunk())
    assert result == "STOP"
    svc._send_chunk.assert_not_called()


@pytest.mark.anyio
async def test_handle_audio_chunk_idle_passthrough():
    svc = _make_service()
    result = await svc._handle_audio_chunk(_silence_chunk())
    assert result == "IDLE"


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
async def test_stop_with_active_segment_finalizes():
    from pipecat.frames.frames import EndFrame

    svc = _make_service()
    svc._segment_active = True
    svc._finalize_segment = AsyncMock()
    svc._close_stream = AsyncMock()
    await svc.stop(EndFrame())
    svc._finalize_segment.assert_awaited_once()
    svc._close_stream.assert_awaited_once()


@pytest.mark.anyio
async def test_stop_without_active_segment_skips_finalize():
    from pipecat.frames.frames import EndFrame

    svc = _make_service()
    svc._finalize_segment = AsyncMock()
    svc._close_stream = AsyncMock()
    await svc.stop(EndFrame())
    svc._finalize_segment.assert_not_called()
    svc._close_stream.assert_awaited_once()


@pytest.mark.anyio
async def test_cancel_with_active_segment_finalizes():
    from pipecat.frames.frames import CancelFrame

    svc = _make_service()
    svc._segment_active = True
    svc._finalize_segment = AsyncMock()
    svc._close_stream = AsyncMock()
    await svc.cancel(CancelFrame())
    svc._finalize_segment.assert_awaited_once()


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
async def test_run_stt_yields_error_frame_and_resets_on_exception():
    svc = _make_service()
    svc._recompute_audio_params()
    svc._segment_active = True
    svc._stream_broken = True
    svc._closed = True
    svc._tear_down_stream = AsyncMock()

    async def raise_error(chunk, pre_roll_bytes=b""):
        raise RuntimeError("boom")

    svc._handle_audio_chunk = raise_error
    audio = _loud_chunk(svc._chunk_samples)
    frames = await _collect(svc.run_stt(audio))
    assert len(frames) == 1
    assert "processing failed" in frames[0].error
    assert svc._segment_active is False
    assert svc._stream_broken is False
    assert svc._closed is False
    svc._tear_down_stream.assert_awaited_once()


@pytest.mark.anyio
async def test_run_stt_preroll_buffer_trims_overflow():
    svc = _make_service(chunk_ms=200, input_sample_rate=16000)
    svc._pre_roll_ms = 200
    svc._recompute_audio_params()
    svc._handle_audio_chunk = AsyncMock(return_value="CONTINUE")
    audio = _loud_chunk(svc._chunk_samples * 3)
    await _collect(svc.run_stt(audio))
    assert len(svc._pre_roll_buffer) <= svc._pre_roll_bytes


@pytest.mark.anyio
async def test_run_stt_clears_preroll_buffer_when_disabled():
    svc = _make_service(chunk_ms=200, input_sample_rate=16000)
    svc._pre_roll_ms = 0
    svc._recompute_audio_params()
    svc._pre_roll_buffer.extend(b"\x00\x01")
    svc._handle_audio_chunk = AsyncMock(return_value="CONTINUE")
    audio = _loud_chunk(svc._chunk_samples)
    await _collect(svc.run_stt(audio))
    assert svc._pre_roll_buffer == bytearray()


@pytest.mark.anyio
async def test_set_language_and_model():
    svc = _make_service()
    await svc.set_language("hi")
    assert svc._language == "hi"
    await svc.set_model("new-model")
    assert svc._model == "new-model"
