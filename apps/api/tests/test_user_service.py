"""user_service unit tests against a hand-rolled in-memory Mongo fake."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.auth import get_password_hash
from app.database_init import ROLE_ADMIN, ROLE_MEMBER, ROLE_SUPER_ADMIN
from app.models.schemas import UserCreate
from app.services import member_service, org_service, user_service


def _matches(doc: dict[str, Any], query: dict[str, Any]) -> bool:
    for key, value in query.items():
        if isinstance(value, dict) and "$in" in value:
            if doc.get(key) not in value["$in"]:
                return False
        else:
            if doc.get(key) != value:
                return False
    return True


class Cursor(list):
    def sort(self, field: str, direction: int = 1):
        ordered = sorted(list(self), key=lambda d: d.get(field), reverse=direction == -1)
        self.clear()
        self.extend(ordered)
        return self


class FakeCollection:
    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []

    def insert_one(self, doc: dict[str, Any]) -> None:
        self.docs.append(dict(doc))

    def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        for doc in self.docs:
            if _matches(doc, query):
                return dict(doc)
        return None

    def find(self, query: dict[str, Any] | None = None, *_args, **_kwargs) -> Cursor:
        query = query or {}
        return Cursor(dict(d) for d in self.docs if _matches(d, query))

    def update_one(self, query: dict[str, Any], update: dict[str, Any]) -> MagicMock:
        for doc in self.docs:
            if _matches(doc, query):
                if "$set" in update:
                    doc.update(update["$set"])
                if "$unset" in update:
                    for key in update["$unset"]:
                        doc.pop(key, None)
                result = MagicMock()
                result.matched_count = 1
                return result
        result = MagicMock()
        result.matched_count = 0
        return result

    def update_many(self, query: dict[str, Any], update: dict[str, Any]) -> MagicMock:
        count = 0
        for doc in self.docs:
            if _matches(doc, query):
                if "$set" in update:
                    doc.update(update["$set"])
                if "$unset" in update:
                    for key in update["$unset"]:
                        doc.pop(key, None)
                count += 1
        result = MagicMock()
        result.modified_count = count
        return result

    def delete_one(self, query: dict[str, Any]) -> MagicMock:
        for idx, doc in enumerate(self.docs):
            if _matches(doc, query):
                self.docs.pop(idx)
                result = MagicMock()
                result.deleted_count = 1
                return result
        result = MagicMock()
        result.deleted_count = 0
        return result

    def delete_many(self, query: dict[str, Any]) -> MagicMock:
        before = len(self.docs)
        self.docs[:] = [d for d in self.docs if not _matches(d, query)]
        result = MagicMock()
        result.deleted_count = before - len(self.docs)
        return result

    def count_documents(self, query: dict[str, Any]) -> int:
        return sum(1 for d in self.docs if _matches(d, query))


@pytest.fixture(autouse=True)
def fake_db(monkeypatch: pytest.MonkeyPatch) -> dict[str, FakeCollection]:
    db = {
        "Users": FakeCollection(),
        "Memberships": FakeCollection(),
        "Organizations": FakeCollection(),
    }

    def get_database():
        return db

    monkeypatch.setattr(user_service, "get_database", get_database)
    monkeypatch.setattr(member_service, "get_database", get_database)
    monkeypatch.setattr(org_service, "get_database", get_database)
    return db


def _seed_org(db: dict[str, FakeCollection], org_id: str, name: str = "Acme") -> None:
    db["Organizations"].insert_one(
        {"org_id": org_id, "name": name, "created_by_email": "x@example.com"}
    )


def _seed_user(db: dict[str, FakeCollection], email: str, password: str, **extra: Any) -> None:
    db["Users"].insert_one(
        {"email": email, "password": get_password_hash(password), **extra}
    )


def _seed_membership(db: dict[str, FakeCollection], email: str, org_id: str, role: str, created_at: str) -> None:
    db["Memberships"].insert_one(
        {"email": email, "org_id": org_id, "role": role, "created_at": created_at}
    )


def test_sign_up_user_new_email_creates_org_and_membership(fake_db):
    result = user_service.sign_up_user(
        UserCreate(email="new@example.com", password="secret123", organisation_name="Acme")
    )
    assert result["status"] == "success"
    assert result["is_first_login"] is True
    user = fake_db["Users"].find_one({"email": "new@example.com"})
    assert user is not None
    membership = fake_db["Memberships"].find_one({"email": "new@example.com"})
    assert membership is not None
    assert membership["role"] == ROLE_SUPER_ADMIN


def test_sign_up_user_existing_email_wrong_password_fails(fake_db):
    _seed_user(fake_db, "existing@example.com", "correct-pass")
    result = user_service.sign_up_user(
        UserCreate(email="existing@example.com", password="wrong-pass", organisation_name="Acme2")
    )
    assert result["status"] == "fail"


def test_validate_user_and_get_token_user_not_found(fake_db):
    result = user_service.validate_user_and_get_token("nobody@example.com", "whatever")
    assert result["status"] == "fail"


def test_validate_user_and_get_token_wrong_password(fake_db):
    _seed_user(fake_db, "u@example.com", "correct-pass")
    result = user_service.validate_user_and_get_token("u@example.com", "wrong-pass")
    assert result["status"] == "fail"


def test_validate_user_falls_back_to_oldest_membership_when_default_invalid(fake_db):
    _seed_user(fake_db, "u@example.com", "correct-pass", default_org_id="org-stale")
    _seed_org(fake_db, "org-old")
    _seed_membership(fake_db, "u@example.com", "org-old", ROLE_MEMBER, "2020-01-01T00:00:00+00:00")
    result = user_service.validate_user_and_get_token("u@example.com", "correct-pass")
    assert result["status"] == "success"
    assert result["org_id"] == "org-old"


def test_switch_organisation_not_a_member_fails(fake_db):
    _seed_user(fake_db, "u@example.com", "correct-pass")
    result = user_service.switch_organisation("u@example.com", "org-none")
    assert result["status"] == "fail"


def test_delete_active_organisation_last_membership_clears_org(fake_db):
    _seed_org(fake_db, "org-1")
    _seed_membership(fake_db, "root@example.com", "org-1", ROLE_SUPER_ADMIN, "2020-01-01T00:00:00+00:00")
    result = user_service.delete_active_organisation("root@example.com", "org-1", ROLE_SUPER_ADMIN)
    assert result["status"] == "success"
    assert result["org_id"] is None
    assert result["organisations"] == []


def test_reset_password_with_token_invalid_token(fake_db):
    result = user_service.reset_password_with_token("bogus-token", "newpass123")
    assert result["status"] == "fail"


def test_sign_up_existing_email_correct_password_adds_membership(fake_db):
    _seed_user(fake_db, "existing@example.com", "correct-pass", last_logged_in_at="2020-01-01")
    result = user_service.sign_up_user(
        UserCreate(email="existing@example.com", password="correct-pass", organisation_name="Acme2")
    )
    assert result["status"] == "success"
    assert result["is_first_login"] is False
    memberships = fake_db["Memberships"].find({"email": "existing@example.com"})
    assert len(memberships) == 1


def test_check_email_for_org(fake_db):
    _seed_user(fake_db, "u@example.com", "pw")
    _seed_org(fake_db, "org-1")
    _seed_membership(fake_db, "u@example.com", "org-1", ROLE_MEMBER, "2020-01-01")
    result = user_service.check_email_for_org("u@example.com", "org-1")
    assert result["exists"] is True
    assert result["already_in_org"] is True
    assert result["can_join"] is False

    result_new = user_service.check_email_for_org("nobody@example.com")
    assert result_new["exists"] is False
    assert result_new["can_join"] is True


def test_switch_organisation_success(fake_db):
    _seed_user(fake_db, "u@example.com", "pw")
    _seed_org(fake_db, "org-1")
    _seed_membership(fake_db, "u@example.com", "org-1", ROLE_MEMBER, "2020-01-01")
    result = user_service.switch_organisation("u@example.com", "org-1")
    assert result["status"] == "success"
    assert result["org_id"] == "org-1"


def test_get_profile_user_not_found(fake_db):
    assert user_service.get_profile("nobody@example.com", None) is None


def test_get_profile_no_memberships(fake_db):
    _seed_user(fake_db, "u@example.com", "pw")
    profile = user_service.get_profile("u@example.com", None)
    assert profile["organisations"] == []
    assert profile["role"] == ROLE_MEMBER


def test_get_profile_uses_active_org(fake_db):
    _seed_user(fake_db, "u@example.com", "pw")
    _seed_org(fake_db, "org-1", "Acme")
    _seed_org(fake_db, "org-2", "Beta")
    _seed_membership(fake_db, "u@example.com", "org-1", ROLE_MEMBER, "2020-01-01")
    _seed_membership(fake_db, "u@example.com", "org-2", ROLE_ADMIN, "2020-01-02")
    profile = user_service.get_profile("u@example.com", "org-2")
    assert profile["org_id"] == "org-2"
    assert profile["role"] == ROLE_ADMIN
    assert profile["organisation_name"] == "Beta"


def test_list_organisations_for_user(fake_db):
    _seed_user(fake_db, "u@example.com", "pw")
    _seed_org(fake_db, "org-1")
    _seed_membership(fake_db, "u@example.com", "org-1", ROLE_MEMBER, "2020-01-01")
    result = user_service.list_organisations_for_user("u@example.com")
    assert result["status"] == "success"
    assert result["count"] == 1


def test_request_password_reset_unknown_user_still_success(fake_db):
    result = user_service.request_password_reset("nobody@example.com")
    assert result["status"] == "success"


def test_request_password_reset_known_user_sends_email(monkeypatch, fake_db):
    _seed_user(fake_db, "u@example.com", "pw")
    monkeypatch.setattr(user_service, "send_password_reset_email", lambda *a, **k: True)
    result = user_service.request_password_reset("u@example.com")
    assert result["status"] == "success"
    user = fake_db["Users"].find_one({"email": "u@example.com"})
    assert user.get("reset_token")


def test_request_password_reset_email_send_fails(monkeypatch, fake_db):
    _seed_user(fake_db, "u@example.com", "pw")
    monkeypatch.setattr(user_service, "send_password_reset_email", lambda *a, **k: False)
    result = user_service.request_password_reset("u@example.com")
    assert result["status"] == "fail"


def test_reset_password_with_token_success(fake_db):
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    fake_db["Users"].insert_one(
        {
            "email": "u@example.com",
            "reset_token": "tok-1",
            "reset_token_used": False,
            "reset_token_expires": future,
        }
    )
    result = user_service.reset_password_with_token("tok-1", "newpass123")
    assert result["status"] == "success"


def test_reset_password_with_token_expired(fake_db):
    expired = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    fake_db["Users"].insert_one(
        {
            "email": "u@example.com",
            "reset_token": "tok-1",
            "reset_token_used": False,
            "reset_token_expires": expired,
        }
    )
    result = user_service.reset_password_with_token("tok-1", "newpass123")
    assert result["status"] == "fail"
    assert "expired" in result["message"].lower()
