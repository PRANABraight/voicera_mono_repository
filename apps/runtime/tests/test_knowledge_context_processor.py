"""Tests for KnowledgeContextProcessor (context.py) and the KB tool (tool.py).

KnowledgeContextProcessor is a real pipecat FrameProcessor; we drive it by
calling process_frame directly (it is unlinked, so push_frame is a no-op) and
assert on its effect on a real LLMContext's messages list.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from pipecat.frames.frames import Frame, LLMContextFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection

from apps.runtime.services.backend import backend_client
from apps.runtime.services.knowledge.context_processor import KnowledgeContextProcessor
from apps.runtime.services.knowledge.tool import build_knowledge_tool


def _processor(context: LLMContext) -> KnowledgeContextProcessor:
    return KnowledgeContextProcessor(
        org_id="org-1",
        document_ids=["doc-1"],
        top_k=3,
        context=context,
    )


@pytest.mark.asyncio
async def test_no_user_message_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    context = LLMContext([{"role": "system", "content": "sys"}])
    processor = _processor(context)
    retrieve = AsyncMock(return_value=[{"text": "chunk"}])
    monkeypatch.setattr(backend_client, "retrieve_knowledge_chunks", retrieve)

    await processor.process_frame(LLMContextFrame(context), FrameDirection.DOWNSTREAM)

    retrieve.assert_not_awaited()
    assert context.messages == [{"role": "system", "content": "sys"}]


@pytest.mark.asyncio
async def test_empty_chunks_leaves_message_unmodified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = LLMContext([{"role": "user", "content": "What is the refund policy?"}])
    processor = _processor(context)
    retrieve = AsyncMock(return_value=[])
    monkeypatch.setattr(backend_client, "retrieve_knowledge_chunks", retrieve)

    await processor.process_frame(LLMContextFrame(context), FrameDirection.DOWNSTREAM)

    retrieve.assert_awaited_once()
    assert context.messages[0]["content"] == "What is the refund policy?"


@pytest.mark.asyncio
async def test_chunks_augment_message_and_track_restore_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = LLMContext([{"role": "user", "content": "What is the refund policy?"}])
    processor = _processor(context)
    retrieve = AsyncMock(
        return_value=[{"text": "Refunds within 30 days.", "source_filename": "policy.pdf"}]
    )
    monkeypatch.setattr(backend_client, "retrieve_knowledge_chunks", retrieve)

    await processor.process_frame(LLMContextFrame(context), FrameDirection.DOWNSTREAM)

    retrieve.assert_awaited_once_with(
        org_id="org-1",
        question="What is the refund policy?",
        document_ids=["doc-1"],
        top_k=3,
        timeout=0.8,
    )
    assert context.messages[0]["content"] != "What is the refund policy?"
    assert "policy.pdf" in context.messages[0]["content"]
    assert processor._pending_restore_index == 0
    assert processor._pending_restore_content == "What is the refund policy?"


@pytest.mark.asyncio
async def test_restore_user_message_restores_original(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = LLMContext([{"role": "user", "content": "What is the refund policy?"}])
    processor = _processor(context)
    retrieve = AsyncMock(
        return_value=[{"text": "Refunds within 30 days.", "source_filename": "policy.pdf"}]
    )
    monkeypatch.setattr(backend_client, "retrieve_knowledge_chunks", retrieve)

    await processor.process_frame(LLMContextFrame(context), FrameDirection.DOWNSTREAM)
    await processor.restore_user_message()

    assert context.messages[0]["content"] == "What is the refund policy?"
    assert processor._pending_restore_index is None
    assert processor._pending_restore_content is None


@pytest.mark.asyncio
async def test_restore_user_message_noop_when_nothing_pending() -> None:
    context = LLMContext([{"role": "user", "content": "hello"}])
    processor = _processor(context)
    await processor.restore_user_message()
    assert context.messages[0]["content"] == "hello"


@pytest.mark.asyncio
async def test_non_llm_context_frame_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    context = LLMContext([{"role": "user", "content": "hello"}])
    processor = _processor(context)
    retrieve = AsyncMock(return_value=[{"text": "chunk"}])
    monkeypatch.setattr(backend_client, "retrieve_knowledge_chunks", retrieve)

    await processor.process_frame(Frame(), FrameDirection.DOWNSTREAM)

    retrieve.assert_not_awaited()


# --- tool.py: build_knowledge_tool ---


@pytest.mark.asyncio
async def test_search_knowledge_base_tool_invokes_result_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    retrieve = AsyncMock(
        return_value=[
            {"text": "Refunds within 30 days.", "source_filename": "policy.pdf"},
        ]
    )
    monkeypatch.setattr(backend_client, "retrieve_knowledge_chunks", retrieve)

    search_knowledge_base = build_knowledge_tool(
        org_id="org-1", document_ids=["doc-1"], top_k=5
    )
    params = AsyncMock()
    params.result_callback = AsyncMock()

    await search_knowledge_base(params, "refund policy")

    retrieve.assert_awaited_once_with(
        org_id="org-1",
        question="refund policy",
        document_ids=["doc-1"],
        top_k=5,
    )
    params.result_callback.assert_awaited_once()
    (payload,), _ = params.result_callback.await_args
    assert payload["query"] == "refund policy"
    assert payload["total_results"] == 1
    assert "policy.pdf" in payload["excerpts"][0]
