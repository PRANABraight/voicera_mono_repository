"""Campaign CSV validation tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.campaign.source_sync import CampaignSourceSyncService
from app.services.campaign.sources.csv import CSVSyncService


def test_validate_source_data_requires_phone_column() -> None:
    result = CampaignSourceSyncService.validate_source_data(
        ["name"], [["Alice"]]
    )
    assert not result.is_valid
    assert result.error is not None
    assert "phone_number" in result.error.message


def test_validate_source_data_requires_e164() -> None:
    result = CampaignSourceSyncService.validate_source_data(
        ["phone_number"], [["5551234"]]
    )
    assert not result.is_valid
    assert result.error is not None


def test_validate_source_data_accepts_valid_rows() -> None:
    result = CampaignSourceSyncService.validate_source_data(
        ["phone_number", "name"],
        [["+14155551234", "Alice"]],
    )
    assert result.is_valid


MOD = "app.services.campaign.sources.csv"


@pytest.mark.asyncio
async def test_csv_validate_source_storage_fetch_raises_invalid():
    service = CSVSyncService()
    with patch.object(
        service, "_fetch_csv_data", new_callable=AsyncMock, side_effect=RuntimeError("boom")
    ):
        result = await service.validate_source("bad-key")
    assert not result.is_valid
    assert "boom" in result.error.message


@pytest.mark.asyncio
async def test_csv_validate_source_header_only_invalid():
    service = CSVSyncService()
    with patch.object(
        service, "_fetch_csv_data", new_callable=AsyncMock, return_value=[["phone_number"]]
    ):
        result = await service.validate_source("header-only.csv")
    assert not result.is_valid


@pytest.mark.asyncio
async def test_csv_sync_source_data_campaign_not_found_raises():
    service = CSVSyncService()
    with patch(f"{MOD}.repo.get_campaign_by_id", return_value=None):
        with pytest.raises(ValueError):
            await service.sync_source_data("missing-campaign")


@pytest.mark.asyncio
async def test_csv_sync_source_data_skips_rows_missing_phone():
    service = CSVSyncService()
    campaign = {"campaign_id": "c1", "source_id": "campaigns/org-1/file.csv"}
    csv_rows = [
        ["phone_number", "name"],
        ["", "NoPhone"],
        ["+14155551234", "Alice"],
    ]
    with (
        patch(f"{MOD}.repo.get_campaign_by_id", return_value=campaign),
        patch.object(
            service, "_fetch_csv_data", new_callable=AsyncMock, return_value=csv_rows
        ),
        patch(f"{MOD}.repo.bulk_create_queued_runs") as bulk_create,
        patch(f"{MOD}.repo.update_campaign") as update_campaign,
    ):
        count = await service.sync_source_data("c1")
    assert count == 1
    bulk_create.assert_called_once()
    created_rows = bulk_create.call_args[0][0]
    assert len(created_rows) == 1
    assert created_rows[0]["context_variables"]["phone_number"] == "+14155551234"
    update_campaign.assert_called_once_with(
        "c1", total_rows=1, source_sync_status="completed"
    )
