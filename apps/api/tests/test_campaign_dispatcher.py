"""Campaign call dispatcher tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.call_concurrency import CallConcurrencySlot
from app.services.campaign.campaign_call_dispatcher import CampaignCallDispatcher
from app.services.campaign.errors import PhoneNumberPoolExhaustedError


@pytest.mark.asyncio
async def test_process_batch_returns_zero_when_not_running() -> None:
    dispatcher = CampaignCallDispatcher()
    with patch(
        "app.services.campaign.campaign_call_dispatcher.repo.get_campaign_by_id",
        return_value={"campaign_id": "c1", "state": "paused"},
    ):
        count = await dispatcher.process_batch("c1")
    assert count == 0


@pytest.mark.asyncio
async def test_process_batch_running_but_no_queued_rows() -> None:
    dispatcher = CampaignCallDispatcher()
    with (
        patch(
            "app.services.campaign.campaign_call_dispatcher.repo.get_campaign_by_id",
            return_value={"campaign_id": "c1", "state": "running"},
        ),
        patch(
            "app.services.campaign.campaign_call_dispatcher.repo.claim_queued_runs_for_processing",
            return_value=[],
        ),
        patch(
            "app.services.campaign.campaign_call_dispatcher.initiate_outbound_call",
            new_callable=AsyncMock,
        ) as initiate,
    ):
        count = await dispatcher.process_batch("c1")
    assert count == 0
    initiate.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_call_no_number_available_raises() -> None:
    dispatcher = CampaignCallDispatcher()
    campaign = {"campaign_id": "c1", "org_id": "org-1", "agent_id": "agent-1"}
    queued_run = {
        "queued_run_id": "q1",
        "source_uuid": "row_1",
        "context_variables": {"phone_number": "+14155551234"},
    }
    slot = CallConcurrencySlot(
        organization_id="org-1",
        slot_id="slot-1",
        max_concurrent=5,
        source="campaign",
        scope_key="campaign:c1",
    )
    with patch.object(
        dispatcher, "acquire_from_number", new_callable=AsyncMock, return_value=(None, False)
    ):
        with pytest.raises(PhoneNumberPoolExhaustedError):
            await dispatcher.dispatch_call(queued_run, campaign, slot)


@pytest.mark.asyncio
async def test_dispatch_call_initiate_raises_releases_slot() -> None:
    dispatcher = CampaignCallDispatcher()
    campaign = {"campaign_id": "c1", "org_id": "org-1", "agent_id": "agent-1"}
    queued_run = {
        "queued_run_id": "q1",
        "source_uuid": "row_1",
        "context_variables": {"phone_number": "+14155551234"},
    }
    slot = CallConcurrencySlot(
        organization_id="org-1",
        slot_id="slot-1",
        max_concurrent=5,
        source="campaign",
        scope_key="campaign:c1",
    )
    with (
        patch.object(
            dispatcher,
            "acquire_from_number",
            new_callable=AsyncMock,
            return_value=("+15551234567", False),
        ),
        patch(
            "app.services.campaign.campaign_call_dispatcher.initiate_outbound_call",
            new_callable=AsyncMock,
            side_effect=RuntimeError("telephony down"),
        ),
        patch(
            "app.services.campaign.campaign_call_dispatcher.call_concurrency.release_slot",
            new_callable=AsyncMock,
        ) as release_slot,
    ):
        with pytest.raises(RuntimeError):
            await dispatcher.dispatch_call(queued_run, campaign, slot)
    release_slot.assert_called_once_with(slot)


@pytest.mark.asyncio
async def test_process_batch_marks_queued_run_failed_when_dispatch_raises() -> None:
    dispatcher = CampaignCallDispatcher()
    campaign = {
        "campaign_id": "c1",
        "org_id": "org-1",
        "agent_id": "agent-1",
        "state": "running",
    }
    queued_run = {
        "queued_run_id": "q1",
        "source_uuid": "row_1",
        "context_variables": {"phone_number": "+14155551234"},
    }
    with (
        patch(
            "app.services.campaign.campaign_call_dispatcher.repo.get_campaign_by_id",
            return_value=campaign,
        ),
        patch(
            "app.services.campaign.campaign_call_dispatcher.repo.claim_queued_runs_for_processing",
            return_value=[queued_run],
        ),
        patch.object(
            dispatcher, "apply_rate_limit", new_callable=AsyncMock,
        ),
        patch.object(
            dispatcher, "acquire_concurrent_slot", new_callable=AsyncMock, return_value=MagicMock(),
        ),
        patch.object(
            dispatcher,
            "dispatch_call",
            new_callable=AsyncMock,
            side_effect=RuntimeError("telephony down"),
        ),
        patch(
            "app.services.campaign.campaign_call_dispatcher.repo.update_queued_run"
        ) as update_run,
    ):
        count = await dispatcher.process_batch("c1")
    assert count == 0
    update_run.assert_called_once()
    assert update_run.call_args[0][0] == "q1"
    assert update_run.call_args[1]["state"] == "failed"


@pytest.mark.asyncio
async def test_dispatch_call_links_call_log() -> None:
    dispatcher = CampaignCallDispatcher()
    campaign = {
        "campaign_id": "c1",
        "org_id": "org-1",
        "agent_id": "agent-1",
    }
    queued_run = {
        "queued_run_id": "q1",
        "source_uuid": "row_1",
        "context_variables": {"phone_number": "+14155551234", "name": "Jane"},
    }
    slot = CallConcurrencySlot(
        organization_id="org-1",
        slot_id="slot-1",
        max_concurrent=5,
        source="campaign",
        scope_key="campaign:c1",
    )

    with (
        patch.object(
            dispatcher,
            "acquire_from_number",
            new_callable=AsyncMock,
            return_value=("+15551234567", False),
        ),
        patch(
            "app.services.campaign.campaign_call_dispatcher.initiate_outbound_call",
            new_callable=AsyncMock,
            return_value={"call_id": "call-99"},
        ),
        patch(
            "app.services.campaign.campaign_call_dispatcher.call_log_service.update_call_log"
        ) as update_log,
        patch(
            "app.services.campaign.campaign_call_dispatcher.call_concurrency.bind_call_slot",
            new_callable=AsyncMock,
        ),
        patch(
            "app.services.campaign.campaign_call_dispatcher.rate_limiter.store_call_from_number_mapping",
            new_callable=AsyncMock,
        ),
    ):
        result = await dispatcher.dispatch_call(queued_run, campaign, slot)
        assert result["call_id"] == "call-99"
        update_log.assert_called_once()
        assert update_log.call_args[0][1]["campaign_id"] == "c1"


@pytest.mark.asyncio
async def test_resolve_from_numbers_uses_campaign_override() -> None:
    dispatcher = CampaignCallDispatcher()
    campaign = {"from_number": "+15550001111"}
    numbers = await dispatcher._resolve_from_numbers("org-1", "agent-1", campaign)
    assert numbers == ["+15550001111"]


@pytest.mark.asyncio
async def test_resolve_from_numbers_combines_agent_and_pool() -> None:
    dispatcher = CampaignCallDispatcher()
    campaign: dict = {}
    with (
        patch(
            "app.services.campaign.campaign_call_dispatcher.agent_service.get_agent",
            return_value={"linked_phone_number": "+15550001111"},
        ),
        patch(
            "app.services.campaign.campaign_call_dispatcher.phone_number_service.list_by_org",
            return_value=[{"agent_id": "agent-1", "phone_number": "+15550002222"}],
        ),
    ):
        numbers = await dispatcher._resolve_from_numbers("org-1", "agent-1", campaign)
    assert numbers == ["+15550001111", "+15550002222"]


@pytest.mark.asyncio
async def test_apply_rate_limit_retries_until_token_acquired() -> None:
    dispatcher = CampaignCallDispatcher()
    with (
        patch(
            "app.services.campaign.campaign_call_dispatcher.rate_limiter.acquire_token",
            new_callable=AsyncMock,
            side_effect=[False, True],
        ) as acquire_token,
        patch("asyncio.sleep", new_callable=AsyncMock),
    ):
        await dispatcher.apply_rate_limit("org-1", 5)
    assert acquire_token.await_count == 2


@pytest.mark.asyncio
async def test_acquire_concurrent_slot_wraps_exception() -> None:
    from app.services.campaign.errors import ConcurrentSlotAcquisitionError

    dispatcher = CampaignCallDispatcher()
    campaign = {"campaign_id": "c1", "orchestrator_metadata": {}}
    with patch(
        "app.services.campaign.campaign_call_dispatcher.call_concurrency.acquire_org_slot",
        new_callable=AsyncMock,
        side_effect=RuntimeError("no capacity"),
    ):
        with pytest.raises(ConcurrentSlotAcquisitionError):
            await dispatcher.acquire_concurrent_slot("org-1", campaign)


@pytest.mark.asyncio
async def test_return_unprocessed_claims_only_returns_unprocessed() -> None:
    dispatcher = CampaignCallDispatcher()
    queued_runs = [{"queued_run_id": "q1"}, {"queued_run_id": "q2"}]
    with patch(
        "app.services.campaign.campaign_call_dispatcher.repo.return_processing_queued_runs_without_call"
    ) as return_mock:
        await dispatcher._return_unprocessed_claims(queued_runs, {"q1"})
    return_mock.assert_called_once_with(["q2"])


@pytest.mark.asyncio
async def test_release_call_slot_releases_from_number_mapping() -> None:
    dispatcher = CampaignCallDispatcher()
    with (
        patch(
            "app.services.campaign.campaign_call_dispatcher.call_concurrency.release_call_slot",
            new_callable=AsyncMock,
        ) as release_call_slot,
        patch(
            "app.services.campaign.campaign_call_dispatcher.rate_limiter.get_call_from_number_mapping",
            new_callable=AsyncMock,
            return_value=("org-1", "+15551234567", "agent:agent-1"),
        ),
        patch(
            "app.services.campaign.campaign_call_dispatcher.rate_limiter.release_from_number",
            new_callable=AsyncMock,
        ) as release_from_number,
        patch(
            "app.services.campaign.campaign_call_dispatcher.rate_limiter.delete_call_from_number_mapping",
            new_callable=AsyncMock,
        ) as delete_mapping,
    ):
        await dispatcher.release_call_slot("call-1")
    release_call_slot.assert_called_once_with("call-1")
    release_from_number.assert_called_once_with("org-1", "+15551234567", "agent:agent-1")
    delete_mapping.assert_called_once_with("call-1")


@pytest.mark.asyncio
async def test_acquire_from_number_reuses_single_cli_without_pool() -> None:
    dispatcher = CampaignCallDispatcher()
    campaign = {
        "campaign_id": "c1",
        "from_number": "+918065480891",
    }

    with patch.object(
        dispatcher,
        "_resolve_from_numbers",
        new_callable=AsyncMock,
        return_value=["+918065480891"],
    ):
        first, first_managed = await dispatcher.acquire_from_number(
            "org-1", "agent-1", campaign
        )
        second, second_managed = await dispatcher.acquire_from_number(
            "org-1", "agent-1", campaign
        )

    assert first == "+918065480891"
    assert second == "+918065480891"
    assert first_managed is False
    assert second_managed is False


@pytest.mark.asyncio
async def test_dispatch_call_skips_pool_mapping_for_single_cli() -> None:
    dispatcher = CampaignCallDispatcher()
    campaign = {
        "campaign_id": "c1",
        "org_id": "org-1",
        "agent_id": "agent-1",
        "from_number": "+918065480891",
    }
    queued_run = {
        "queued_run_id": "q1",
        "source_uuid": "row_1",
        "context_variables": {"phone_number": "+14155551234"},
    }
    slot = CallConcurrencySlot(
        organization_id="org-1",
        slot_id="slot-1",
        max_concurrent=5,
        source="campaign",
        scope_key="campaign:c1",
    )

    with (
        patch.object(
            dispatcher,
            "acquire_from_number",
            new_callable=AsyncMock,
            return_value=("+918065480891", False),
        ),
        patch(
            "app.services.campaign.campaign_call_dispatcher.initiate_outbound_call",
            new_callable=AsyncMock,
            return_value={"call_id": "call-99"},
        ),
        patch(
            "app.services.campaign.campaign_call_dispatcher.call_log_service.update_call_log"
        ),
        patch(
            "app.services.campaign.campaign_call_dispatcher.call_concurrency.bind_call_slot",
            new_callable=AsyncMock,
        ),
        patch(
            "app.services.campaign.campaign_call_dispatcher.rate_limiter.store_call_from_number_mapping",
            new_callable=AsyncMock,
        ) as store_mapping,
    ):
        await dispatcher.dispatch_call(queued_run, campaign, slot)
        store_mapping.assert_not_called()
