"""Tests for pipecat/lifecycle.py (SessionContext, finalize_call, run_with_lifecycle).

``WorkerRunner`` is pipecat library scaffolding (not code under test here), so
it is replaced with a lightweight fake that records calls; the worker itself
is a real ``PipelineWorker`` and the writers are real ``TranscriptWriter`` /
``CallMetricsWriter`` instances, so the finally-block orchestration in
``run_with_lifecycle`` runs for real.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker

from apps.runtime.services.backend import backend_client
from apps.runtime.services.pipecat import lifecycle
from apps.runtime.services.pipecat.lifecycle import SessionContext, finalize_call, run_with_lifecycle
from apps.runtime.services.pipecat.metrics.writer import CallMetricsWriter
from apps.runtime.services.storage.transcript import TranscriptWriter


@pytest.mark.asyncio
async def test_finalize_call_success(monkeypatch: pytest.MonkeyPatch) -> None:
    update_call = AsyncMock()
    notify = AsyncMock()
    monkeypatch.setattr(backend_client, "update_call", update_call)
    monkeypatch.setattr(backend_client, "notify_campaign_call_status", notify)

    await finalize_call("org-1", "call-1")

    update_call.assert_awaited_once()
    args, _ = update_call.await_args
    assert args[0] == "call-1"
    assert args[1] == "org-1"
    assert args[2]["status"] == "completed"
    assert args[2]["call_response"] == "answered"
    notify.assert_awaited_once_with("org-1", "call-1", "answered")


@pytest.mark.asyncio
async def test_finalize_call_swallows_backend_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        backend_client, "update_call", AsyncMock(side_effect=RuntimeError("boom"))
    )
    notify = AsyncMock()
    monkeypatch.setattr(backend_client, "notify_campaign_call_status", notify)

    # Must not raise.
    await finalize_call("org-1", "call-1")
    notify.assert_not_awaited()


class _FakeWorkerRunner:
    instances: list["_FakeWorkerRunner"] = []

    def __init__(self, *, handle_sigint: bool = True) -> None:
        self.handle_sigint = handle_sigint
        self.added_workers = []
        self.add_workers = AsyncMock(side_effect=self._add_workers)
        self.run = AsyncMock()
        _FakeWorkerRunner.instances.append(self)

    async def _add_workers(self, *workers):
        self.added_workers.extend(workers)


@pytest.fixture(autouse=True)
def _fake_runner(monkeypatch: pytest.MonkeyPatch):
    _FakeWorkerRunner.instances = []
    monkeypatch.setattr(lifecycle, "WorkerRunner", _FakeWorkerRunner)
    yield


def _real_worker() -> PipelineWorker:
    return PipelineWorker(Pipeline([]), params=PipelineParams())


@pytest.mark.asyncio
async def test_run_with_lifecycle_flushes_writers_and_finalizes_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = _real_worker()
    transcript_writer = TranscriptWriter(org_id="org-1", call_id="call-1")
    transcript_writer.append("user", "t1", "hi")
    transcript_writer.flush = AsyncMock()
    metrics_writer = CallMetricsWriter(org_id="org-1", call_id="call-1", session_label="s")
    metrics_writer.flush = AsyncMock()
    finalize = AsyncMock()
    monkeypatch.setattr(lifecycle, "finalize_call", finalize)

    ctx = SessionContext(
        org_id="org-1",
        call_id="call-1",
        session_label="s",
        finalize_call=True,
        agent_id="agent-1",
        sample_rate=8000,
        transcript_writer=transcript_writer,
        metrics_writer=metrics_writer,
    )

    await run_with_lifecycle(worker, ctx)

    runner = _FakeWorkerRunner.instances[0]
    runner.add_workers.assert_awaited_once_with(worker)
    runner.run.assert_awaited_once()
    transcript_writer.flush.assert_awaited_once()
    metrics_writer.flush.assert_awaited_once()
    finalize.assert_awaited_once_with("org-1", "call-1")


@pytest.mark.asyncio
async def test_run_with_lifecycle_skips_finalize_when_not_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = _real_worker()
    finalize = AsyncMock()
    monkeypatch.setattr(lifecycle, "finalize_call", finalize)

    ctx = SessionContext(
        org_id="org-1",
        call_id="call-1",
        session_label="s",
        finalize_call=False,
        agent_id=None,
        sample_rate=8000,
    )

    await run_with_lifecycle(worker, ctx)

    finalize.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_with_lifecycle_flushes_even_when_runner_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = _real_worker()
    transcript_writer = TranscriptWriter(org_id="org-1", call_id="call-1")
    transcript_writer.flush = AsyncMock()
    finalize = AsyncMock()
    monkeypatch.setattr(lifecycle, "finalize_call", finalize)

    ctx = SessionContext(
        org_id="org-1",
        call_id="call-1",
        session_label="s",
        finalize_call=True,
        agent_id=None,
        sample_rate=8000,
        transcript_writer=transcript_writer,
    )

    runner = _FakeWorkerRunner.instances
    # Force runner.run to raise once instantiated inside run_with_lifecycle.
    orig_init = _FakeWorkerRunner.__init__

    def _raising_init(self, *a, **kw):
        orig_init(self, *a, **kw)
        self.run = AsyncMock(side_effect=RuntimeError("pipeline crashed"))

    monkeypatch.setattr(_FakeWorkerRunner, "__init__", _raising_init)

    with pytest.raises(RuntimeError, match="pipeline crashed"):
        await run_with_lifecycle(worker, ctx)

    transcript_writer.flush.assert_awaited_once()
    finalize.assert_awaited_once_with("org-1", "call-1")
