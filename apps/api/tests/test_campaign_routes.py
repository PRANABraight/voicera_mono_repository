"""Campaign API route tests."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import get_current_user
from app.routers import campaign as campaign_router
from app.services.agent_service import AgentNotFoundError


def _admin() -> dict[str, Any]:
    return {"email": "admin@example.com", "org_id": "org-1", "role": "admin"}


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(campaign_router.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = _admin
    return TestClient(app)


def test_upload_campaign_csv(client: TestClient) -> None:
    csv_body = b"phone_number,customer_name\n+14155551234,Jane\n"
    with (
        patch("app.routers.campaign.MinIOStorage") as storage_cls,
        patch("app.routers.campaign.get_sync_service") as sync_factory,
    ):
        storage_cls.return_value.put_object_bytes = AsyncMock()
        sync_service = AsyncMock()
        sync_service.validate_source.return_value = type(
            "VR",
            (),
            {
                "is_valid": True,
                "error": None,
                "headers": ["phone_number", "customer_name"],
                "rows": [["+14155551234", "Jane"]],
            },
        )()
        sync_factory.return_value = sync_service
        response = client.post(
            "/api/v1/campaign/upload",
            files={"file": ("contacts.csv", csv_body, "text/csv")},
        )
    assert response.status_code == 201
    body = response.json()
    assert body["source_id"].startswith("campaigns/org-1/")
    assert body["contact_rows"] == 1


def test_upload_campaign_csv_rejects_non_csv(client: TestClient) -> None:
    response = client.post(
        "/api/v1/campaign/upload",
        files={"file": ("contacts.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 400


def test_create_campaign_validates_agent(client: TestClient) -> None:
    with patch(
        "app.routers.campaign.agent_service.get_agent",
        side_effect=AgentNotFoundError("missing"),
    ):
        response = client.post(
            "/api/v1/campaign/create",
            json={
                "name": "Camp",
                "agent_id": "missing",
                "source_id": "campaigns/org-1/x.csv",
            },
        )
    assert response.status_code == 404


def test_upload_campaign_csv_rejects_oversized(client: TestClient) -> None:
    with patch("app.routers.campaign.settings.CAMPAIGN_MAX_CSV_BYTES", 10):
        response = client.post(
            "/api/v1/campaign/upload",
            files={"file": ("contacts.csv", b"phone_number\n" + b"1" * 20, "text/csv")},
        )
    assert response.status_code == 413


def test_create_campaign_rejects_non_telephony_agent(client: TestClient) -> None:
    with patch(
        "app.routers.campaign.agent_service.get_agent",
        return_value={"agent_category": "chat"},
    ):
        response = client.post(
            "/api/v1/campaign/create",
            json={
                "name": "Camp",
                "agent_id": "agent-1",
                "source_id": "campaigns/org-1/x.csv",
            },
        )
    assert response.status_code == 422


def test_create_campaign_rejects_agent_without_phone_number(client: TestClient) -> None:
    with (
        patch(
            "app.routers.campaign.agent_service.get_agent",
            return_value={"agent_category": "telephony", "linked_phone_number": None},
        ),
        patch("app.routers.campaign.phone_number_service.list_by_org", return_value=[]),
    ):
        response = client.post(
            "/api/v1/campaign/create",
            json={
                "name": "Camp",
                "agent_id": "agent-1",
                "source_id": "campaigns/org-1/x.csv",
            },
        )
    assert response.status_code == 422


def test_create_campaign_rejects_max_concurrency_over_org_limit(client: TestClient) -> None:
    with (
        patch(
            "app.routers.campaign.agent_service.get_agent",
            return_value={"agent_category": "telephony", "linked_phone_number": "+15551234567"},
        ),
        patch("app.routers.campaign.phone_number_service.list_by_org", return_value=[]),
        patch("app.routers.campaign.get_org_concurrent_limit", return_value=2),
    ):
        response = client.post(
            "/api/v1/campaign/create",
            json={
                "name": "Camp",
                "agent_id": "agent-1",
                "source_id": "campaigns/org-1/x.csv",
                "max_concurrency": 10,
            },
        )
    assert response.status_code == 400


def test_delete_campaign_non_admin_forbidden() -> None:
    app = FastAPI()
    app.include_router(campaign_router.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: {
        "email": "member@example.com",
        "org_id": "org-1",
        "role": "member",
    }
    client = TestClient(app)
    response = client.delete("/api/v1/campaign/c1")
    assert response.status_code == 403


def test_start_campaign_invalid_state_transition(client: TestClient) -> None:
    with (
        patch(
            "app.routers.campaign.repo.get_campaign_for_org",
            return_value={"campaign_id": "c1", "org_id": "org-1", "state": "running"},
        ),
        patch(
            "app.routers.campaign.campaign_runner_service.start_campaign",
            new_callable=AsyncMock,
            side_effect=ValueError("Campaign must be in 'created' state, current: running"),
        ),
    ):
        response = client.post("/api/v1/campaign/c1/start")
    assert response.status_code == 400


def test_pause_campaign_invalid_state_transition(client: TestClient) -> None:
    with (
        patch(
            "app.routers.campaign.repo.get_campaign_for_org",
            return_value={"campaign_id": "c1", "org_id": "org-1", "state": "created"},
        ),
        patch(
            "app.routers.campaign.campaign_runner_service.pause_campaign",
            new_callable=AsyncMock,
            side_effect=ValueError("Campaign must be running or syncing, current: created"),
        ),
    ):
        response = client.post("/api/v1/campaign/c1/pause")
    assert response.status_code == 400


def test_resume_campaign_invalid_state_transition(client: TestClient) -> None:
    with (
        patch(
            "app.routers.campaign.repo.get_campaign_for_org",
            return_value={"campaign_id": "c1", "org_id": "org-1", "state": "running"},
        ),
        patch(
            "app.routers.campaign.campaign_runner_service.resume_campaign",
            new_callable=AsyncMock,
            side_effect=ValueError("Campaign must be paused, current: running"),
        ),
    ):
        response = client.post("/api/v1/campaign/c1/resume")
    assert response.status_code == 400


def test_update_campaign_partial_update(client: TestClient) -> None:
    existing = {
        "campaign_id": "c1",
        "org_id": "org-1",
        "name": "Old Name",
        "agent_id": "agent-1",
        "source_type": "csv",
        "source_id": "campaigns/org-1/x.csv",
        "state": "created",
        "orchestrator_metadata": {},
    }
    updated = {**existing, "name": "New Name"}
    with (
        patch("app.routers.campaign.repo.get_campaign_for_org", return_value=existing),
        patch("app.routers.campaign.repo.update_campaign", return_value=updated) as update_mock,
    ):
        response = client.patch(
            "/api/v1/campaign/c1",
            json={"name": "New Name"},
        )
    assert response.status_code == 200
    assert response.json()["name"] == "New Name"
    update_mock.assert_called_once_with("c1", name="New Name")


def _campaign_doc(**overrides) -> dict:
    doc = {
        "campaign_id": "c1",
        "org_id": "org-1",
        "name": "Camp",
        "agent_id": "agent-1",
        "source_type": "csv",
        "source_id": "campaigns/org-1/x.csv",
        "state": "created",
        "orchestrator_metadata": {},
    }
    doc.update(overrides)
    return doc


def test_create_campaign_happy_path(client: TestClient) -> None:
    created = _campaign_doc()
    validation_result = type(
        "VR", (), {"is_valid": True, "error": None}
    )()
    with (
        patch(
            "app.routers.campaign.agent_service.get_agent",
            return_value={"agent_category": "telephony", "linked_phone_number": "+15551234567"},
        ),
        patch("app.routers.campaign.phone_number_service.list_by_org", return_value=[]),
        patch("app.routers.campaign.get_org_concurrent_limit", return_value=20),
        patch("app.routers.campaign.get_sync_service") as sync_factory,
        patch("app.routers.campaign.repo.create_campaign", return_value=created) as create_mock,
    ):
        sync_service = AsyncMock()
        sync_service.validate_source.return_value = validation_result
        sync_factory.return_value = sync_service
        response = client.post(
            "/api/v1/campaign/create",
            json={
                "name": "Camp",
                "agent_id": "agent-1",
                "source_id": "campaigns/org-1/x.csv",
            },
        )
    assert response.status_code == 201
    assert response.json()["campaign_id"] == "c1"
    create_mock.assert_called_once()


def test_list_campaigns(client: TestClient) -> None:
    with patch(
        "app.routers.campaign.repo.list_campaigns", return_value=[_campaign_doc()]
    ):
        response = client.get("/api/v1/campaign/")
    assert response.status_code == 200
    assert len(response.json()) == 1


def test_get_campaign_not_found(client: TestClient) -> None:
    with patch(
        "app.routers.campaign.repo.get_campaign_for_org",
        side_effect=campaign_router.CampaignNotFoundError("c1"),
    ):
        response = client.get("/api/v1/campaign/c1")
    assert response.status_code == 404


def test_get_campaign_found(client: TestClient) -> None:
    with patch(
        "app.routers.campaign.repo.get_campaign_for_org", return_value=_campaign_doc()
    ):
        response = client.get("/api/v1/campaign/c1")
    assert response.status_code == 200
    assert response.json()["campaign_id"] == "c1"


def test_delete_campaign_happy_path(client: TestClient) -> None:
    with (
        patch(
            "app.routers.campaign.repo.get_campaign_for_org", return_value=_campaign_doc()
        ),
        patch("app.routers.campaign.repo.delete_queued_runs_for_campaign") as del_runs,
        patch("app.routers.campaign.repo.delete_campaign") as del_campaign,
    ):
        response = client.delete("/api/v1/campaign/c1")
    assert response.status_code == 200
    del_runs.assert_called_once_with("c1")
    del_campaign.assert_called_once_with("c1")


def test_start_campaign_happy_path(client: TestClient) -> None:
    with (
        patch(
            "app.routers.campaign.repo.get_campaign_for_org", return_value=_campaign_doc()
        ),
        patch(
            "app.routers.campaign.campaign_runner_service.start_campaign",
            new_callable=AsyncMock,
        ) as start_mock,
    ):
        response = client.post("/api/v1/campaign/c1/start")
    assert response.status_code == 200
    assert response.json()["status"] == "started"
    start_mock.assert_called_once_with("c1")


def test_pause_campaign_happy_path(client: TestClient) -> None:
    with (
        patch(
            "app.routers.campaign.repo.get_campaign_for_org", return_value=_campaign_doc()
        ),
        patch(
            "app.routers.campaign.campaign_runner_service.pause_campaign",
            new_callable=AsyncMock,
        ),
    ):
        response = client.post("/api/v1/campaign/c1/pause")
    assert response.status_code == 200
    assert response.json()["status"] == "paused"


def test_resume_campaign_happy_path(client: TestClient) -> None:
    with (
        patch(
            "app.routers.campaign.repo.get_campaign_for_org", return_value=_campaign_doc()
        ),
        patch(
            "app.routers.campaign.campaign_runner_service.resume_campaign",
            new_callable=AsyncMock,
        ),
    ):
        response = client.post("/api/v1/campaign/c1/resume")
    assert response.status_code == 200
    assert response.json()["status"] == "resumed"


def test_get_campaign_runs(client: TestClient) -> None:
    with (
        patch(
            "app.routers.campaign.repo.get_campaign_for_org", return_value=_campaign_doc()
        ),
        patch(
            "app.routers.campaign.list_call_logs_by_campaign",
            return_value=[{"call_id": "call-1"}],
        ),
        patch(
            "app.routers.campaign.transform_call_log_urls", side_effect=lambda log: log
        ),
    ):
        response = client.get("/api/v1/campaign/c1/runs")
    assert response.status_code == 200
    assert response.json() == [{"call_id": "call-1"}]


def test_get_campaign_progress(client: TestClient) -> None:
    with (
        patch(
            "app.routers.campaign.repo.get_campaign_for_org", return_value=_campaign_doc()
        ),
        patch(
            "app.routers.campaign.campaign_runner_service.get_campaign_status",
            new_callable=AsyncMock,
            return_value={
                "campaign_id": "c1",
                "state": "running",
                "total_rows": 10,
                "processed_rows": 5,
                "failed_rows": 0,
                "progress_percentage": 50.0,
                "rate_limit": 1,
                "started_at": None,
                "completed_at": None,
            },
        ),
    ):
        response = client.get("/api/v1/campaign/c1/progress")
    assert response.status_code == 200
    assert response.json()["processed_rows"] == 5


def test_source_download_url(client: TestClient) -> None:
    with (
        patch(
            "app.routers.campaign.repo.get_campaign_for_org", return_value=_campaign_doc()
        ),
        patch("app.routers.campaign.MinIOStorage") as storage_cls,
    ):
        storage_cls.return_value.presigned_get_url.return_value = "https://minio/x"
        response = client.get("/api/v1/campaign/c1/source-download-url")
    assert response.status_code == 200
    assert response.json()["download_url"] == "https://minio/x"
