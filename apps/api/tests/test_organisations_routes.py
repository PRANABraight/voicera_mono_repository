"""Organisation router tests."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import get_current_user
from app.routers import organisations


def _super_admin(org_id: str = "org-1") -> dict[str, Any]:
    return {"email": "root@example.com", "org_id": org_id, "role": "super_admin"}


def _admin() -> dict[str, Any]:
    # Zero-argument: passed directly to FastAPI's dependency_overrides, which
    # would otherwise bind the path's "org_id" instead of using a default here.
    return {"email": "admin@example.com", "org_id": "org-1", "role": "admin"}


def _make_client(user_factory) -> TestClient:
    app = FastAPI()
    app.include_router(organisations.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = user_factory
    return TestClient(app)


def test_delete_organisation_non_super_admin_forbidden():
    client = _make_client(_admin)
    response = client.delete("/api/v1/organisations/org-1")
    assert response.status_code == 403


def test_delete_organisation_wrong_org_forbidden():
    client = _make_client(lambda: _super_admin("org-1"))
    response = client.delete("/api/v1/organisations/org-2")
    assert response.status_code == 403


@patch(
    "app.routers.organisations.user_service.delete_active_organisation",
    return_value={
        "status": "success",
        "message": "Organisation deleted successfully",
        "org_id": None,
        "role": None,
        "access_token": None,
        "token_type": None,
        "organisations": [],
    },
)
def test_delete_organisation_success(delete_mock):
    client = _make_client(lambda: _super_admin("org-1"))
    response = client.delete("/api/v1/organisations/org-1")
    assert response.status_code == 200
    delete_mock.assert_called_once_with("root@example.com", "org-1", "super_admin")


@patch(
    "app.routers.organisations.user_service.delete_active_organisation",
    return_value={"status": "fail", "message": "Organization not found"},
)
def test_delete_organisation_service_failure(_delete_mock):
    client = _make_client(lambda: _super_admin("org-1"))
    response = client.delete("/api/v1/organisations/org-1")
    assert response.status_code == 400
