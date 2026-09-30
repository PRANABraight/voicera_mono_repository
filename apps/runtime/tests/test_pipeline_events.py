"""Real-object harnesses for pipecat/hold.register_hold_handlers and
pipecat/events/* (logging.py, recording.py, transport.py, __init__.py).

These handlers are registered on real pipecat BaseObject subclasses
(LLMContextAggregatorPair members, a plain FrameProcessor standing in for the
llm, a real AudioBufferProcessor, a real FastAPIWebsocketTransport, and a real
PipelineWorker). Events are triggered via ``_call_event_handler`` and, since
pipecat dispatches handlers as background tasks, we wait for them to finish
before asserting.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from pipecat.frames.frames import TextFrame, TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    AssistantTurnStoppedMessage,
    LLMContextAggregatorPair,
    UserTurnStoppedMessage,
)
from pipecat.processors.audio.audio_buffer_processor import AudioBufferProcessor
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

from apps.runtime.services.pipecat import events as events_pkg
from apps.runtime.services.pipecat.events.logging import register_turn_logging_handlers
from apps.runtime.services.pipecat.events.recording import register_recording_handlers
from apps.runtime.services.pipecat.events.transport import register_transport_handlers
from apps.runtime.services.pipecat.hold import HoldMessageHandler, register_hold_handlers


async def _fire_and_wait(obj, event_name: str, *args) -> None:
    await obj._call_event_handler(event_name, *args)
    pending = [task for _, task in obj._event_tasks]
    if pending:
        await asyncio.gather(*pending)


def _aggregator_pair() -> LLMContextAggregatorPair:
    return LLMContextAggregatorPair(LLMContext([]))


# --- hold.py: register_hold_handlers ---


@pytest.mark.asyncio
async def test_register_hold_handlers_resets_idle_and_cancels_hold_on_turn_started() -> None:
    user_aggregator, _assistant = _aggregator_pair()
    llm = FrameProcessor(name="llm")
    hold_handler = HoldMessageHandler(messages=["Hold on."], timeout_seconds=5, tts=MagicMock())
    hold_handler.cancel = AsyncMock()
    idle_handler = MagicMock()
    idle_handler.reset = MagicMock()

    register_hold_handlers(user_aggregator, llm, hold_handler, idle_handler=idle_handler)

    await _fire_and_wait(user_aggregator, "on_user_turn_started", None)

    idle_handler.reset.assert_called_once()
    hold_handler.cancel.assert_awaited_once()


@pytest.mark.asyncio
async def test_register_hold_handlers_starts_hold_on_inference_triggered() -> None:
    user_aggregator, _assistant = _aggregator_pair()
    llm = FrameProcessor(name="llm")
    hold_handler = HoldMessageHandler(messages=["Hold on."], timeout_seconds=5, tts=MagicMock())
    hold_handler.on_inference_started = AsyncMock()

    register_hold_handlers(user_aggregator, llm, hold_handler, idle_handler=None)

    await _fire_and_wait(user_aggregator, "on_user_turn_inference_triggered", None)

    hold_handler.on_inference_started.assert_awaited_once()


@pytest.mark.asyncio
async def test_register_hold_handlers_cancels_on_llm_text_frame() -> None:
    user_aggregator, _assistant = _aggregator_pair()
    llm = FrameProcessor(name="llm")
    hold_handler = HoldMessageHandler(messages=["Hold on."], timeout_seconds=5, tts=MagicMock())
    hold_handler.cancel = AsyncMock()

    register_hold_handlers(user_aggregator, llm, hold_handler, idle_handler=None)

    await _fire_and_wait(llm, "on_after_push_frame", TextFrame("hi"))
    hold_handler.cancel.assert_awaited_once()

    hold_handler.cancel.reset_mock()
    await _fire_and_wait(llm, "on_after_push_frame", TTSSpeakFrame("not text"))
    hold_handler.cancel.assert_not_awaited()


def test_register_hold_handlers_noop_without_hold_or_idle() -> None:
    user_aggregator, _assistant = _aggregator_pair()
    llm = FrameProcessor(name="llm")
    # Should not raise even though neither handler is configured.
    register_hold_handlers(user_aggregator, llm, None, idle_handler=None)
    assert "on_user_turn_inference_triggered" not in user_aggregator._event_handlers or not (
        user_aggregator._event_handlers["on_user_turn_inference_triggered"].handlers
    )


# --- events/logging.py ---


@pytest.mark.asyncio
async def test_register_turn_logging_handlers_runs_without_error() -> None:
    user_aggregator, assistant_aggregator = _aggregator_pair()
    register_turn_logging_handlers(
        user_aggregator, assistant_aggregator, session_label="call-1"
    )

    user_message = UserTurnStoppedMessage(timestamp="t1", content="hello")
    await _fire_and_wait(user_aggregator, "on_user_turn_stopped", None, user_message)

    assistant_message = AssistantTurnStoppedMessage(
        timestamp="t2", content="hi there", interrupted=True
    )
    await _fire_and_wait(assistant_aggregator, "on_assistant_turn_stopped", assistant_message)


# --- events/recording.py ---


@pytest.mark.asyncio
async def test_register_recording_handlers_skips_without_call_id() -> None:
    audiobuffer = AudioBufferProcessor(num_channels=1, enable_turn_audio=False)
    register_recording_handlers(audiobuffer, org_id="org-1", call_id=None)
    assert not audiobuffer._event_handlers["on_audio_data"].handlers


@pytest.mark.asyncio
async def test_register_recording_handlers_uploads_audio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audiobuffer = AudioBufferProcessor(num_channels=1, enable_turn_audio=False)
    save_and_link = AsyncMock()
    monkeypatch.setattr(
        "apps.runtime.services.pipecat.events.recording.save_and_link", save_and_link
    )

    register_recording_handlers(audiobuffer, org_id="org-1", call_id="call-1")

    await _fire_and_wait(audiobuffer, "on_audio_data", b"\x00\x00", 8000, 1)

    save_and_link.assert_awaited_once()
    _, kwargs = save_and_link.await_args
    assert kwargs["org_id"] == "org-1"
    assert kwargs["call_id"] == "call-1"
    assert kwargs["url_field"] == "recording_url"
    assert kwargs["filename"] == "recording.wav"
    assert kwargs["data"].startswith(b"RIFF")


# --- events/transport.py ---


@pytest.mark.asyncio
async def test_register_transport_handlers_queues_greeting_on_connect() -> None:
    transport = FastAPIWebsocketTransport(
        websocket=MagicMock(),
        params=FastAPIWebsocketParams(audio_in_enabled=True, audio_out_enabled=True),
    )
    worker = MagicMock()
    worker.queue_frames = AsyncMock()
    worker.cancel = AsyncMock()

    register_transport_handlers(
        transport, worker, session_label="call-1", greeting="Hello!", hold_handler=None
    )

    await _fire_and_wait(transport, "on_client_connected", MagicMock())
    worker.queue_frames.assert_awaited_once()
    (frames,), _ = worker.queue_frames.await_args
    assert isinstance(frames[0], TTSSpeakFrame)
    assert frames[0].text == "Hello!"


@pytest.mark.asyncio
async def test_register_transport_handlers_no_greeting_skips_queue() -> None:
    transport = FastAPIWebsocketTransport(
        websocket=MagicMock(),
        params=FastAPIWebsocketParams(audio_in_enabled=True, audio_out_enabled=True),
    )
    worker = MagicMock()
    worker.queue_frames = AsyncMock()
    worker.cancel = AsyncMock()

    register_transport_handlers(
        transport, worker, session_label="call-1", greeting="", hold_handler=None
    )

    await _fire_and_wait(transport, "on_client_connected", MagicMock())
    worker.queue_frames.assert_not_awaited()


@pytest.mark.asyncio
async def test_register_transport_handlers_disconnect_cancels_hold_and_worker() -> None:
    transport = FastAPIWebsocketTransport(
        websocket=MagicMock(),
        params=FastAPIWebsocketParams(audio_in_enabled=True, audio_out_enabled=True),
    )
    worker = MagicMock()
    worker.queue_frames = AsyncMock()
    worker.cancel = AsyncMock()
    hold_handler = MagicMock()
    hold_handler.cancel = AsyncMock()

    register_transport_handlers(
        transport, worker, session_label="call-1", greeting="", hold_handler=hold_handler
    )

    await _fire_and_wait(transport, "on_client_disconnected", MagicMock())
    hold_handler.cancel.assert_awaited_once()
    worker.cancel.assert_awaited_once()


# --- events/__init__.py: register_all_handlers wiring (integration) ---


@pytest.mark.asyncio
async def test_register_all_handlers_wires_transcript_writer_when_call_id_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apps.runtime.services.pipecat.config import pipeline_config_from_behaviour
    from apps.runtime.services.pipecat.factory import PipelineComponents

    user_aggregator, assistant_aggregator = _aggregator_pair()
    llm = FrameProcessor(name="llm")
    audiobuffer = AudioBufferProcessor(num_channels=1, enable_turn_audio=False)
    transport = FastAPIWebsocketTransport(
        websocket=MagicMock(),
        params=FastAPIWebsocketParams(audio_in_enabled=True, audio_out_enabled=True),
    )
    worker = MagicMock()
    worker.queue_frames = AsyncMock()
    worker.cancel = AsyncMock()

    components = PipelineComponents(
        transport=transport,
        pipeline=MagicMock(),
        worker=worker,
        user_aggregator=user_aggregator,
        assistant_aggregator=assistant_aggregator,
        llm=llm,
        audiobuffer=audiobuffer,
        context=LLMContext([]),
    )

    config = pipeline_config_from_behaviour({})

    events_pkg.register_all_handlers(
        components,
        config=config,
        greeting="Hi!",
        session_label="call-1",
        hold_handler=None,
        idle_handler=None,
        org_id="org-1",
        call_id="call-1",
    )

    assert components.transcript_writer is not None
    assert components.transcript_writer.object_uri.endswith("org-1/call-1/transcript.txt")


@pytest.mark.asyncio
async def test_register_all_handlers_skips_transcript_writer_without_call_id() -> None:
    from apps.runtime.services.pipecat.config import pipeline_config_from_behaviour
    from apps.runtime.services.pipecat.factory import PipelineComponents

    user_aggregator, assistant_aggregator = _aggregator_pair()
    llm = FrameProcessor(name="llm")
    audiobuffer = AudioBufferProcessor(num_channels=1, enable_turn_audio=False)
    transport = FastAPIWebsocketTransport(
        websocket=MagicMock(),
        params=FastAPIWebsocketParams(audio_in_enabled=True, audio_out_enabled=True),
    )
    worker = MagicMock()
    worker.queue_frames = AsyncMock()
    worker.cancel = AsyncMock()

    components = PipelineComponents(
        transport=transport,
        pipeline=MagicMock(),
        worker=worker,
        user_aggregator=user_aggregator,
        assistant_aggregator=assistant_aggregator,
        llm=llm,
        audiobuffer=audiobuffer,
        context=LLMContext([]),
    )

    config = pipeline_config_from_behaviour({})

    events_pkg.register_all_handlers(
        components,
        config=config,
        greeting="",
        session_label="call-1",
        hold_handler=None,
        idle_handler=None,
        org_id="org-1",
        call_id=None,
    )

    assert components.transcript_writer is None
