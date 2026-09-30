"""Tests for the Indic Nemotron native WebSocket STT service."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from apps.providers.local.indic_nemotron.stt import IndicNemotronSTTService


def _make_service(**overrides):
    kwargs = {"ws_url": "ws://model-server:8100/v1/asr/ws", "language": "hi"}
    kwargs.update(overrides)
    svc = IndicNemotronSTTService(**kwargs)
    svc._sample_rate = svc._init_sample_rate or 16000
    # Bypass full pipeline wiring: capture pushed frames directly like the
    # established kenpath test pattern (push_frame needs no TaskManager then).
    svc.push_frame = AsyncMock()
    svc.start_ttfb_metrics = AsyncMock()
    svc.stop_ttfb_metrics = AsyncMock()
    svc.start_processing_metrics = AsyncMock()
    svc.stop_processing_metrics = AsyncMock()
    return svc


def test_can_generate_metrics_true():
    assert _make_service().can_generate_metrics() is True


def test_connection_url_appends_query():
    svc = _make_service(ws_url="ws://host/v1/asr/ws")
    assert svc._connection_url() == "ws://host/v1/asr/ws?language=hi"


def test_connection_url_appends_with_ampersand_when_query_present():
    svc = _make_service(ws_url="ws://host/v1/asr/ws?foo=bar")
    assert svc._connection_url() == "ws://host/v1/asr/ws?foo=bar&language=hi"


@pytest.mark.anyio
async def test_send_json_raises_when_not_connected():
    svc = _make_service()
    with pytest.raises(RuntimeError, match="not connected"):
        await svc._send_json({"action": "flush_eos"})


@pytest.mark.anyio
async def test_send_pcm_noop_when_not_connected():
    svc = _make_service()
    await svc._send_pcm(b"\x00\x01")  # no websocket: silently returns


@pytest.mark.anyio
async def test_send_pcm_noop_when_empty():
    svc = _make_service()
    svc._websocket = AsyncMock()
    await svc._send_pcm(b"")
    svc._websocket.send.assert_not_called()


@pytest.mark.anyio
async def test_send_json_and_pcm_when_connected():
    svc = _make_service()
    svc._websocket = AsyncMock()
    await svc._send_json({"action": "flush_eos"})
    svc._websocket.send.assert_called_once_with(json.dumps({"action": "flush_eos"}))

    svc._websocket.send.reset_mock()
    await svc._send_pcm(b"\x01\x02")
    svc._websocket.send.assert_called_once_with(b"\x01\x02")


class _FakeWebSocket:
    """Minimal async-iterable fake mirroring the ``websockets`` connection API.

    When ``stay_open`` is set, the async iterator blocks (on an Event that is
    never set) after exhausting ``messages`` instead of finishing — mirrors a
    real long-lived socket, so ``_receive_handler``'s ``finally`` clause
    (which always clears ``_ready``) doesn't race with a caller awaiting
    ``_ready.wait()`` immediately after the message is processed.
    """

    def __init__(self, messages: list[str] | None = None, *, stay_open: bool = False):
        self._messages = messages or []
        self._stay_open = stay_open
        self.closed = False
        self.sent: list = []

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for message in self._messages:
            yield message
        if self._stay_open:
            await asyncio.Event().wait()

    async def send(self, data):
        self.sent.append(data)

    async def close(self):
        self.closed = True


@pytest.mark.anyio
async def test_connect_success(monkeypatch):
    svc = _make_service()
    fake_ws = _FakeWebSocket([json.dumps({"status": "ready"})], stay_open=True)

    async def fake_connect(uri, ping_interval=20, ping_timeout=20):
        return fake_ws

    import apps.providers.local.indic_nemotron.stt as stt_mod

    monkeypatch.setattr(stt_mod.websockets, "connect", fake_connect)
    await svc._connect()
    assert svc._websocket is fake_ws
    await svc._disconnect()


@pytest.mark.anyio
async def test_connect_noop_when_already_connected_or_closed():
    svc = _make_service()
    svc._websocket = object()
    await svc._connect()  # already "connected": no-op, doesn't raise

    svc2 = _make_service()
    svc2._closed = True
    await svc2._connect()  # closed: also a no-op
    assert svc2._websocket is None


@pytest.mark.anyio
async def test_connect_times_out_waiting_for_ready(monkeypatch):
    svc = _make_service()
    fake_ws = _FakeWebSocket([])  # never sends "ready"

    async def fake_connect(uri, ping_interval=20, ping_timeout=20):
        return fake_ws

    import apps.providers.local.indic_nemotron.stt as stt_mod

    monkeypatch.setattr(stt_mod.websockets, "connect", fake_connect)

    async def fake_wait_for(coro, timeout):
        coro.close()
        raise asyncio.TimeoutError()

    monkeypatch.setattr(stt_mod.asyncio, "wait_for", fake_wait_for)

    with pytest.raises(RuntimeError, match="did not become ready"):
        await svc._connect()
    assert svc._websocket is None


@pytest.mark.anyio
async def test_disconnect_when_never_connected_is_noop():
    svc = _make_service()
    await svc._disconnect()  # nothing to close, nothing to cancel


@pytest.mark.anyio
async def test_disconnect_swallows_close_errors():
    svc = _make_service()

    class _BadWebSocket(_FakeWebSocket):
        async def close(self):
            raise RuntimeError("close failed")

    svc._websocket = _BadWebSocket()
    await svc._disconnect()
    assert svc._websocket is None


@pytest.mark.anyio
async def test_receive_handler_skips_binary_and_non_json(monkeypatch):
    svc = _make_service()
    svc._websocket = _FakeWebSocket([b"\x01\x02", "not json"])
    await svc._receive_handler()
    svc.push_frame.assert_not_called()


@pytest.mark.anyio
async def test_receive_handler_error_without_text_pushes_error_frame():
    svc = _make_service()
    svc._websocket = _FakeWebSocket([json.dumps({"error": "boom"})])
    await svc._receive_handler()
    assert svc.push_frame.await_count == 1
    frame = svc.push_frame.await_args.args[0]
    assert "Nemotron ASR error" in frame.error


@pytest.mark.anyio
async def test_receive_handler_status_ready_sets_event():
    svc = _make_service()
    svc._websocket = _FakeWebSocket([json.dumps({"status": "ready"})])
    set_calls = []
    original_set = svc._ready.set
    svc._ready.set = lambda: (set_calls.append(True), original_set())[-1]
    # The handler's `finally` always clears `_ready` once the message stream
    # ends (a real socket's `finally` only runs when the connection drops),
    # so we spy on `.set()` being called during iteration rather than
    # asserting the event's state after the (short-lived, fake) stream ends.
    await svc._receive_handler()
    assert set_calls == [True]


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "hello_ack"},
        {"status": "sample_rate_mismatch"},
        {"status": "language_updated"},
        # "text" key present (even empty) so this doesn't match the earlier
        # generic "error" branch, which only fires when "text" is absent.
        {"status": "language_rejected", "language": "xx", "error": "unsupported", "text": ""},
        {"status": "session_reset"},
        {"status": "audio_rate_warning", "ratio": 1.5, "implied_sample_rate": 24000},
        {"status": "language_detected"},
    ],
)
@pytest.mark.anyio
async def test_receive_handler_status_only_messages_are_swallowed(payload):
    svc = _make_service()
    svc._websocket = _FakeWebSocket([json.dumps(payload)])
    await svc._receive_handler()
    svc.push_frame.assert_not_called()


@pytest.mark.anyio
async def test_receive_handler_ignores_empty_text_without_is_final():
    svc = _make_service()
    svc._websocket = _FakeWebSocket([json.dumps({"text": ""})])
    await svc._receive_handler()
    svc.push_frame.assert_not_called()


@pytest.mark.anyio
async def test_receive_handler_final_text_pushes_transcription_frame():
    svc = _make_service()
    svc._flush_event = asyncio.Event()
    svc._websocket = _FakeWebSocket(
        [json.dumps({"text": "hello world", "is_final": True})]
    )
    await svc._receive_handler()
    assert svc._pending_final == "hello world"
    assert svc._flush_event.is_set()
    svc.push_frame.assert_awaited_once()
    frame = svc.push_frame.await_args.args[0]
    assert type(frame).__name__ == "TranscriptionFrame"
    assert frame.text == "hello world"
    svc.stop_ttfb_metrics.assert_awaited_once()
    svc.stop_processing_metrics.assert_awaited_once()


@pytest.mark.anyio
async def test_receive_handler_final_empty_text_no_transcription_frame():
    svc = _make_service()
    svc._websocket = _FakeWebSocket([json.dumps({"text": "", "is_final": True})])
    await svc._receive_handler()
    assert svc._pending_final == ""
    svc.push_frame.assert_not_called()


@pytest.mark.anyio
async def test_receive_handler_partial_text_pushes_interim_frame():
    svc = _make_service()
    svc._websocket = _FakeWebSocket([json.dumps({"text": "hel"})])
    await svc._receive_handler()
    svc.push_frame.assert_awaited_once()
    frame = svc.push_frame.await_args.args[0]
    assert type(frame).__name__ == "InterimTranscriptionFrame"
    assert frame.text == "hel"


@pytest.mark.anyio
async def test_receive_handler_generic_exception_pushes_error_when_not_closed():
    svc = _make_service()

    class _RaisingWebSocket:
        def __aiter__(self):
            return self._iter()

        async def _iter(self):
            raise RuntimeError("socket dropped")
            yield  # pragma: no cover - unreachable

    svc._websocket = _RaisingWebSocket()
    await svc._receive_handler()
    svc.push_frame.assert_awaited_once()
    frame = svc.push_frame.await_args.args[0]
    assert "receive failed" in frame.error


@pytest.mark.anyio
async def test_receive_handler_swallows_exception_when_closed():
    svc = _make_service()
    svc._closed = True

    class _RaisingWebSocket:
        def __aiter__(self):
            return self._iter()

        async def _iter(self):
            raise RuntimeError("socket dropped")
            yield  # pragma: no cover

    svc._websocket = _RaisingWebSocket()
    await svc._receive_handler()
    svc.push_frame.assert_not_called()


@pytest.mark.anyio
async def test_receive_handler_reraises_cancelled_error():
    svc = _make_service()

    class _CancellingWebSocket:
        def __aiter__(self):
            return self._iter()

        async def _iter(self):
            raise asyncio.CancelledError()
            yield  # pragma: no cover

    svc._websocket = _CancellingWebSocket()
    with pytest.raises(asyncio.CancelledError):
        await svc._receive_handler()


@pytest.mark.anyio
async def test_flush_utterance_noop_without_websocket():
    svc = _make_service()
    await svc._flush_utterance()  # nothing connected: returns immediately


@pytest.mark.anyio
async def test_flush_utterance_noop_when_already_locked():
    svc = _make_service()
    svc._websocket = AsyncMock()
    async with svc._flush_lock:
        await svc._flush_utterance()  # lock held: no-op
    svc._websocket.send.assert_not_called()


@pytest.mark.anyio
async def test_flush_utterance_sends_and_waits_for_event():
    svc = _make_service()
    svc._websocket = AsyncMock()

    async def set_event_soon():
        await asyncio.sleep(0)
        assert svc._flush_event is not None
        svc._flush_event.set()

    task = asyncio.create_task(set_event_soon())
    await svc._flush_utterance()
    await task
    assert svc._flush_event is None
    svc._websocket.send.assert_called_once_with(
        json.dumps({"action": "flush_eos"})
    )


@pytest.mark.anyio
async def test_flush_utterance_times_out(monkeypatch):
    svc = _make_service()
    svc._websocket = AsyncMock()

    import apps.providers.local.indic_nemotron.stt as stt_mod

    async def fake_wait_for(coro, timeout):
        coro.close()
        raise asyncio.TimeoutError()

    monkeypatch.setattr(stt_mod.asyncio, "wait_for", fake_wait_for)
    await svc._flush_utterance()  # timeout is logged, not raised
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
async def test_process_frame_vad_stopped_flush_error_pushes_error_frame():
    from pipecat.frames.frames import VADUserStoppedSpeakingFrame
    from pipecat.processors.frame_processor import FrameDirection

    svc = _make_service()

    async def raise_error():
        raise RuntimeError("flush boom")

    svc._flush_utterance = raise_error
    await svc.process_frame(VADUserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    # The base STTService.process_frame() also passes the frame itself
    # through push_frame, so the ErrorFrame is the *last* of (at least) two
    # calls rather than the only one.
    error_frames = [
        call.args[0]
        for call in svc.push_frame.await_args_list
        if type(call.args[0]).__name__ == "ErrorFrame"
    ]
    assert len(error_frames) == 1
    assert "flush failed" in error_frames[0].error


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
async def test_stop_flushes_and_disconnects_when_connected():
    from pipecat.frames.frames import EndFrame

    svc = _make_service()
    svc._websocket = AsyncMock()
    svc._flush_utterance = AsyncMock()
    svc._disconnect = AsyncMock()
    await svc.stop(EndFrame())
    assert svc._closed is True
    svc._flush_utterance.assert_awaited_once()
    svc._disconnect.assert_awaited_once()


@pytest.mark.anyio
async def test_stop_skips_flush_when_not_connected():
    from pipecat.frames.frames import EndFrame

    svc = _make_service()
    svc._flush_utterance = AsyncMock()
    svc._disconnect = AsyncMock()
    await svc.stop(EndFrame())
    svc._flush_utterance.assert_not_called()
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
async def test_run_stt_empty_audio_yields_nothing():
    svc = _make_service()
    frames = await _collect(svc.run_stt(b""))
    assert frames == []


@pytest.mark.anyio
async def test_run_stt_closed_yields_nothing():
    svc = _make_service()
    svc._closed = True
    frames = await _collect(svc.run_stt(b"\x00\x01"))
    assert frames == []


@pytest.mark.anyio
async def test_run_stt_connects_when_not_connected():
    svc = _make_service()
    svc._connect = AsyncMock(side_effect=lambda: setattr(svc, "_websocket", object()))
    svc._send_pcm = AsyncMock()
    frames = await _collect(svc.run_stt(b"\x00\x01"))
    svc._connect.assert_awaited_once()
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
    svc._websocket = object()
    svc._sample_rate = 8000  # pipeline rate != target (16000 from catalog)
    svc._send_pcm = AsyncMock()

    resampled = b"\x09\x09"

    async def fake_resample(audio, in_rate, out_rate):
        assert in_rate == 8000
        return resampled

    svc._resampler.resample = fake_resample
    frames = await _collect(svc.run_stt(b"\x00\x01"))
    svc._send_pcm.assert_awaited_once_with(resampled)
    assert frames == [None]


@pytest.mark.anyio
async def test_run_stt_no_outgoing_audio_yields_nothing():
    svc = _make_service()
    svc._websocket = object()
    svc._sample_rate = 8000

    async def fake_resample(audio, in_rate, out_rate):
        return b""

    svc._resampler.resample = fake_resample
    frames = await _collect(svc.run_stt(b"\x00\x01"))
    assert frames == []


@pytest.mark.anyio
async def test_run_stt_send_error_yields_error_frame():
    svc = _make_service()
    svc._websocket = object()

    async def raise_error(pcm):
        raise RuntimeError("send boom")

    svc._send_pcm = raise_error
    frames = await _collect(svc.run_stt(b"\x00\x01"))
    assert len(frames) == 1
    assert "send failed" in frames[0].error


@pytest.mark.anyio
async def test_set_language_without_websocket():
    svc = _make_service()
    await svc.set_language("OD")
    assert svc._language == "od"


@pytest.mark.anyio
async def test_set_language_with_websocket_sends_update():
    svc = _make_service()
    svc._websocket = AsyncMock()
    await svc.set_language("OD")
    svc._websocket.send.assert_called_once_with(
        json.dumps({"action": "set_language", "language": "od"})
    )
