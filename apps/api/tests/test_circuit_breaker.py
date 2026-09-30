"""Circuit breaker tests with a hand-rolled fake async Redis client (no real Redis)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.campaign.circuit_breaker import CircuitBreaker


def _fake_redis(eval_return):
    client = AsyncMock()
    client.eval = AsyncMock(return_value=eval_return)
    client.lpush = AsyncMock()
    client.ltrim = AsyncMock()
    client.expire = AsyncMock()
    client.delete = AsyncMock()
    return client


@pytest.mark.asyncio
async def test_record_call_outcome_disabled_skips_redis():
    cb = CircuitBreaker()
    cb.redis_client = _fake_redis([0, 0, 0, 0])
    tripped, stats = await cb.record_call_outcome("c1", True, config={"enabled": False})
    assert tripped is False
    assert stats is None
    cb.redis_client.eval.assert_not_called()


@pytest.mark.asyncio
async def test_record_call_outcome_below_min_calls_not_tripped():
    cb = CircuitBreaker()
    cb.redis_client = _fake_redis([0, 1, 0, 1])
    tripped, stats = await cb.record_call_outcome(
        "c1", True, config={"min_calls_in_window": 5, "failure_threshold": 0.1}
    )
    assert tripped is False
    assert stats["failure_count"] == 1


@pytest.mark.asyncio
async def test_record_call_outcome_above_threshold_tripped():
    cb = CircuitBreaker()
    cb.redis_client = _fake_redis([1, 9, 1, 10])
    tripped, stats = await cb.record_call_outcome(
        "c1", True, config={"min_calls_in_window": 5, "failure_threshold": 0.5}
    )
    assert tripped is True
    assert stats["failure_count"] == 9
    assert stats["success_count"] == 1
    assert stats["failure_rate"] == 0.9


@pytest.mark.asyncio
async def test_is_circuit_open_same_threshold_logic():
    cb = CircuitBreaker()
    cb.redis_client = _fake_redis([1, 8, 2, 10])
    is_open, stats = await cb.is_circuit_open(
        "c1", config={"min_calls_in_window": 5, "failure_threshold": 0.5}
    )
    assert is_open is True
    assert stats["failure_count"] == 8


@pytest.mark.asyncio
async def test_is_circuit_open_disabled_returns_false_without_redis():
    cb = CircuitBreaker()
    cb.redis_client = _fake_redis([0, 0, 0, 0])
    is_open, stats = await cb.is_circuit_open("c1", config={"enabled": False})
    assert is_open is False
    assert stats is None
    cb.redis_client.eval.assert_not_called()


@pytest.mark.asyncio
async def test_record_and_evaluate_campaign_not_running_early_return():
    cb = CircuitBreaker()
    with (
        patch(
            "app.services.campaign.circuit_breaker.repo.get_campaign_by_id",
            return_value={"campaign_id": "c1", "state": "paused"},
        ),
        patch.object(cb, "record_call_outcome", new_callable=AsyncMock) as record_outcome,
    ):
        await cb.record_and_evaluate("c1", True, call_id="call-1", reason="busy")
    record_outcome.assert_not_called()


@pytest.mark.asyncio
async def test_record_and_evaluate_tripped_pauses_and_publishes():
    cb = CircuitBreaker()
    cb.redis_client = _fake_redis([0, 0, 0, 0])
    stats = {
        "failure_rate": 0.9,
        "failure_count": 9,
        "success_count": 1,
        "threshold": 0.5,
        "window_seconds": 60,
    }
    publisher = AsyncMock()
    with (
        patch(
            "app.services.campaign.circuit_breaker.repo.get_campaign_by_id",
            return_value={"campaign_id": "c1", "state": "running"},
        ),
        patch("app.services.campaign.circuit_breaker.repo.update_campaign") as update_campaign,
        patch("app.services.campaign.circuit_breaker.repo.append_campaign_log") as append_log,
        patch.object(cb, "record_call_outcome", new_callable=AsyncMock, return_value=(True, stats)),
        patch(
            "app.services.campaign.circuit_breaker.get_campaign_event_publisher",
            new_callable=AsyncMock,
            return_value=publisher,
        ),
    ):
        await cb.record_and_evaluate("c1", True, call_id="call-1", reason="busy")
    update_campaign.assert_called_once_with("c1", state="paused")
    append_log.assert_called_once()
    publisher.publish_circuit_breaker_tripped.assert_called_once()
    assert publisher.publish_circuit_breaker_tripped.call_args.kwargs["campaign_id"] == "c1"


@pytest.mark.asyncio
async def test_reset_deletes_expected_keys():
    cb = CircuitBreaker()
    fake_redis = _fake_redis([0, 0, 0, 0])
    cb.redis_client = fake_redis
    result = await cb.reset("c1")
    assert result is True
    fake_redis.delete.assert_called_once_with(
        "cb_failures:c1", "cb_successes:c1", "cb_recent_failures:c1"
    )
