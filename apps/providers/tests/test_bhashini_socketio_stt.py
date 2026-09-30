"""Tests for the Bhashini Socket.IO pipeline STT service."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from apps.providers.adapters.bhashini.socketio_stt import (
    BhashiniSocketIOSTTService,
    _extract_transcript_text,
)


def _make_service(**overrides):
    kwargs = {"api_key": "key", "language": "en"}
    kwargs.update(overrides)
    svc = BhashiniSocketIOSTTService(**kwargs)
    svc._sample_rate = svc._init_sample_rate or 16000
    svc.push_frame = AsyncMock()
    svc.start_processing_metrics = AsyncMock()
    svc.stop_processing_metrics = AsyncMock()
    return svc


def _loud_chunk(n_samples: int = 320, amplitude: int = 20000):
    import numpy as np

    return (np.ones(n_samples, dtype=np.int16) * amplitude).tobytes()


def _silence_chunk(n_samples: int = 320):
    import numpy as np

    return np.zeros(n_samples, dtype=np.int16).tobytes()


# --- _extract_transcript_text ---


def test_extract_transcript_text_empty_list():
    assert _extract_transcript_text([], interim=True) == ""
    assert _extract_transcript_text([], interim=False) == ""


def test_extract_transcript_text_interim_uses_first_chunk():
    output = [{"source": "hello"}, {"source": "world"}]
    assert _extract_transcript_text(output, interim=True) == "hello"


def test_extract_transcript_text_final_joins_chunks():
    output = [{"source": "hello"}, {"source": "world"}]
    assert _extract_transcript_text(output, interim=False) == "hello. world"


def test_extract_transcript_text_falls_back_to_target():
    output = [{"target": "namaste"}]
    assert _extract_transcript_text(output, interim=True) == "namaste"


def test_extract_transcript_text_skips_empty_chunks_in_final():
    output = [{"source": ""}, {"source": "hi"}, {"source": "  "}]
    assert _extract_transcript_text(output, interim=False) == "hi"


# --- construction / small helpers ---


def test_requires_api_key():
    with pytest.raises(ValueError, match="requires api_key"):
        BhashiniSocketIOSTTService(api_key="  ")


def test_can_generate_metrics_true():
    assert _make_service().can_generate_metrics() is True


def test_build_task_sequence_shape():
    svc = _make_service(service_id="svc-1", language="en")
    seq = svc._build_task_sequence()
    assert seq[0]["taskType"] == "asr"
    assert seq[0]["config"]["serviceId"] == "svc-1"
    assert seq[0]["config"]["language"]["sourceLanguage"] == "en"


def test_build_streaming_config_shape():
    svc = _make_service(response_frequency_in_secs=1.5)
    cfg = svc._build_streaming_config()
    assert cfg["responseFrequencyInSecs"] == 1.5
    assert cfg["responseTaskSequenceDepth"] == 1


@pytest.mark.anyio
async def test_set_language_and_model():
    svc = _make_service()
    await svc.set_language("hi")
    assert svc._language == "hi"
    await svc.set_model("new-service")
    assert svc._service_id == "new-service"


# --- telemetry ---


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


# --- _register_handlers via a fake socketio.AsyncClient ---


class _FakeSio:
    def __init__(self):
        self.handlers = {}
        self.emitted = []
        self.disconnect_called = False

    def event(self, fn):
        self.handlers[fn.__name__] = fn
        return fn

    def on(self, name):
        def deco(fn):
            self.handlers[name] = fn
            return fn

        return deco

    def get_sid(self):
        return "sid-1"

    async def emit(self, event, data=None):
        self.emitted.append((event, data))

    async def disconnect(self):
        self.disconnect_called = True


@pytest.mark.anyio
async def test_registered_connect_handler_emits_start():
    svc = _make_service()
    sio = _FakeSio()
    svc._register_handlers(sio)
    await sio.handlers["connect"]()
    assert sio.emitted[0][0] == "start"


@pytest.mark.anyio
async def test_registered_connect_error_handler_disables_and_sets_ready():
    svc = _make_service()
    sio = _FakeSio()
    svc._register_handlers(sio)
    svc._ready_event = asyncio.Event()
    await sio.handlers["connect_error"]("bad auth")
    assert svc._disabled is True
    assert svc._ready_event.is_set()


@pytest.mark.anyio
async def test_registered_connect_error_handler_noop_when_no_ready_event():
    svc = _make_service()
    sio = _FakeSio()
    svc._register_handlers(sio)
    svc._ready_event = None
    await sio.handlers["connect_error"]("bad auth")  # no ready_event: no crash
    assert svc._disabled is True


@pytest.mark.anyio
async def test_registered_ready_handler_sets_stream_active_and_event():
    svc = _make_service()
    sio = _FakeSio()
    svc._register_handlers(sio)
    svc._ready_event = asyncio.Event()
    svc._is_stream_inactive = True
    await sio.handlers["ready"]()
    assert svc._is_stream_inactive is False
    assert svc._ready_event.is_set()


@pytest.mark.anyio
async def test_registered_response_handler_delegates():
    svc = _make_service()
    sio = _FakeSio()
    svc._register_handlers(sio)
    svc._handle_response = AsyncMock()
    await sio.handlers["response"]({"pipelineResponse": []}, None)
    svc._handle_response.assert_awaited_once_with({"pipelineResponse": []}, {})


@pytest.mark.anyio
async def test_registered_abort_handler_disables():
    svc = _make_service()
    sio = _FakeSio()
    svc._register_handlers(sio)
    await sio.handlers["abort"]("server aborted")
    assert svc._disabled is True


@pytest.mark.anyio
async def test_registered_terminate_handler_disconnects():
    svc = _make_service()
    sio = _FakeSio()
    svc._register_handlers(sio)
    await sio.handlers["terminate"]()
    assert sio.disconnect_called is True


@pytest.mark.anyio
async def test_registered_disconnect_handler_clears_connected():
    svc = _make_service()
    sio = _FakeSio()
    svc._register_handlers(sio)
    svc._connected = True
    await sio.handlers["disconnect"]()
    assert svc._connected is False


# --- _connect / _disconnect ---


@pytest.mark.anyio
async def test_connect_noop_when_already_connected():
    svc = _make_service()
    svc._connected = True
    assert await svc._connect() is True


@pytest.mark.anyio
async def test_connect_noop_when_disabled():
    svc = _make_service()
    svc._disabled = True
    assert await svc._connect() is False


@pytest.mark.anyio
async def test_connect_success(monkeypatch):
    svc = _make_service()

    class _FakeAsyncClient(_FakeSio):
        def __init__(self, *a, **k):
            super().__init__()

        async def connect(self, **kwargs):
            # Simulate the server replying "ready" right after connecting.
            await self.handlers["ready"]()

    import apps.providers.adapters.bhashini.socketio_stt as socketio_stt_mod

    monkeypatch.setattr(socketio_stt_mod.socketio, "AsyncClient", _FakeAsyncClient)
    assert await svc._connect() is True
    assert svc._connected is True


@pytest.mark.anyio
async def test_connect_returns_false_when_disabled_mid_connect(monkeypatch):
    svc = _make_service()

    class _FakeAsyncClient(_FakeSio):
        def __init__(self, *a, **k):
            super().__init__()

        async def connect(self, **kwargs):
            await self.handlers["connect_error"]("nope")

    import apps.providers.adapters.bhashini.socketio_stt as socketio_stt_mod

    monkeypatch.setattr(socketio_stt_mod.socketio, "AsyncClient", _FakeAsyncClient)
    assert await svc._connect() is False


@pytest.mark.anyio
async def test_connect_failure_disables_service(monkeypatch):
    svc = _make_service()

    class _FailingAsyncClient(_FakeSio):
        def __init__(self, *a, **k):
            super().__init__()

        async def connect(self, **kwargs):
            raise RuntimeError("connect failed")

    import apps.providers.adapters.bhashini.socketio_stt as socketio_stt_mod

    monkeypatch.setattr(socketio_stt_mod.socketio, "AsyncClient", _FailingAsyncClient)
    assert await svc._connect() is False
    assert svc._disabled is True
    assert svc._connected is False


@pytest.mark.anyio
async def test_disconnect_when_never_connected_is_noop():
    svc = _make_service()
    await svc._disconnect()
    assert svc._closed is True


@pytest.mark.anyio
async def test_disconnect_closes_socket_and_swallows_errors():
    svc = _make_service()

    class _BadSio:
        async def disconnect(self):
            raise RuntimeError("disconnect failed")

    svc._sio = _BadSio()
    svc._connected = True
    await svc._disconnect()
    assert svc._sio is None
    assert svc._connected is False
    assert svc._ready_event is None


# --- _handle_response ---


@pytest.mark.anyio
async def test_handle_response_non_dict_is_logged_not_raised():
    svc = _make_service()
    await svc._handle_response("not a dict", {})
    svc.push_frame.assert_not_called()


@pytest.mark.anyio
async def test_handle_response_empty_pipeline_response():
    svc = _make_service()
    await svc._handle_response({"pipelineResponse": []}, {})
    svc.push_frame.assert_not_called()


@pytest.mark.anyio
async def test_handle_response_malformed_output():
    svc = _make_service()
    await svc._handle_response(
        {"pipelineResponse": [{"output": "not-a-list"}]}, {}
    )
    svc.push_frame.assert_not_called()


@pytest.mark.anyio
async def test_handle_response_empty_text_returns_early():
    svc = _make_service()
    await svc._handle_response(
        {"pipelineResponse": [{"output": [{"source": "  "}]}]}, {}
    )
    svc.push_frame.assert_not_called()


@pytest.mark.anyio
async def test_handle_response_interim_pushes_interim_frame():
    svc = _make_service()
    svc._speech_started_at = 0.0
    calls = []

    async def callback(payload):
        calls.append(payload["metric"])

    svc._telemetry_callback = callback
    await svc._handle_response(
        {"pipelineResponse": [{"output": [{"source": "hel"}]}]},
        {"isIntermediateResult": True},
    )
    svc.push_frame.assert_awaited_once()
    frame = svc.push_frame.await_args.args[0]
    assert type(frame).__name__ == "InterimTranscriptionFrame"
    assert "first_transcript_ms" in calls


@pytest.mark.anyio
async def test_handle_response_final_pushes_transcription_frame():
    svc = _make_service()
    svc._speech_started_at = 0.0
    await svc._handle_response(
        {"pipelineResponse": [{"output": [{"source": "hello"}]}]},
        {"isIntermediateResult": False},
    )
    svc.push_frame.assert_awaited_once()
    frame = svc.push_frame.await_args.args[0]
    assert type(frame).__name__ == "TranscriptionFrame"
    assert frame.text == "hello"
    svc.stop_processing_metrics.assert_awaited_once()


@pytest.mark.anyio
async def test_handle_response_swallows_unexpected_exception():
    svc = _make_service()
    svc.push_frame = AsyncMock(side_effect=RuntimeError("push boom"))
    # Should not propagate: caught by the handler's own try/except.
    await svc._handle_response(
        {"pipelineResponse": [{"output": [{"source": "hello"}]}]},
        {"isIntermediateResult": False},
    )


# --- _resample_audio / _emit_audio / _transmit_end_of_segment ---


@pytest.mark.anyio
async def test_resample_audio_passthrough_when_rate_matches():
    svc = _make_service(input_sample_rate=16000)
    audio = _loud_chunk()
    assert await svc._resample_audio(audio) == audio


@pytest.mark.anyio
async def test_resample_audio_resamples_when_rate_differs():
    svc = _make_service(input_sample_rate=8000)
    resampled = b"\x01\x02"

    async def fake_resample(audio, in_rate, out_rate):
        return resampled

    svc._resampler.resample = fake_resample
    assert await svc._resample_audio(_loud_chunk()) == resampled


@pytest.mark.anyio
async def test_emit_audio_noop_when_not_connected():
    svc = _make_service()
    await svc._emit_audio(_loud_chunk())  # no sio: no-op


@pytest.mark.anyio
async def test_emit_audio_noop_when_disabled():
    svc = _make_service()
    svc._sio = AsyncMock()
    svc._connected = True
    svc._disabled = True
    await svc._emit_audio(_loud_chunk())
    svc._sio.emit.assert_not_called()


@pytest.mark.anyio
async def test_emit_audio_noop_when_resampled_empty():
    svc = _make_service(input_sample_rate=8000)
    svc._sio = AsyncMock()
    svc._connected = True

    async def fake_resample(audio, in_rate, out_rate):
        return b""

    svc._resampler.resample = fake_resample
    await svc._emit_audio(_loud_chunk())
    svc._sio.emit.assert_not_called()


@pytest.mark.anyio
async def test_emit_audio_sends_data_event():
    svc = _make_service(input_sample_rate=16000)
    svc._sio = AsyncMock()
    svc._connected = True
    svc._is_speaking = True
    await svc._emit_audio(_loud_chunk())
    svc._sio.emit.assert_called_once()
    assert svc._sio.emit.call_args.args[0] == "data"


@pytest.mark.anyio
async def test_transmit_end_of_segment_noop_when_not_connected():
    svc = _make_service()
    await svc._transmit_end_of_segment()  # no sio: no-op


@pytest.mark.anyio
async def test_transmit_end_of_segment_emits_twice():
    svc = _make_service()
    svc._sio = AsyncMock()
    svc._connected = True
    svc._is_speaking = True
    await svc._transmit_end_of_segment()
    assert svc._sio.emit.await_count == 2
    assert svc._is_stream_inactive is True


# --- _handle_audio_chunk ---


@pytest.mark.anyio
async def test_handle_audio_chunk_start_failed_when_connect_fails():
    svc = _make_service()
    svc._connect = AsyncMock(return_value=False)
    result = await svc._handle_audio_chunk(_loud_chunk())
    assert result == "START_FAILED"


@pytest.mark.anyio
async def test_handle_audio_chunk_start_success_emits_preroll_and_audio():
    svc = _make_service()
    svc._connect = AsyncMock(return_value=True)
    svc._emit_audio = AsyncMock()
    result = await svc._handle_audio_chunk(_loud_chunk(), pre_roll_bytes=b"\x00\x01")
    assert result == "START"
    assert svc._segment_active is True
    assert svc._emit_audio.await_count == 2


@pytest.mark.anyio
async def test_handle_audio_chunk_start_reuses_existing_connection():
    svc = _make_service()
    svc._connected = True
    svc._connect = AsyncMock()
    svc._emit_audio = AsyncMock()
    await svc._handle_audio_chunk(_loud_chunk())
    svc._connect.assert_not_called()


@pytest.mark.anyio
async def test_handle_audio_chunk_continue_when_active_and_speaking():
    svc = _make_service()
    svc._vad.is_speaking = True
    svc._segment_active = True
    svc._is_speaking = True
    svc._emit_audio = AsyncMock()
    result = await svc._handle_audio_chunk(_loud_chunk())
    assert result == "CONTINUE"
    svc._emit_audio.assert_awaited_once()


@pytest.mark.anyio
async def test_handle_audio_chunk_stop_transmits_end_of_segment():
    svc = _make_service()
    svc._vad.is_speaking = True
    svc._vad.silence_run_ms = 10_000
    svc._transmit_end_of_segment = AsyncMock()
    result = await svc._handle_audio_chunk(_silence_chunk())
    assert result == "STOP"
    svc._transmit_end_of_segment.assert_awaited_once()
    assert svc._segment_active is False


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
async def test_stop_no_segment_just_disconnects():
    from pipecat.frames.frames import EndFrame

    svc = _make_service()
    svc._disconnect = AsyncMock()
    await svc.stop(EndFrame())
    svc._disconnect.assert_awaited_once()


@pytest.mark.anyio
async def test_stop_with_active_segment_transmits_end():
    from pipecat.frames.frames import EndFrame

    svc = _make_service()
    svc._segment_active = True
    svc._transmit_end_of_segment = AsyncMock()
    svc._disconnect = AsyncMock()
    await svc.stop(EndFrame())
    svc._transmit_end_of_segment.assert_awaited_once()


@pytest.mark.anyio
async def test_cancel_with_active_segment_transmits_end():
    from pipecat.frames.frames import CancelFrame

    svc = _make_service()
    svc._segment_active = True
    svc._transmit_end_of_segment = AsyncMock()
    svc._disconnect = AsyncMock()
    await svc.cancel(CancelFrame())
    svc._transmit_end_of_segment.assert_awaited_once()


@pytest.mark.anyio
async def test_cancel_no_segment_just_disconnects():
    from pipecat.frames.frames import CancelFrame

    svc = _make_service()
    svc._disconnect = AsyncMock()
    await svc.cancel(CancelFrame())
    svc._disconnect.assert_awaited_once()


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
async def test_run_stt_yields_error_frame_on_exception():
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
