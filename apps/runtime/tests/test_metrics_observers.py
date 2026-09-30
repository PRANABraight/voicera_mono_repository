"""Real-object harness for pipecat/metrics/observers.py (register_call_metrics).

Uses real pipecat observer classes (StartupTimingObserver, UserBotLatencyObserver,
TurnTrackingObserver) and a real PipelineWorker, wired to a real CallMetricsWriter.
Events are fired via ``_call_event_handler`` and awaited through pending tasks.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pipecat.observers.turn_tracking_observer import TurnTrackingObserver
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker

from apps.runtime.services.pipecat.metrics.observers import register_call_metrics
from apps.runtime.services.pipecat.metrics.writer import CallMetricsWriter


async def _fire_and_wait(obj, event_name: str, *args) -> None:
    await obj._call_event_handler(event_name, *args)
    pending = [task for _, task in obj._event_tasks]
    if pending:
        await asyncio.gather(*pending)


def _real_worker() -> PipelineWorker:
    return PipelineWorker(Pipeline([]), params=PipelineParams())


@pytest.mark.asyncio
async def test_register_call_metrics_uses_workers_existing_turn_observer() -> None:
    worker = _real_worker()
    existing_turn_observer = worker.turn_tracking_observer
    writer = CallMetricsWriter(org_id="org-1", call_id="call-1", session_label="s")

    register_call_metrics(worker, writer)

    await _fire_and_wait(existing_turn_observer, "on_turn_started", 1)
    await _fire_and_wait(existing_turn_observer, "on_turn_ended", 1, 2.5, False)

    assert writer._turns[0] == {"turn_number": 1, "started": True}
    assert writer._turns[1]["duration_secs"] == 2.5


@pytest.mark.asyncio
async def test_register_call_metrics_creates_turn_observer_when_missing() -> None:
    worker = MagicMock()
    worker.turn_tracking_observer = None
    worker.add_observer = MagicMock()
    writer = CallMetricsWriter(org_id="org-1", call_id="call-1", session_label="s")

    register_call_metrics(worker, writer)

    # Three observers should have been attached: startup timing, latency, turn tracking.
    assert worker.add_observer.call_count == 3
    observers = [call.args[0] for call in worker.add_observer.call_args_list]
    turn_observer = next(o for o in observers if isinstance(o, TurnTrackingObserver))

    await _fire_and_wait(turn_observer, "on_turn_started", 3)
    assert writer._turns[0] == {"turn_number": 3, "started": True}


@pytest.mark.asyncio
async def test_register_call_metrics_wires_transport_and_latency_observers() -> None:
    worker = MagicMock()
    worker.turn_tracking_observer = TurnTrackingObserver()
    worker.add_observer = MagicMock()
    writer = CallMetricsWriter(org_id="org-1", call_id="call-1", session_label="s")

    register_call_metrics(worker, writer)

    observers = [call.args[0] for call in worker.add_observer.call_args_list]
    transport_observer = next(
        o for o in observers if type(o).__name__ == "StartupTimingObserver"
    )
    latency_observer = next(
        o for o in observers if type(o).__name__ == "UserBotLatencyObserver"
    )

    report = SimpleNamespace(
        start_time=0.0, bot_connected_secs=1.0, client_connected_secs=0.5
    )
    await _fire_and_wait(transport_observer, "on_transport_timing_report", report)
    assert writer._transport == {
        "start_time": 0.0,
        "bot_connected_secs": 1.0,
        "client_connected_secs": 0.5,
    }

    await _fire_and_wait(latency_observer, "on_latency_measured", 0.3)
    assert writer._latencies["user_to_bot_secs"] == [0.3]

    await _fire_and_wait(latency_observer, "on_first_bot_speech_latency", 0.4)
    assert writer._latencies["first_bot_speech_secs"] == 0.4

    breakdown = SimpleNamespace(ttfb=[])
    await _fire_and_wait(latency_observer, "on_latency_breakdown", breakdown)
    assert len(writer._latencies["breakdowns"]) == 1
