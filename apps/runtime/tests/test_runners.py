"""Tests for pipecat/runners.py — imported directly (NOT via the conftest.py
sys.modules stub used by route-level tests), so this exercises the real
module. run_pipeline is pipecat-orchestration already covered by
test_pipeline_run.py / test_factory.py, so here we patch it and assert
run_telephony_bot / run_websocket_bot build the correct serializer and pass
the right arguments through.
"""

from __future__ import annotations

import importlib
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
from pipecat.serializers.protobuf import ProtobufFrameSerializer

# conftest.py stubs this module with a MagicMock (sys.modules injection) so
# route-level tests can avoid loading pipecat's full pipeline. This test file
# exercises the REAL module, so drop the stub and force a genuine import.
sys.modules.pop("apps.runtime.services.pipecat.runners", None)
runners = importlib.import_module("apps.runtime.services.pipecat.runners")
runners = importlib.reload(runners)


@pytest.mark.asyncio
async def test_run_telephony_bot_builds_provider_serializer_and_delegates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_pipeline = AsyncMock()
    monkeypatch.setattr(runners, "run_pipeline", run_pipeline)
    create_frame_serializer = MagicMock(return_value=MagicMock(name="serializer"))
    monkeypatch.setattr(runners, "create_frame_serializer", create_frame_serializer)

    websocket = MagicMock()
    agent = {"agent_id": "agent-1"}

    await runners.run_telephony_bot(
        websocket,
        org_id="org-1",
        provider="plivo",
        stream_sid="stream-1",
        call_sid="call-sid-1",
        call_id="call-1",
        agent=agent,
        custom_variables={"name": "Jane"},
    )

    create_frame_serializer.assert_called_once_with(
        "plivo",
        stream_sid="stream-1",
        call_sid="call-sid-1",
        sample_rate=runners.telephony_sample_rate(),
    )
    run_pipeline.assert_awaited_once()
    args, kwargs = run_pipeline.await_args
    assert args[0] is websocket
    assert kwargs["org_id"] == "org-1"
    assert kwargs["agent"] == agent
    assert kwargs["serializer"] is create_frame_serializer.return_value
    assert kwargs["sample_rate"] == runners.telephony_sample_rate()
    assert kwargs["call_id"] == "call-1"
    assert kwargs["custom_variables"] == {"name": "Jane"}
    assert kwargs["session_label"] == "call_sid=call-sid-1"
    assert kwargs["finalize_call"] is True


@pytest.mark.asyncio
async def test_run_websocket_bot_uses_protobuf_serializer_and_call_id_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_pipeline = AsyncMock()
    monkeypatch.setattr(runners, "run_pipeline", run_pipeline)

    websocket = MagicMock()
    agent = {"agent_id": "agent-2"}

    await runners.run_websocket_bot(
        websocket,
        org_id="org-1",
        agent=agent,
        call_id="call-web-1",
    )

    run_pipeline.assert_awaited_once()
    args, kwargs = run_pipeline.await_args
    assert args[0] is websocket
    assert isinstance(kwargs["serializer"], ProtobufFrameSerializer)
    assert kwargs["sample_rate"] == runners.websocket_sample_rate()
    assert kwargs["session_label"] == "call_id=call-web-1"
    assert kwargs["finalize_call"] is True


@pytest.mark.asyncio
async def test_run_websocket_bot_without_call_id_labels_by_agent_and_skips_finalize(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_pipeline = AsyncMock()
    monkeypatch.setattr(runners, "run_pipeline", run_pipeline)

    websocket = MagicMock()
    agent = {"agent_id": "agent-3"}

    await runners.run_websocket_bot(websocket, org_id="org-1", agent=agent)

    run_pipeline.assert_awaited_once()
    _, kwargs = run_pipeline.await_args
    assert kwargs["session_label"] == "agent_id=agent-3"
    assert kwargs["call_id"] is None
    assert kwargs["finalize_call"] is False
