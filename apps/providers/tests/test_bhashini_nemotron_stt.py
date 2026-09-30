"""Tests for the Bhashini NVCF Nemotron Indic gRPC streaming STT service."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import grpc
import pytest

from apps.providers.adapters.bhashini import asr_pb2
from apps.providers.adapters.bhashini.nemotron_stt import (
    BhashiniNemotronSTTService,
    _strip_bearer,
)


def _make_service(**overrides):
    kwargs = {"auth_token": "tok", "function_id": "fn-1", "language": "hi"}
    kwargs.update(overrides)
    svc = BhashiniNemotronSTTService(**kwargs)
    svc._sample_rate = svc._init_sample_rate or 16000
    svc.push_frame = AsyncMock()
    svc.start_ttfb_metrics = AsyncMock()
    svc.stop_ttfb_metrics = AsyncMock()
    svc.start_processing_metrics = AsyncMock()
    svc.stop_processing_metrics = AsyncMock()
    return svc


def test_strip_bearer_removes_prefix():
    assert _strip_bearer("Bearer abc123") == "abc123"
    assert _strip_bearer("bearer abc123") == "abc123"
    assert _strip_bearer("abc123") == "abc123"
    assert _strip_bearer("  abc123  ") == "abc123"


def test_requires_auth_token():
    with pytest.raises(ValueError, match="requires auth_token"):
        BhashiniNemotronSTTService(auth_token="   ", function_id="fn")


def test_requires_function_id():
    with pytest.raises(ValueError, match="requires function_id"):
        BhashiniNemotronSTTService(auth_token="tok", function_id="  ")


def test_can_generate_metrics_true():
    assert _make_service().can_generate_metrics() is True


def test_metadata_includes_bearer_token():
    svc = _make_service()
    meta = dict(svc._metadata())
    assert meta["authorization"] == "Bearer tok"
    assert meta["function-id"] == "fn-1"


def test_streaming_config_shape():
    svc = _make_service(language="hi")
    cfg = svc._streaming_config()
    assert cfg.language == "hi"
    assert cfg.sample_rate_hz == 16000


@pytest.mark.anyio
async def test_enqueue_raises_when_not_connected():
    svc = _make_service()
    with pytest.raises(RuntimeError, match="not connected"):
        await svc._enqueue(b"\x00\x01")


@pytest.mark.anyio
async def test_enqueue_puts_item_on_queue():
    svc = _make_service()
    svc._outbound = asyncio.Queue()
    await svc._enqueue(b"\x00\x01")
    assert await svc._outbound.get() == b"\x00\x01"


@pytest.mark.anyio
async def test_request_generator_yields_config_then_items():
    from apps.providers.adapters.bhashini.nemotron_stt import _CLOSE, _COMMIT

    svc = _make_service()
    svc._outbound = asyncio.Queue()
    await svc._outbound.put(b"\x01\x02")
    await svc._outbound.put(_COMMIT)
    await svc._outbound.put(_CLOSE)

    items = [item async for item in svc._request_generator()]
    assert items[0].config.language == "hi"
    assert items[1].audio == b"\x01\x02"
    assert items[2].HasField("commit")
    assert len(items) == 3


@pytest.mark.anyio
async def test_request_generator_yields_replaced_config():
    svc = _make_service()
    svc._outbound = asyncio.Queue()
    new_config = asr_pb2.StreamingConfig(language="or", sample_rate_hz=16000)
    await svc._outbound.put(new_config)
    from apps.providers.adapters.bhashini.nemotron_stt import _CLOSE

    await svc._outbound.put(_CLOSE)

    items = [item async for item in svc._request_generator()]
    assert items[1].config.language == "or"


@pytest.mark.anyio
async def test_request_generator_skips_empty_bytes():
    svc = _make_service()
    svc._outbound = asyncio.Queue()
    await svc._outbound.put(b"")
    from apps.providers.adapters.bhashini.nemotron_stt import _CLOSE

    await svc._outbound.put(_CLOSE)

    items = [item async for item in svc._request_generator()]
    assert len(items) == 1  # only the initial config; empty bytes skipped


class _FakeCall:
    """Async-iterable fake mirroring a gRPC streaming call."""

    def __init__(self, responses=None, *, stay_open: bool = False):
        self._responses = responses or []
        self._stay_open = stay_open

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for r in self._responses:
            yield r
        if self._stay_open:
            await asyncio.Event().wait()


def _started_response(session_id="s1") -> asr_pb2.StreamingResponse:
    return asr_pb2.StreamingResponse(
        started=asr_pb2.SessionStarted(
            session_id=session_id, model_chunk_ms=100, expected_sample_rate_hz=16000, language="hi"
        )
    )


def _transcript_response(text: str, is_final: bool) -> asr_pb2.StreamingResponse:
    return asr_pb2.StreamingResponse(
        transcript=asr_pb2.Transcript(text=text, is_final=is_final)
    )


def _warning_response(code: str = "W1", message: str = "careful") -> asr_pb2.StreamingResponse:
    return asr_pb2.StreamingResponse(
        warning=asr_pb2.Warning(code=code, message=message, samples_dropped=5)
    )


@pytest.mark.anyio
async def test_receive_handler_started_sets_ready():
    svc = _make_service()
    set_calls = []
    original_set = svc._ready.set
    svc._ready.set = lambda: (set_calls.append(True), original_set())[-1]
    call = _FakeCall([_started_response()])
    await svc._receive_handler(call)
    assert set_calls == [True]


@pytest.mark.anyio
async def test_receive_handler_final_transcript_pushes_frame():
    svc = _make_service()
    svc._flush_event = asyncio.Event()
    call = _FakeCall([_transcript_response("hello", True)])
    await svc._receive_handler(call)
    assert svc._pending_final == "hello"
    assert svc._flush_event.is_set()
    svc.push_frame.assert_awaited_once()
    frame = svc.push_frame.await_args.args[0]
    assert type(frame).__name__ == "TranscriptionFrame"
    svc.stop_ttfb_metrics.assert_awaited_once()
    svc.stop_processing_metrics.assert_awaited_once()


@pytest.mark.anyio
async def test_receive_handler_final_empty_text_no_frame():
    svc = _make_service()
    call = _FakeCall([_transcript_response("", True)])
    await svc._receive_handler(call)
    svc.push_frame.assert_not_called()


@pytest.mark.anyio
async def test_receive_handler_interim_transcript_pushes_frame():
    svc = _make_service()
    call = _FakeCall([_transcript_response("hel", False)])
    await svc._receive_handler(call)
    svc.push_frame.assert_awaited_once()
    frame = svc.push_frame.await_args.args[0]
    assert type(frame).__name__ == "InterimTranscriptionFrame"


@pytest.mark.anyio
async def test_receive_handler_interim_empty_text_ignored():
    svc = _make_service()
    call = _FakeCall([_transcript_response("", False)])
    await svc._receive_handler(call)
    svc.push_frame.assert_not_called()


@pytest.mark.anyio
async def test_receive_handler_warning_logged_not_pushed():
    svc = _make_service()
    call = _FakeCall([_warning_response()])
    await svc._receive_handler(call)
    svc.push_frame.assert_not_called()


@pytest.mark.anyio
async def test_receive_handler_grpc_error_pushes_error_frame():
    svc = _make_service()

    class _FakeRpcError(grpc.aio.AioRpcError):
        def __init__(self):
            pass

        def code(self):
            return "UNAVAILABLE"

        def details(self):
            return "down"

    class _RaisingCall:
        def __aiter__(self):
            return self._iter()

        async def _iter(self):
            raise _FakeRpcError()
            yield  # pragma: no cover

    await svc._receive_handler(_RaisingCall())
    svc.push_frame.assert_awaited_once()
    frame = svc.push_frame.await_args.args[0]
    assert "gRPC failed" in frame.error


@pytest.mark.anyio
async def test_receive_handler_grpc_error_swallowed_when_closed():
    svc = _make_service()
    svc._closed = True

    class _FakeRpcError(grpc.aio.AioRpcError):
        def __init__(self):
            pass

        def code(self):
            return "UNAVAILABLE"

        def details(self):
            return "down"

    class _RaisingCall:
        def __aiter__(self):
            return self._iter()

        async def _iter(self):
            raise _FakeRpcError()
            yield  # pragma: no cover

    await svc._receive_handler(_RaisingCall())
    svc.push_frame.assert_not_called()


@pytest.mark.anyio
async def test_receive_handler_generic_exception_pushes_error_frame():
    svc = _make_service()

    class _RaisingCall:
        def __aiter__(self):
            return self._iter()

        async def _iter(self):
            raise RuntimeError("boom")
            yield  # pragma: no cover

    await svc._receive_handler(_RaisingCall())
    svc.push_frame.assert_awaited_once()
    frame = svc.push_frame.await_args.args[0]
    assert "receive failed" in frame.error


@pytest.mark.anyio
async def test_receive_handler_reraises_cancelled():
    svc = _make_service()

    class _CancellingCall:
        def __aiter__(self):
            return self._iter()

        async def _iter(self):
            raise asyncio.CancelledError()
            yield  # pragma: no cover

    with pytest.raises(asyncio.CancelledError):
        await svc._receive_handler(_CancellingCall())


@pytest.mark.anyio
async def test_receive_handler_finally_closes_channel_on_unexpected_death():
    svc = _make_service()
    svc._channel = AsyncMock()

    call = _FakeCall([])  # stream ends immediately: nothing pending
    await svc._receive_handler(call)
    assert svc._channel is None
    assert svc._outbound is None
    assert svc._receiver_task is None


@pytest.mark.anyio
async def test_receive_handler_finally_skips_channel_close_when_closed():
    svc = _make_service()
    svc._closed = True
    fake_channel = AsyncMock()
    svc._channel = fake_channel

    call = _FakeCall([])
    await svc._receive_handler(call)
    assert svc._channel is fake_channel  # untouched: stop()/cancel() own teardown


@pytest.mark.anyio
async def test_connect_success(monkeypatch):
    svc = _make_service()

    import apps.providers.adapters.bhashini.nemotron_stt as nemotron_mod

    fake_call = _FakeCall([_started_response()], stay_open=True)

    class _FakeStub:
        def __init__(self, channel):
            pass

        def StreamingRecognize(self, request_iter, metadata=None):
            return fake_call

    monkeypatch.setattr(nemotron_mod.grpc, "ssl_channel_credentials", lambda: object())
    monkeypatch.setattr(
        nemotron_mod.grpc.aio, "secure_channel", lambda *a, **k: AsyncMock()
    )
    monkeypatch.setattr(nemotron_mod.asr_pb2_grpc, "AsrStub", _FakeStub)

    await svc._connect()
    assert svc._channel is not None
    await svc._disconnect()


@pytest.mark.anyio
async def test_connect_noop_when_already_connected_or_closed():
    svc = _make_service()
    svc._channel = object()
    await svc._connect()  # already connected: no-op

    svc2 = _make_service()
    svc2._closed = True
    await svc2._connect()
    assert svc2._channel is None


@pytest.mark.anyio
async def test_connect_times_out(monkeypatch):
    svc = _make_service()
    import apps.providers.adapters.bhashini.nemotron_stt as nemotron_mod

    fake_call = _FakeCall([])  # never sends "started"

    class _FakeStub:
        def __init__(self, channel):
            pass

        def StreamingRecognize(self, request_iter, metadata=None):
            return fake_call

    monkeypatch.setattr(nemotron_mod.grpc, "ssl_channel_credentials", lambda: object())
    monkeypatch.setattr(
        nemotron_mod.grpc.aio, "secure_channel", lambda *a, **k: AsyncMock()
    )
    monkeypatch.setattr(nemotron_mod.asr_pb2_grpc, "AsrStub", _FakeStub)

    async def fake_wait_for(coro, timeout):
        coro.close()
        raise asyncio.TimeoutError()

    monkeypatch.setattr(nemotron_mod.asyncio, "wait_for", fake_wait_for)

    with pytest.raises(RuntimeError, match="did not become ready"):
        await svc._connect()
    assert svc._channel is None


@pytest.mark.anyio
async def test_disconnect_when_never_connected_is_noop():
    svc = _make_service()
    await svc._disconnect()


@pytest.mark.anyio
async def test_disconnect_swallows_enqueue_and_close_errors():
    svc = _make_service()

    class _BadQueue:
        async def put(self, item):
            raise RuntimeError("queue closed")

    class _BadChannel:
        async def close(self):
            raise RuntimeError("close failed")

    svc._outbound = _BadQueue()
    svc._channel = _BadChannel()
    await svc._disconnect()
    assert svc._channel is None
    assert svc._outbound is None


@pytest.mark.anyio
async def test_disconnect_cancels_pending_task():
    svc = _make_service()

    async def never_ending():
        await asyncio.Event().wait()

    svc._receiver_task = asyncio.create_task(never_ending())
    await svc._disconnect()
    assert svc._receiver_task is None


@pytest.mark.anyio
async def test_flush_utterance_noop_without_channel():
    svc = _make_service()
    await svc._flush_utterance()


@pytest.mark.anyio
async def test_flush_utterance_noop_when_locked():
    svc = _make_service()
    svc._channel = object()
    svc._outbound = asyncio.Queue()
    async with svc._flush_lock:
        await svc._flush_utterance()
    assert svc._outbound.empty()


@pytest.mark.anyio
async def test_flush_utterance_commits_and_waits():
    svc = _make_service()
    svc._channel = object()
    svc._outbound = asyncio.Queue()

    async def set_event_soon():
        await asyncio.sleep(0)
        svc._flush_event.set()

    task = asyncio.create_task(set_event_soon())
    await svc._flush_utterance()
    await task
    assert svc._flush_event is None
    from apps.providers.adapters.bhashini.nemotron_stt import _COMMIT

    assert svc._outbound.get_nowait() is _COMMIT


@pytest.mark.anyio
async def test_flush_utterance_times_out(monkeypatch):
    svc = _make_service()
    svc._channel = object()
    svc._outbound = asyncio.Queue()

    import apps.providers.adapters.bhashini.nemotron_stt as nemotron_mod

    async def fake_wait_for(coro, timeout):
        coro.close()
        raise asyncio.TimeoutError()

    monkeypatch.setattr(nemotron_mod.asyncio, "wait_for", fake_wait_for)
    await svc._flush_utterance()
    assert svc._flush_event is None


@pytest.mark.anyio
async def test_process_frame_vad_started_starts_metrics():
    from pipecat.frames.frames import VADUserStartedSpeakingFrame
    from pipecat.processors.frame_processor import FrameDirection

    svc = _make_service()
    await svc.process_frame(VADUserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    svc.start_ttfb_metrics.assert_awaited_once()
    svc.start_processing_metrics.assert_awaited_once()


@pytest.mark.anyio
async def test_process_frame_vad_stopped_flushes():
    from pipecat.frames.frames import VADUserStoppedSpeakingFrame
    from pipecat.processors.frame_processor import FrameDirection

    svc = _make_service()
    svc._flush_utterance = AsyncMock()
    await svc.process_frame(VADUserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    svc._flush_utterance.assert_awaited_once()


@pytest.mark.anyio
async def test_process_frame_vad_stopped_error_pushes_error_frame():
    from pipecat.frames.frames import VADUserStoppedSpeakingFrame
    from pipecat.processors.frame_processor import FrameDirection

    svc = _make_service()

    async def raise_error():
        raise RuntimeError("commit boom")

    svc._flush_utterance = raise_error
    await svc.process_frame(VADUserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    error_frames = [
        call.args[0]
        for call in svc.push_frame.await_args_list
        if type(call.args[0]).__name__ == "ErrorFrame"
    ]
    assert len(error_frames) == 1
    assert "commit failed" in error_frames[0].error


@pytest.mark.anyio
async def test_start_connects_successfully():
    from pipecat.frames.frames import StartFrame

    svc = _make_service()
    svc._connect = AsyncMock()
    await svc.start(StartFrame())
    svc._connect.assert_awaited_once()
    assert svc._closed is False


@pytest.mark.anyio
async def test_start_pushes_error_on_connect_failure():
    from pipecat.frames.frames import StartFrame

    svc = _make_service()

    async def raise_error():
        raise RuntimeError("connect boom")

    svc._connect = raise_error
    await svc.start(StartFrame())
    svc.push_frame.assert_awaited_once()
    frame = svc.push_frame.await_args.args[0]
    assert "connect failed" in frame.error


@pytest.mark.anyio
async def test_stop_no_channel_just_disconnects():
    from pipecat.frames.frames import EndFrame

    svc = _make_service()
    svc._disconnect = AsyncMock()
    await svc.stop(EndFrame())
    assert svc._closed is True
    svc._disconnect.assert_awaited_once()


@pytest.mark.anyio
async def test_stop_half_close_waits_for_final():
    from pipecat.frames.frames import EndFrame

    svc = _make_service()
    svc._channel = object()
    svc._outbound = asyncio.Queue()
    svc._disconnect = AsyncMock()

    async def set_event_soon():
        await asyncio.sleep(0)
        svc._flush_event.set()

    task = asyncio.create_task(set_event_soon())
    await svc.stop(EndFrame())
    await task
    assert svc._outbound is None
    svc._disconnect.assert_awaited_once()


@pytest.mark.anyio
async def test_stop_half_close_times_out():
    from pipecat.frames.frames import EndFrame

    svc = _make_service()
    svc._channel = object()

    class _SlowQueue:
        async def put(self, item):
            return None

    svc._outbound = _SlowQueue()
    svc._disconnect = AsyncMock()

    async def fake_wait_for(coro, timeout):
        coro.close()
        raise asyncio.TimeoutError()

    import apps.providers.adapters.bhashini.nemotron_stt as nemotron_mod

    original = nemotron_mod.asyncio.wait_for
    nemotron_mod.asyncio.wait_for = fake_wait_for
    try:
        await svc.stop(EndFrame())
    finally:
        nemotron_mod.asyncio.wait_for = original
    svc._disconnect.assert_awaited_once()


@pytest.mark.anyio
async def test_stop_half_close_swallows_enqueue_error():
    from pipecat.frames.frames import EndFrame

    svc = _make_service()
    svc._channel = object()

    class _BadQueue:
        async def put(self, item):
            raise RuntimeError("enqueue boom")

    svc._outbound = _BadQueue()
    svc._disconnect = AsyncMock()
    await svc.stop(EndFrame())
    svc._disconnect.assert_awaited_once()


@pytest.mark.anyio
async def test_cancel_disconnects():
    from pipecat.frames.frames import CancelFrame

    svc = _make_service()
    svc._disconnect = AsyncMock()
    await svc.cancel(CancelFrame())
    assert svc._closed is True
    svc._disconnect.assert_awaited_once()


async def _collect(async_gen):
    out = []
    async for item in async_gen:
        out.append(item)
    return out


@pytest.mark.anyio
async def test_run_stt_empty_or_closed_yields_nothing():
    svc = _make_service()
    assert await _collect(svc.run_stt(b"")) == []
    svc._closed = True
    assert await _collect(svc.run_stt(b"\x00\x01")) == []


@pytest.mark.anyio
async def test_run_stt_connects_when_not_connected():
    svc = _make_service()

    async def fake_connect():
        svc._channel = object()

    svc._connect = fake_connect
    svc._enqueue = AsyncMock()
    frames = await _collect(svc.run_stt(b"\x00\x01"))
    svc._enqueue.assert_awaited_once()
    assert frames == [None]


@pytest.mark.anyio
async def test_run_stt_connect_failure_yields_error():
    svc = _make_service()

    async def raise_error():
        raise RuntimeError("connect boom")

    svc._connect = raise_error
    frames = await _collect(svc.run_stt(b"\x00\x01"))
    assert len(frames) == 1
    assert "connect failed" in frames[0].error


@pytest.mark.anyio
async def test_run_stt_resamples_when_rate_mismatched():
    svc = _make_service()
    svc._channel = object()
    svc._sample_rate = 8000
    svc._enqueue = AsyncMock()
    resampled = b"\x09\x09"

    async def fake_resample(audio, in_rate, out_rate):
        assert in_rate == 8000
        return resampled

    svc._resampler.resample = fake_resample
    frames = await _collect(svc.run_stt(b"\x00\x01"))
    svc._enqueue.assert_awaited_once_with(resampled)
    assert frames == [None]


@pytest.mark.anyio
async def test_run_stt_no_outgoing_audio_yields_nothing():
    svc = _make_service()
    svc._channel = object()
    svc._sample_rate = 8000

    async def fake_resample(audio, in_rate, out_rate):
        return b""

    svc._resampler.resample = fake_resample
    frames = await _collect(svc.run_stt(b"\x00\x01"))
    assert frames == []


@pytest.mark.anyio
async def test_run_stt_send_error_yields_error_frame():
    svc = _make_service()
    svc._channel = object()

    async def raise_error(item):
        raise RuntimeError("send boom")

    svc._enqueue = raise_error
    frames = await _collect(svc.run_stt(b"\x00\x01"))
    assert len(frames) == 1
    assert "send failed" in frames[0].error


@pytest.mark.anyio
async def test_set_language_without_channel():
    svc = _make_service()
    await svc.set_language("OD")
    assert svc._language == "od"


@pytest.mark.anyio
async def test_set_language_with_channel_enqueues_config():
    svc = _make_service()
    svc._channel = object()
    svc._outbound = asyncio.Queue()
    await svc.set_language("OD")
    item = svc._outbound.get_nowait()
    assert item.language == "od"
