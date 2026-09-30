"""Orchestration tests for pipecat/pipeline.run_pipeline.

The sub-steps (build_ai_services, build_pipeline_components,
register_all_handlers, register_call_metrics, run_with_lifecycle) each have
their own dedicated real-object test coverage elsewhere; here we verify
run_pipeline wires them together correctly (arguments passed through, correct
branching on call_id / online-detection / llm.set_call_id), using AsyncMock/
MagicMock stand-ins for those collaborators.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.runtime.services.pipecat import pipeline as pipeline_module
from apps.runtime.services.pipecat.factory import PipelineComponents


def _agent(**behaviour_overrides) -> dict:
    behaviour = {"hold_messages": ["Hold"], "hold_message_timeout_seconds": 1.0}
    behaviour.update(behaviour_overrides)
    return {
        "agent_id": "agent-1",
        "config": {
            "prompts": {"system_prompt": "Be helpful.", "greeting_message": "Hi!"},
            "behaviour": behaviour,
        },
    }


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch):
    stt, tts, llm = MagicMock(name="stt"), MagicMock(name="tts"), MagicMock(name="llm")
    stt.name, tts.name, llm.name = "stt", "tts", "llm"
    build_ai_services = AsyncMock(return_value=(stt, tts, llm))
    monkeypatch.setattr(pipeline_module, "build_ai_services", build_ai_services)

    components = PipelineComponents(
        transport=MagicMock(),
        pipeline=MagicMock(),
        worker=MagicMock(),
        user_aggregator=MagicMock(),
        assistant_aggregator=MagicMock(),
        llm=llm,
        audiobuffer=MagicMock(),
        context=MagicMock(),
    )
    build_pipeline_components = MagicMock(return_value=components)
    monkeypatch.setattr(pipeline_module, "build_pipeline_components", build_pipeline_components)

    register_all_handlers = MagicMock()
    monkeypatch.setattr(pipeline_module, "register_all_handlers", register_all_handlers)

    register_call_metrics = MagicMock()
    monkeypatch.setattr(pipeline_module, "register_call_metrics", register_call_metrics)

    run_with_lifecycle = AsyncMock()
    monkeypatch.setattr(pipeline_module, "run_with_lifecycle", run_with_lifecycle)

    return {
        "stt": stt,
        "tts": tts,
        "llm": llm,
        "build_ai_services": build_ai_services,
        "components": components,
        "build_pipeline_components": build_pipeline_components,
        "register_all_handlers": register_all_handlers,
        "register_call_metrics": register_call_metrics,
        "run_with_lifecycle": run_with_lifecycle,
    }


@pytest.mark.asyncio
async def test_run_pipeline_sets_call_id_on_llm_when_supported(patched) -> None:
    patched["llm"].set_call_id = MagicMock()

    await pipeline_module.run_pipeline(
        MagicMock(),
        org_id="org-1",
        agent=_agent(),
        serializer=MagicMock(),
        sample_rate=8000,
        call_id="call-1",
    )

    patched["llm"].set_call_id.assert_called_once_with("call-1")


@pytest.mark.asyncio
async def test_run_pipeline_registers_metrics_when_call_id_present(patched) -> None:
    await pipeline_module.run_pipeline(
        MagicMock(),
        org_id="org-1",
        agent=_agent(),
        serializer=MagicMock(),
        sample_rate=8000,
        call_id="call-1",
        session_label="mysession",
    )

    patched["register_call_metrics"].assert_called_once()
    worker_arg, writer_arg = patched["register_call_metrics"].call_args.args
    assert worker_arg is patched["components"].worker
    assert writer_arg.call_id == "call-1"
    assert patched["components"].metrics_writer is writer_arg


@pytest.mark.asyncio
async def test_run_pipeline_skips_metrics_without_call_id(patched) -> None:
    await pipeline_module.run_pipeline(
        MagicMock(),
        org_id="org-1",
        agent=_agent(),
        serializer=MagicMock(),
        sample_rate=8000,
        call_id=None,
    )

    patched["register_call_metrics"].assert_not_called()


@pytest.mark.asyncio
async def test_run_pipeline_passes_session_context_to_lifecycle(patched) -> None:
    await pipeline_module.run_pipeline(
        MagicMock(),
        org_id="org-1",
        agent=_agent(),
        serializer=MagicMock(),
        sample_rate=8000,
        call_id="call-1",
        session_label="mysession",
        finalize_call=True,
    )

    patched["run_with_lifecycle"].assert_awaited_once()
    worker_arg, ctx_arg = patched["run_with_lifecycle"].await_args.args
    assert worker_arg is patched["components"].worker
    assert ctx_arg.org_id == "org-1"
    assert ctx_arg.call_id == "call-1"
    assert ctx_arg.session_label == "mysession"
    assert ctx_arg.finalize_call is True
    assert ctx_arg.agent_id == "agent-1"
    assert ctx_arg.sample_rate == 8000


@pytest.mark.asyncio
async def test_run_pipeline_builds_idle_handler_when_online_detection_enabled(
    patched, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = {}

    def _capture_register(components, **kwargs):
        captured.update(kwargs)

    patched["register_all_handlers"].side_effect = _capture_register

    agent = _agent(
        user_online_detection_enabled=True,
        user_online_detection_seconds=5,
        user_online_detection_repeats=2,
        user_online_detection_message="Still there?",
        user_online_detection_closing_message="Bye.",
    )

    await pipeline_module.run_pipeline(
        MagicMock(),
        org_id="org-1",
        agent=agent,
        serializer=MagicMock(),
        sample_rate=8000,
        call_id="call-1",
    )

    assert captured["idle_handler"] is not None
    assert captured["idle_handler"]._max_repeats == 2


@pytest.mark.asyncio
async def test_run_pipeline_no_idle_handler_when_online_detection_disabled(
    patched,
) -> None:
    captured = {}

    def _capture_register(components, **kwargs):
        captured.update(kwargs)

    patched["register_all_handlers"].side_effect = _capture_register

    await pipeline_module.run_pipeline(
        MagicMock(),
        org_id="org-1",
        agent=_agent(),
        serializer=MagicMock(),
        sample_rate=8000,
        call_id="call-1",
    )

    assert captured["idle_handler"] is None
