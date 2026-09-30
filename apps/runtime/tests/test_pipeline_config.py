"""Tests for pipecat/config.py (PipelineConfig) and pipecat/idle.py."""

from __future__ import annotations

import asyncio

import pytest
from pipecat.frames.frames import EndWorkerFrame, TTSSpeakFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.processors.frame_processor import FrameDirection

from apps.runtime.services.pipecat.config import (
    PipelineConfig,
    pipeline_config_from_behaviour,
)
from apps.runtime.services.pipecat.idle import (
    UserOnlineDetectionHandler,
    register_idle_handlers,
)


# --- config.py ---


def test_pipeline_config_defaults_from_empty_behaviour() -> None:
    config = pipeline_config_from_behaviour({})
    assert config.ignore_user_speech_before_greeting is False
    assert config.interruption_min_words == 0
    assert config.online_detection_enabled is False
    assert config.user_idle_timeout == 0


def test_pipeline_config_user_idle_timeout_prefers_online_detection() -> None:
    config = pipeline_config_from_behaviour(
        {
            "user_online_detection_enabled": True,
            "user_online_detection_seconds": 15,
            "user_silence_hangup_seconds": 60,
        }
    )
    assert config.online_detection_enabled is True
    assert config.user_idle_timeout == 15


def test_pipeline_config_user_idle_timeout_falls_back_to_silence_hangup() -> None:
    config = pipeline_config_from_behaviour(
        {"user_silence_hangup_seconds": 45}
    )
    assert config.online_detection_enabled is False
    assert config.user_idle_timeout == 45


def test_pipeline_config_parses_full_behaviour() -> None:
    config = pipeline_config_from_behaviour(
        {
            "ignore_user_speech_before_greeting": True,
            "interruption_min_words": 3,
            "user_online_detection_enabled": True,
            "user_online_detection_seconds": 8,
            "user_online_detection_repeats": 2,
            "user_online_detection_message": "Still there?",
            "user_online_detection_closing_message": "Goodbye.",
        }
    )
    assert config == PipelineConfig(
        ignore_user_speech_before_greeting=True,
        interruption_min_words=3,
        online_detection_enabled=True,
        online_detection_seconds=8,
        online_detection_repeats=2,
        online_detection_message="Still there?",
        online_detection_closing_message="Goodbye.",
        user_silence_hangup_seconds=0,
    )


# --- idle.py: UserOnlineDetectionHandler ---


class _RecordingAggregator:
    def __init__(self) -> None:
        self.pushed: list[tuple[object, FrameDirection]] = []

    async def push_frame(self, frame, direction: FrameDirection = FrameDirection.DOWNSTREAM) -> None:
        self.pushed.append((frame, direction))


@pytest.mark.asyncio
async def test_idle_handler_plays_idle_message_until_repeats_exhausted() -> None:
    handler = UserOnlineDetectionHandler(
        max_repeats=2, idle_message="Still there?", closing_message="Goodbye."
    )
    agg = _RecordingAggregator()

    await handler.handle_idle(agg)
    await handler.handle_idle(agg)
    assert len(agg.pushed) == 2
    assert all(isinstance(f, TTSSpeakFrame) and f.text == "Still there?" for f, _ in agg.pushed)

    await handler.handle_idle(agg)
    assert len(agg.pushed) == 4
    closing_frame, _ = agg.pushed[2]
    assert isinstance(closing_frame, TTSSpeakFrame)
    assert closing_frame.text == "Goodbye."
    end_frame, end_direction = agg.pushed[3]
    assert isinstance(end_frame, EndWorkerFrame)
    assert end_direction == FrameDirection.UPSTREAM


@pytest.mark.asyncio
async def test_idle_handler_reset_restarts_attempt_counter() -> None:
    handler = UserOnlineDetectionHandler(
        max_repeats=1, idle_message="Still there?", closing_message="Goodbye."
    )
    agg = _RecordingAggregator()

    await handler.handle_idle(agg)
    handler.reset()
    await handler.handle_idle(agg)

    assert len(agg.pushed) == 2
    assert all(f.text == "Still there?" for f, _ in agg.pushed)


def test_online_detection_from_behaviour_defaults() -> None:
    from apps.runtime.services.pipecat.idle import online_detection_from_behaviour

    result = online_detection_from_behaviour({})
    assert result == (False, 10.0, 1, "", "", 0.0)


async def _fire_and_wait(obj, event_name: str, *args) -> None:
    """Trigger a pipecat event handler and wait for its (task-based) dispatch."""
    await obj._call_event_handler(event_name, *args)
    pending = [task for _, task in obj._event_tasks]
    if pending:
        await asyncio.gather(*pending)


# --- idle.py: register_idle_handlers wired onto a real LLMContextAggregatorPair ---


@pytest.mark.asyncio
async def test_register_idle_handlers_enabled_delegates_to_handler() -> None:
    context = LLMContext([])
    user_aggregator, _assistant = LLMContextAggregatorPair(context)
    handler = UserOnlineDetectionHandler(
        max_repeats=1, idle_message="Still there?", closing_message="Bye."
    )

    register_idle_handlers(
        user_aggregator,
        online_detection_enabled=True,
        idle_handler=handler,
        closing_message="Bye.",
    )

    await _fire_and_wait(user_aggregator, "on_user_turn_idle")
    # First attempt within max_repeats plays the idle message, not the closing one.
    assert handler._attempt == 1


@pytest.mark.asyncio
async def test_register_idle_handlers_disabled_ends_call_directly() -> None:
    context = LLMContext([])
    user_aggregator, _assistant = LLMContextAggregatorPair(context)
    pushed: list[object] = []

    async def fake_push_frame(frame, direction=FrameDirection.DOWNSTREAM):
        pushed.append(frame)

    user_aggregator.push_frame = fake_push_frame

    register_idle_handlers(
        user_aggregator,
        online_detection_enabled=False,
        idle_handler=None,
        closing_message="Session ending.",
    )

    await _fire_and_wait(user_aggregator, "on_user_turn_idle")

    assert any(isinstance(f, TTSSpeakFrame) and f.text == "Session ending." for f in pushed)
    assert any(isinstance(f, EndWorkerFrame) for f in pushed)
