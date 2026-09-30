"""Real pipeline-assembly harness for pipecat/factory.build_pipeline_components.

stt/tts/llm are plain real ``FrameProcessor`` instances (the minimal real
substitute pipecat allows for a service that only needs to sit in the
processor chain and expose ``.name``); the transport is a real
``FastAPIWebsocketTransport`` built on a MagicMock websocket (construction
does not touch the socket unless ``allowed_origins`` is set, which it isn't
here). The assembled ``Pipeline`` is real, so its processor chain/order is
asserted directly.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.serializers.protobuf import ProtobufFrameSerializer

from apps.runtime.services.pipecat.config import pipeline_config_from_behaviour
from apps.runtime.services.pipecat.factory import build_pipeline_components


def _build(
    *,
    behaviour: dict | None = None,
    agent: dict | None = None,
    llm: FrameProcessor | None = None,
):
    config = pipeline_config_from_behaviour(behaviour or {})
    return build_pipeline_components(
        websocket=MagicMock(),
        serializer=ProtobufFrameSerializer(),
        sample_rate=8000,
        stt=FrameProcessor(name="stt"),
        tts=FrameProcessor(name="tts"),
        llm=llm or FrameProcessor(name="llm"),
        system_prompt="You are helpful.",
        config=config,
        agent=agent or {"agent_id": "agent-1"},
        org_id="org-1",
        behaviour=behaviour or {},
    )


def test_build_pipeline_components_default_processor_chain() -> None:
    components = _build()

    # source, stt, user_aggregator, llm, tts, transport.output, audiobuffer,
    # assistant_aggregator, sink
    names = [type(p).__name__ for p in components.pipeline._processors]
    assert names[0] == "PipelineSource"
    assert names[-1] == "PipelineSink"
    assert "LLMUserAggregator" in names
    assert "LLMAssistantAggregator" in names
    assert "AudioBufferProcessor" in names
    # No KB processor injected when the agent has no knowledge_base config.
    assert "KnowledgeContextProcessor" not in names


def test_build_pipeline_components_seeds_system_prompt() -> None:
    components = _build()
    assert components.context.messages[0] == {
        "role": "system",
        "content": "You are helpful.",
    }


def test_build_pipeline_components_no_system_prompt_omits_message() -> None:
    config = pipeline_config_from_behaviour({})
    components = build_pipeline_components(
        websocket=MagicMock(),
        serializer=ProtobufFrameSerializer(),
        sample_rate=8000,
        stt=FrameProcessor(name="stt"),
        tts=FrameProcessor(name="tts"),
        llm=FrameProcessor(name="llm"),
        system_prompt="",
        config=config,
        agent={"agent_id": "agent-1"},
        org_id="org-1",
        behaviour={},
    )
    assert components.context.messages == []


def test_build_pipeline_components_injects_knowledge_context_processor() -> None:
    agent = {
        "agent_id": "agent-1",
        "config": {
            "knowledge_base": {
                "enabled": True,
                "mode": "context",
                "document_ids": ["doc-1"],
                "top_k": 4,
            }
        },
    }
    components = _build(agent=agent)
    names = [type(p).__name__ for p in components.pipeline._processors]
    assert "KnowledgeContextProcessor" in names


def test_build_pipeline_components_appends_llm_after_output_processors() -> None:
    extra = FrameProcessor(name="llm-extra")

    class _LLMWithExtras(FrameProcessor):
        def pipeline_processors_after_output(self):
            return [extra]

    llm = _LLMWithExtras(name="llm")
    components = _build(llm=llm)
    assert extra in components.pipeline._processors


def test_build_pipeline_components_interruption_min_words_sets_turn_strategy() -> None:
    components = _build(behaviour={"interruption_min_words": 3})
    strategies = components.user_aggregator._params.user_turn_strategies
    assert strategies is not None
    assert strategies.start
    assert strategies.start[0]._min_words == 3
