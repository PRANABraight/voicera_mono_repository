"""Member router tests."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import get_current_user
from app.routers import members


def _admin() -> dict[str, Any]:
    # Zero-argument: passed directly to FastAPI's dependency_overrides, which
    # would otherwise bind a same-named path param (e.g. "org_id" on
    # GET /{org_id}) instead of using a default here.
    return {"email": "admin@example.com", "org_id": "org-1", "role": "admin"}


def _super_admin() -> dict[str, Any]:
    return {"email": "root@example.com", "org_id": "org-1", "role": "super_admin"}


def _no_org() -> dict[str, Any]:
    return {"email": "admin@example.com", "org_id": None, "role": "admin"}


def _make_client(user_factory) -> TestClient:
    app = FastAPI()
    app.include_router(members.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = user_factory
    return TestClient(app)


@patch(
    "app.routers.members.member_service.invite_member",
    return_value={
        "status": "success",
        "message": "Member invited successfully",
        "org_id": "org-1",
        "role": "member",
    },
)
def test_invite_member_success(invite_mock):
    client = _make_client(_admin)
    response = client.post(
        "/api/v1/members/invite",
        json={"email": "new@example.com", "password": "secret123"},
    )
    assert response.status_code == 201
    invite_mock.assert_called_once()
    args = invite_mock.call_args[0]
    assert args[2] == "org-1"


def test_invite_member_no_org_in_token():
    client = _make_client(_no_org)
    response = client.post(
        "/api/v1/members/invite",
        json={"email": "new@example.com", "password": "secret123"},
    )
    assert response.status_code == 400


@patch(
    "app.routers.members.member_service.join_organisation",
    return_value={"status": "fail", "message": "Organization not found"},
)
def test_join_organisation_fail(_join_mock):
    client = _make_client(_admin)
    response = client.post(
        "/api/v1/members/join",
        json={"email": "a@example.com", "password": "secret123", "org_id": "bad-org"},
    )
    assert response.status_code == 400


@patch(
    "app.routers.members.user_service.auth_response_for_membership",
    return_value={"status": "success", "message": "Joined organisation successfully"},
)
@patch(
    "app.routers.members.member_service.join_organisation",
    return_value={
        "status": "success",
        "message": "Joined organisation successfully",
        "email": "a@example.com",
        "org_id": "org-1",
        "role": "member",
        "is_first_login": True,
    },
)
def test_join_organisation_success(_join_mock, _auth_mock):
    client = _make_client(_admin)
    response = client.post(
        "/api/v1/members/join",
        json={"email": "a@example.com", "password": "secret123", "org_id": "org-1"},
    )
    assert response.status_code == 201


@patch("app.routers.members.member_service.get_membership", return_value=None)
def test_get_members_forbidden_when_not_a_member(_membership_mock):
    client = _make_client(_admin)
    response = client.get("/api/v1/members/other-org")
    assert response.status_code == 403


@patch(
    "app.routers.members.member_service.get_members_by_org",
    return_value={"status": "success", "members": [], "count": 0},
)
@patch(
    "app.routers.members.member_service.get_membership",
    return_value={"email": "admin@example.com", "org_id": "org-1", "role": "admin"},
)
def test_get_members_success(_membership_mock, _members_mock):
    client = _make_client(_admin)
    response = client.get("/api/v1/members/org-1")
    assert response.status_code == 200
    assert response.json()["count"] == 0


def test_assign_admin_forbidden_for_non_super_admin():
    client = _make_client(_admin)
    response = client.post(
        "/api/v1/members/assign-admin",
        json={"email": "member@example.com"},
    )
    assert response.status_code == 403


@patch(
    "app.routers.members.member_service.assign_admin",
    return_value={"status": "success", "message": "Member promoted to admin"},
)
def test_assign_admin_success(_assign_mock):
    client = _make_client(_super_admin)
    response = client.post(
        "/api/v1/members/assign-admin",
        json={"email": "member@example.com"},
    )
    assert response.status_code == 200


@patch(
    "app.routers.members.member_service.remove_member",
    return_value={"status": "success", "message": "Member removed successfully"},
)
def test_remove_member_success(_remove_mock):
    client = _make_client(_super_admin)
    response = client.post(
        "/api/v1/members/remove",
        json={"email": "member@example.com"},
    )
    assert response.status_code == 200


@patch(
    "app.routers.members.member_service.remove_member",
    return_value={"status": "fail", "message": "Cannot remove the last super admin"},
)
def test_remove_member_failure(_remove_mock):
    client = _make_client(_super_admin)
    response = client.post(
        "/api/v1/members/remove",
        json={"email": "member@example.com"},
    )
    assert response.status_code == 400


def test_remove_member_forbidden_for_non_super_admin():
    client = _make_client(_admin)
    response = client.post(
        "/api/v1/members/remove",
        json={"email": "member@example.com"},
    )
    assert response.status_code == 403
