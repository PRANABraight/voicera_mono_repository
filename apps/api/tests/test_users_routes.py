"""Users router tests: signup, login, forgot/reset password, check-email."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import get_current_user
from app.routers import users


def _user() -> dict[str, Any]:
    # NOTE: this is passed straight to FastAPI's dependency_overrides, which
    # inspects its signature like any other dependency. A parameter named the
    # same as a path param (e.g. "email" on GET /{email}) would get bound from
    # the URL instead of using the default here — keep this zero-argument.
    return {"email": "u@example.com", "org_id": "org-1", "role": "admin"}


def _make_client(user_factory=_user) -> TestClient:
    app = FastAPI()
    app.include_router(users.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = user_factory
    return TestClient(app)


@patch(
    "app.routers.users.user_service.sign_up_user",
    return_value={"status": "fail", "message": "That password doesn't match your existing account."},
)
def test_signup_fail(_signup_mock):
    client = _make_client()
    response = client.post(
        "/api/v1/users/signup",
        json={"email": "u@example.com", "password": "secret123", "organisation_name": "Acme"},
    )
    assert response.status_code == 400


@patch(
    "app.routers.users.user_service.sign_up_user",
    return_value={
        "status": "success",
        "message": "Organisation created successfully",
        "access_token": "tok",
        "token_type": "bearer",
        "org_id": "org-1",
        "role": "super_admin",
        "organisations": [],
        "is_first_login": True,
    },
)
def test_signup_success(_signup_mock):
    client = _make_client()
    response = client.post(
        "/api/v1/users/signup",
        json={"email": "u@example.com", "password": "secret123", "organisation_name": "Acme"},
    )
    assert response.status_code == 201


@patch(
    "app.routers.users.user_service.validate_user_and_get_token",
    return_value={"status": "fail", "message": "Invalid password"},
)
def test_login_invalid_credentials(_login_mock):
    client = _make_client()
    response = client.post(
        "/api/v1/users/login",
        json={"email": "u@example.com", "password": "wrong"},
    )
    assert response.status_code == 401


@patch(
    "app.routers.users.user_service.check_email_for_org",
    return_value={"exists": True, "already_in_org": False, "can_join": True},
)
def test_check_email_passthrough(check_mock):
    client = _make_client()
    response = client.get("/api/v1/users/check/u@example.com")
    assert response.status_code == 200
    assert response.json()["exists"] is True
    check_mock.assert_called_once_with("u@example.com", None)


@patch(
    "app.routers.users.user_service.request_password_reset",
    return_value={"status": "success", "message": "If user exists, password reset email has been sent"},
)
def test_forgot_password_success(_reset_mock):
    client = _make_client()
    response = client.post(
        "/api/v1/users/forgot-password", json={"email": "u@example.com"}
    )
    assert response.status_code == 200


@patch(
    "app.routers.users.user_service.request_password_reset",
    return_value={"status": "fail", "message": "Error: db down"},
)
def test_forgot_password_fail(_reset_mock):
    client = _make_client()
    response = client.post(
        "/api/v1/users/forgot-password", json={"email": "u@example.com"}
    )
    assert response.status_code == 400


@patch(
    "app.routers.users.user_service.reset_password_with_token",
    return_value={"status": "success", "message": "Password reset successfully"},
)
def test_reset_password_success(_reset_mock):
    client = _make_client()
    response = client.post(
        "/api/v1/users/reset-password",
        json={"token": "tok-1", "new_password": "newpass123"},
    )
    assert response.status_code == 200


@patch(
    "app.routers.users.user_service.reset_password_with_token",
    return_value={"status": "fail", "message": "Invalid or expired reset token"},
)
def test_reset_password_fail(_reset_mock):
    client = _make_client()
    response = client.post(
        "/api/v1/users/reset-password",
        json={"token": "tok-1", "new_password": "newpass123"},
    )
    assert response.status_code == 400


def test_get_user_forbidden_for_other_email():
    client = _make_client()
    response = client.get("/api/v1/users/other@example.com")
    assert response.status_code == 403


@patch(
    "app.routers.users.user_service.get_profile",
    return_value={
        "email": "u@example.com",
        "org_id": "org-1",
        "role": "admin",
        "organisation_name": "Acme",
        "organisations": [],
        "created_at": "2020-01-01",
    },
)
def test_get_user_self_success(_profile_mock):
    client = _make_client()
    response = client.get("/api/v1/users/u@example.com")
    assert response.status_code == 200


@patch("app.routers.users.user_service.get_profile", return_value=None)
def test_get_current_user_info_not_found(_profile_mock):
    client = _make_client()
    response = client.get("/api/v1/users/me")
    assert response.status_code == 404


@patch(
    "app.routers.users.user_service.switch_organisation",
    return_value={"status": "fail", "message": "Not a member of this organization"},
)
def test_switch_organisation_fail(_switch_mock):
    client = _make_client()
    response = client.post(
        "/api/v1/users/switch-organisation", json={"org_id": "org-2"}
    )
    assert response.status_code == 400


@patch(
    "app.routers.users.user_service.list_organisations_for_user",
    return_value={"status": "fail", "message": "boom"},
)
def test_list_organisations_fail(_list_mock):
    client = _make_client()
    response = client.get("/api/v1/users/organisations")
    assert response.status_code == 500
