"""Campaign orchestrator unit tests (retry handling, scheduling, completion)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.campaign.campaign_event_protocol import (
    BatchCompletedEvent,
    BatchFailedEvent,
    CircuitBreakerTrippedEvent,
    RetryNeededEvent,
    SyncCompletedEvent,
)
from app.services.campaign.campaign_orchestrator import CampaignOrchestrator


def _orchestrator() -> CampaignOrchestrator:
    return CampaignOrchestrator(AsyncMock())


MOD = "app.services.campaign.campaign_orchestrator"


@pytest.mark.asyncio
async def test_handle_retry_event_disabled_no_queued_run():
    orch = _orchestrator()
    campaign = {"campaign_id": "c1", "retry_config": {"enabled": False}}
    with (
        patch(f"{MOD}.repo.get_campaign_by_id", return_value=campaign),
        patch(f"{MOD}.repo.get_queued_run_by_id") as get_run,
        patch(f"{MOD}.repo.create_queued_run") as create_run,
    ):
        await orch._handle_retry_event(
            RetryNeededEvent(campaign_id="c1", call_id="call-1", queued_run_id="q1", reason="busy")
        )
    get_run.assert_not_called()
    create_run.assert_not_called()


@pytest.mark.asyncio
async def test_handle_retry_event_busy_not_retried_when_disabled():
    orch = _orchestrator()
    campaign = {
        "campaign_id": "c1",
        "retry_config": {"enabled": True, "retry_on_busy": False},
    }
    with (
        patch(f"{MOD}.repo.get_campaign_by_id", return_value=campaign),
        patch(f"{MOD}.repo.get_queued_run_by_id") as get_run,
        patch(f"{MOD}.repo.create_queued_run") as create_run,
    ):
        await orch._handle_retry_event(
            RetryNeededEvent(campaign_id="c1", call_id="call-1", queued_run_id="q1", reason="busy")
        )
    get_run.assert_not_called()
    create_run.assert_not_called()


@pytest.mark.asyncio
async def test_handle_retry_event_max_retries_marks_failed():
    orch = _orchestrator()
    campaign = {
        "campaign_id": "c1",
        "failed_rows": 2,
        "retry_config": {"enabled": True, "max_retries": 1},
    }
    queued_run = {
        "queued_run_id": "q1",
        "source_uuid": "row_1",
        "retry_count": 1,
        "context_variables": {},
    }
    with (
        patch(f"{MOD}.repo.get_campaign_by_id", return_value=campaign),
        patch(f"{MOD}.repo.get_queued_run_by_id", return_value=queued_run),
        patch(f"{MOD}.repo.update_campaign") as update_campaign,
        patch(f"{MOD}.repo.create_queued_run") as create_run,
    ):
        await orch._handle_retry_event(
            RetryNeededEvent(campaign_id="c1", call_id="call-1", queued_run_id="q1", reason="busy")
        )
    update_campaign.assert_called_once_with("c1", failed_rows=3)
    create_run.assert_not_called()


@pytest.mark.asyncio
async def test_handle_retry_event_creates_retry_run():
    orch = _orchestrator()
    campaign = {
        "campaign_id": "c1",
        "retry_config": {"enabled": True, "max_retries": 3, "retry_delay_seconds": 1},
    }
    queued_run = {
        "queued_run_id": "q1",
        "source_uuid": "row_1",
        "retry_count": 0,
        "context_variables": {"phone_number": "+14155550000"},
    }
    with (
        patch(f"{MOD}.repo.get_campaign_by_id", return_value=campaign),
        patch(f"{MOD}.repo.get_queued_run_by_id", return_value=queued_run),
        patch(f"{MOD}.repo.create_queued_run") as create_run,
    ):
        await orch._handle_retry_event(
            RetryNeededEvent(campaign_id="c1", call_id="call-1", queued_run_id="q1", reason="busy")
        )
    create_run.assert_called_once()
    kwargs = create_run.call_args.kwargs
    assert kwargs["campaign_id"] == "c1"
    assert kwargs["retry_count"] == 1
    assert kwargs["source_uuid"] == "row_1_retry_1"


@pytest.mark.asyncio
async def test_handle_event_ignores_missing_campaign_id():
    orch = _orchestrator()
    with patch.object(orch, "_schedule_next_batch", new_callable=AsyncMock) as schedule:
        await orch._handle_event(BatchCompletedEvent(campaign_id="", processed_count=1))
    schedule.assert_not_called()


@pytest.mark.asyncio
async def test_handle_event_batch_completed_not_running_clears_state():
    orch = _orchestrator()
    orch._last_activity["c1"] = orch._last_activity.get("c1")
    orch._batch_in_progress["c1"] = "sentinel"
    with (
        patch(f"{MOD}.repo.get_campaign_by_id", return_value={"campaign_id": "c1", "state": "paused"}),
        patch.object(orch, "_schedule_next_batch", new_callable=AsyncMock) as schedule,
    ):
        await orch._handle_event(BatchCompletedEvent(campaign_id="c1", processed_count=1))
    schedule.assert_not_called()
    assert "c1" not in orch._batch_in_progress


@pytest.mark.asyncio
async def test_handle_event_batch_completed_running_schedules_next():
    orch = _orchestrator()
    with (
        patch(f"{MOD}.repo.get_campaign_by_id", return_value={"campaign_id": "c1", "state": "running"}),
        patch.object(orch, "_schedule_next_batch", new_callable=AsyncMock) as schedule,
    ):
        await orch._handle_event(BatchCompletedEvent(campaign_id="c1", processed_count=1))
    schedule.assert_called_once_with("c1")
    assert "c1" in orch._last_activity


@pytest.mark.asyncio
async def test_handle_event_batch_failed_records_activity():
    orch = _orchestrator()
    orch._batch_in_progress["c1"] = "sentinel"
    await orch._handle_event(BatchFailedEvent(campaign_id="c1", error="boom"))
    assert "c1" not in orch._batch_in_progress
    assert "c1" in orch._last_activity


@pytest.mark.asyncio
async def test_handle_event_sync_completed_schedules_next():
    orch = _orchestrator()
    with patch.object(orch, "_schedule_next_batch", new_callable=AsyncMock) as schedule:
        await orch._handle_event(SyncCompletedEvent(campaign_id="c1", total_rows=5))
    schedule.assert_called_once_with("c1")
    assert "c1" in orch._last_activity


@pytest.mark.asyncio
async def test_handle_event_circuit_breaker_tripped_clears_state():
    orch = _orchestrator()
    orch._last_activity["c1"] = "sentinel"
    await orch._handle_event(
        CircuitBreakerTrippedEvent(
            campaign_id="c1",
            failure_rate=0.9,
            failure_count=9,
            success_count=1,
            threshold=0.5,
            window_seconds=60,
        )
    )
    assert "c1" not in orch._last_activity


@pytest.mark.asyncio
async def test_has_pending_work_true_when_queued():
    orch = _orchestrator()
    with (
        patch(f"{MOD}.repo.count_pending_queued_runs", return_value=2),
        patch(f"{MOD}.repo.count_processing_queued_runs", return_value=0),
    ):
        assert await orch._has_pending_work("c1") is True


@pytest.mark.asyncio
async def test_has_pending_work_false_when_none():
    orch = _orchestrator()
    with (
        patch(f"{MOD}.repo.count_pending_queued_runs", return_value=0),
        patch(f"{MOD}.repo.count_processing_queued_runs", return_value=0),
    ):
        assert await orch._has_pending_work("c1") is False


@pytest.mark.asyncio
async def test_complete_campaign_publishes_completion():
    orch = _orchestrator()
    orch._last_activity["c1"] = "sentinel"
    campaign = {"campaign_id": "c1", "total_rows": 10, "processed_rows": 10, "failed_rows": 0}
    with (
        patch.object(orch, "_has_pending_work", new_callable=AsyncMock, return_value=False),
        patch(f"{MOD}.repo.update_campaign") as update_campaign,
        patch.object(
            orch.publisher, "publish_campaign_completed", new_callable=AsyncMock
        ) as publish_completed,
    ):
        await orch._complete_campaign(campaign)
    update_campaign.assert_called_once()
    assert update_campaign.call_args[0] == ("c1",)
    assert update_campaign.call_args[1]["state"] == "completed"
    publish_completed.assert_called_once()
    assert "c1" not in orch._last_activity


@pytest.mark.asyncio
async def test_check_stale_campaigns_completes_when_no_pending_work():
    orch = _orchestrator()
    campaign = {"campaign_id": "c1", "state": "running"}
    with (
        patch(f"{MOD}.repo.get_campaigns_by_status", return_value=[campaign]),
        patch.object(orch, "_has_pending_work", new_callable=AsyncMock, return_value=False),
        patch.object(orch, "_should_mark_complete", new_callable=AsyncMock, return_value=True),
        patch.object(orch, "_complete_campaign", new_callable=AsyncMock) as complete_mock,
    ):
        await orch._check_stale_campaigns()
    complete_mock.assert_called_once_with(campaign)


@pytest.mark.asyncio
async def test_check_stale_campaigns_schedules_pending_work():
    orch = _orchestrator()
    campaign = {"campaign_id": "c1", "state": "running"}
    with (
        patch(f"{MOD}.repo.get_campaigns_by_status", return_value=[campaign]),
        patch.object(orch, "_has_pending_work", new_callable=AsyncMock, return_value=True),
        patch.object(orch, "_schedule_next_batch", new_callable=AsyncMock) as schedule_mock,
    ):
        await orch._check_stale_campaigns()
    schedule_mock.assert_called_once_with("c1")


@pytest.mark.asyncio
async def test_check_stale_campaigns_recovers_timed_out_batch():
    orch = _orchestrator()
    campaign = {"campaign_id": "c1", "state": "running"}
    orch._batch_in_progress["c1"] = datetime.now(timezone.utc) - timedelta(seconds=400)
    with (
        patch(f"{MOD}.repo.get_campaigns_by_status", return_value=[campaign]),
        patch.object(orch, "_has_pending_work", new_callable=AsyncMock, return_value=True),
        patch.object(orch, "_schedule_next_batch", new_callable=AsyncMock) as schedule_mock,
    ):
        await orch._check_stale_campaigns()
    schedule_mock.assert_called_once_with("c1")
    assert "c1" not in orch._batch_in_progress


@pytest.mark.asyncio
async def test_check_stale_campaigns_handles_exceptions():
    orch = _orchestrator()
    campaign = {"campaign_id": "c1", "state": "running"}
    with (
        patch(f"{MOD}.repo.get_campaigns_by_status", return_value=[campaign]),
        patch.object(
            orch, "_has_pending_work", new_callable=AsyncMock, side_effect=RuntimeError("boom")
        ),
    ):
        await orch._check_stale_campaigns()  # should not raise


@pytest.mark.asyncio
async def test_monitor_completion_stops_when_not_running():
    orch = _orchestrator()
    orch._running = False
    with patch.object(orch, "_check_stale_campaigns", new_callable=AsyncMock) as check_mock:
        await orch._monitor_completion()
    check_mock.assert_not_called()


@pytest.mark.asyncio
async def test_shutdown_unsubscribes_pubsub():
    orch = _orchestrator()
    orch._pubsub = AsyncMock()
    await orch.shutdown()
    assert orch._running is False
    orch._pubsub.unsubscribe.assert_called_once()
    orch._pubsub.aclose.assert_called_once()


@pytest.mark.asyncio
async def test_shutdown_swallows_pubsub_errors():
    orch = _orchestrator()
    orch._pubsub = AsyncMock()
    orch._pubsub.unsubscribe.side_effect = RuntimeError("closed")
    await orch.shutdown()  # should not raise
    assert orch._running is False


def test_is_within_schedule_no_config_defaults_true():
    orch = _orchestrator()
    assert orch._is_within_schedule({}) is True


def test_is_within_schedule_inside_slot():
    orch = _orchestrator()
    now = datetime.now(timezone.utc)
    day = now.weekday()
    start = (now - timedelta(minutes=5)).strftime("%H:%M")
    end = (now + timedelta(minutes=5)).strftime("%H:%M")
    campaign = {
        "orchestrator_metadata": {
            "schedule_config": {
                "enabled": True,
                "timezone": "UTC",
                "slots": [{"day_of_week": day, "start_time": start, "end_time": end}],
            }
        }
    }
    assert orch._is_within_schedule(campaign) is True


def test_is_within_schedule_outside_slot():
    orch = _orchestrator()
    now = datetime.now(timezone.utc)
    day = now.weekday()
    # A slot far in the past that has already ended.
    campaign = {
        "orchestrator_metadata": {
            "schedule_config": {
                "enabled": True,
                "timezone": "UTC",
                "slots": [{"day_of_week": day, "start_time": "00:00", "end_time": "00:01"}],
            }
        }
    }
    if now.strftime("%H:%M") < "00:01":
        pytest.skip("flaky right at midnight UTC")
    assert orch._is_within_schedule(campaign) is False


@pytest.mark.asyncio
async def test_schedule_next_batch_circuit_breaker_open_pauses_campaign():
    orch = _orchestrator()
    campaign = {"campaign_id": "c1", "state": "running", "orchestrator_metadata": {}}
    stats = {
        "failure_rate": 0.9,
        "failure_count": 9,
        "success_count": 1,
        "threshold": 0.5,
        "window_seconds": 60,
    }
    with (
        patch(f"{MOD}.repo.get_campaign_by_id", return_value=campaign),
        patch(f"{MOD}.repo.update_campaign") as update_campaign,
        patch.object(
            orch.publisher, "publish_circuit_breaker_tripped", new_callable=AsyncMock
        ) as publish_tripped,
        patch(
            f"{MOD}.circuit_breaker.is_circuit_open",
            new_callable=AsyncMock,
            return_value=(True, stats),
        ),
        patch.object(orch, "_release_lock_after_delay", new_callable=AsyncMock),
    ):
        await orch._schedule_next_batch("c1")
    update_campaign.assert_called_once_with("c1", state="paused")
    publish_tripped.assert_called_once()
    assert publish_tripped.call_args.kwargs["campaign_id"] == "c1"
    assert "c1" not in orch._last_activity


@pytest.mark.asyncio
async def test_schedule_next_batch_enqueues_when_pending_work():
    orch = _orchestrator()
    campaign = {"campaign_id": "c1", "state": "running", "orchestrator_metadata": {}}
    with (
        patch(f"{MOD}.repo.get_campaign_by_id", return_value=campaign),
        patch(f"{MOD}.repo.update_campaign") as update_campaign,
        patch(
            f"{MOD}.circuit_breaker.is_circuit_open",
            new_callable=AsyncMock,
            return_value=(False, None),
        ),
        patch.object(orch, "_has_pending_work", new_callable=AsyncMock, return_value=True),
        patch(f"{MOD}.enqueue_job", new_callable=AsyncMock) as enqueue_job,
    ):
        await orch._schedule_next_batch("c1")
        # release-lock task is scheduled via asyncio.create_task; let it not matter for assertions.
        orch._processing_locks.pop("c1", None)
    enqueue_job.assert_called_once()
    assert "c1" in orch._batch_in_progress
    update_campaign.assert_called_once()


@pytest.mark.asyncio
async def test_should_mark_complete_false_when_pending_work():
    orch = _orchestrator()
    campaign = {"campaign_id": "c1"}
    with patch.object(orch, "_has_pending_work", new_callable=AsyncMock, return_value=True):
        result = await orch._should_mark_complete(campaign)
    assert result is False


@pytest.mark.asyncio
async def test_should_mark_complete_false_within_timeout():
    orch = _orchestrator()
    campaign = {
        "campaign_id": "c1",
        "last_activity_at": datetime.now(timezone.utc).isoformat(),
    }
    with patch.object(orch, "_has_pending_work", new_callable=AsyncMock, return_value=False):
        result = await orch._should_mark_complete(campaign)
    assert result is False


@pytest.mark.asyncio
async def test_should_mark_complete_true_past_timeout():
    orch = _orchestrator()
    stale = (datetime.now(timezone.utc) - timedelta(seconds=orch.completion_timeout + 10)).isoformat()
    campaign = {"campaign_id": "c1", "last_activity_at": stale}
    with patch.object(orch, "_has_pending_work", new_callable=AsyncMock, return_value=False):
        result = await orch._should_mark_complete(campaign)
    assert result is True
