"""member_service unit tests against a hand-rolled in-memory Mongo fake."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from app.auth import get_password_hash
from app.database_init import ROLE_ADMIN, ROLE_MEMBER, ROLE_SUPER_ADMIN
from app.models.schemas import AssignAdminRequest, MemberInvite, RemoveMemberRequest
from app.services import member_service, org_service


def _matches(doc: dict[str, Any], query: dict[str, Any]) -> bool:
    for key, value in query.items():
        if doc.get(key) != value:
            return False
    return True


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

    def find(self, query: dict[str, Any] | None = None):
        query = query or {}
        matched = [dict(d) for d in self.docs if _matches(d, query)]

        class Cursor(list):
            def sort(self, field, direction=1):
                ordered = sorted(list(self), key=lambda d: d.get(field), reverse=direction == -1)
                self.clear()
                self.extend(ordered)
                return self

        return Cursor(matched)

    def update_one(self, query: dict[str, Any], update: dict[str, Any]) -> MagicMock:
        for doc in self.docs:
            if _matches(doc, query):
                if "$set" in update:
                    doc.update(update["$set"])
                result = MagicMock()
                result.matched_count = 1
                return result
        result = MagicMock()
        result.matched_count = 0
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

    monkeypatch.setattr(member_service, "get_database", get_database)
    monkeypatch.setattr(org_service, "get_database", get_database)
    return db


def _seed_org(db, org_id="org-1"):
    db["Organizations"].insert_one({"org_id": org_id, "name": "Acme"})


def _seed_membership(db, email, org_id, role):
    db["Memberships"].insert_one({"email": email, "org_id": org_id, "role": role, "created_at": "2020-01-01"})


def test_invite_member_rejects_non_admin_caller(fake_db):
    result = member_service.invite_member(
        MemberInvite(email="new@example.com", password="secret123"),
        "caller@example.com",
        "org-1",
        ROLE_MEMBER,
    )
    assert result["status"] == "fail"


def test_invite_member_org_not_found(fake_db):
    result = member_service.invite_member(
        MemberInvite(email="new@example.com", password="secret123"),
        "caller@example.com",
        "org-missing",
        ROLE_ADMIN,
    )
    assert result["status"] == "fail"
    assert "not found" in result["message"].lower()


def test_invite_member_already_a_member(fake_db):
    _seed_org(fake_db)
    _seed_membership(fake_db, "new@example.com", "org-1", ROLE_MEMBER)
    result = member_service.invite_member(
        MemberInvite(email="new@example.com", password="secret123"),
        "caller@example.com",
        "org-1",
        ROLE_ADMIN,
    )
    assert result["status"] == "fail"
    assert "already" in result["message"].lower()


def test_invite_member_existing_user_wrong_password_fails(fake_db):
    _seed_org(fake_db)
    fake_db["Users"].insert_one(
        {"email": "new@example.com", "password": get_password_hash("correct-pass")}
    )
    result = member_service.invite_member(
        MemberInvite(email="new@example.com", password="wrong-pass"),
        "caller@example.com",
        "org-1",
        ROLE_ADMIN,
    )
    assert result["status"] == "fail"


def test_invite_member_existing_user_correct_password_reuses_account(fake_db):
    _seed_org(fake_db)
    fake_db["Users"].insert_one(
        {"email": "new@example.com", "password": get_password_hash("correct-pass")}
    )
    result = member_service.invite_member(
        MemberInvite(email="new@example.com", password="correct-pass"),
        "caller@example.com",
        "org-1",
        ROLE_ADMIN,
    )
    assert result["status"] == "success"
    assert len(fake_db["Users"].docs) == 1


def test_assign_admin_target_is_self_fails(fake_db):
    _seed_org(fake_db)
    result = member_service.assign_admin(
        AssignAdminRequest(email="caller@example.com"),
        "caller@example.com",
        "org-1",
        ROLE_SUPER_ADMIN,
    )
    assert result["status"] == "fail"


def test_assign_admin_target_already_super_admin_fails(fake_db):
    _seed_org(fake_db)
    _seed_membership(fake_db, "target@example.com", "org-1", ROLE_SUPER_ADMIN)
    result = member_service.assign_admin(
        AssignAdminRequest(email="target@example.com"),
        "caller@example.com",
        "org-1",
        ROLE_SUPER_ADMIN,
    )
    assert result["status"] == "fail"


def test_remove_member_removing_self_fails(fake_db):
    _seed_org(fake_db)
    result = member_service.remove_member(
        RemoveMemberRequest(email="caller@example.com"),
        "caller@example.com",
        "org-1",
        ROLE_SUPER_ADMIN,
    )
    assert result["status"] == "fail"


def test_join_organisation_org_not_found(fake_db):
    result = member_service.join_organisation("new@example.com", "secret123", "org-missing")
    assert result["status"] == "fail"


def test_join_organisation_already_member(fake_db):
    _seed_org(fake_db)
    _seed_membership(fake_db, "new@example.com", "org-1", ROLE_MEMBER)
    result = member_service.join_organisation("new@example.com", "secret123", "org-1")
    assert result["status"] == "fail"


def test_join_organisation_new_user_creates_account(fake_db):
    _seed_org(fake_db)
    result = member_service.join_organisation("brand-new@example.com", "secret123", "org-1")
    assert result["status"] == "success"
    assert result["is_first_login"] is True
    assert fake_db["Users"].find_one({"email": "brand-new@example.com"}) is not None


def test_join_organisation_existing_user_wrong_password(fake_db):
    _seed_org(fake_db)
    fake_db["Users"].insert_one(
        {"email": "existing@example.com", "password": get_password_hash("correct-pass")}
    )
    result = member_service.join_organisation("existing@example.com", "wrong-pass", "org-1")
    assert result["status"] == "fail"


def test_get_membership_found_and_missing(fake_db):
    _seed_org(fake_db)
    _seed_membership(fake_db, "u@example.com", "org-1", ROLE_MEMBER)
    assert member_service.get_membership("u@example.com", "org-1") is not None
    assert member_service.get_membership("nobody@example.com", "org-1") is None


def test_get_members_by_org_lists_members(fake_db):
    _seed_org(fake_db)
    _seed_membership(fake_db, "a@example.com", "org-1", ROLE_MEMBER)
    _seed_membership(fake_db, "b@example.com", "org-1", ROLE_ADMIN)
    result = member_service.get_members_by_org("org-1")
    assert result["status"] == "success"
    assert result["count"] == 2


def test_assign_admin_promotes_member(fake_db):
    _seed_org(fake_db)
    _seed_membership(fake_db, "target@example.com", "org-1", ROLE_MEMBER)
    result = member_service.assign_admin(
        AssignAdminRequest(email="target@example.com"),
        "caller@example.com",
        "org-1",
        ROLE_SUPER_ADMIN,
    )
    assert result["status"] == "success"
    membership = fake_db["Memberships"].find_one({"email": "target@example.com", "org_id": "org-1"})
    assert membership["role"] == ROLE_ADMIN


def test_assign_admin_target_not_found(fake_db):
    _seed_org(fake_db)
    result = member_service.assign_admin(
        AssignAdminRequest(email="ghost@example.com"),
        "caller@example.com",
        "org-1",
        ROLE_SUPER_ADMIN,
    )
    assert result["status"] == "fail"


def test_assign_admin_already_admin_is_noop_success(fake_db):
    _seed_org(fake_db)
    _seed_membership(fake_db, "target@example.com", "org-1", ROLE_ADMIN)
    result = member_service.assign_admin(
        AssignAdminRequest(email="target@example.com"),
        "caller@example.com",
        "org-1",
        ROLE_SUPER_ADMIN,
    )
    assert result["status"] == "success"
    assert "already an admin" in result["message"].lower()


def test_remove_member_target_not_found(fake_db):
    _seed_org(fake_db)
    result = member_service.remove_member(
        RemoveMemberRequest(email="ghost@example.com"),
        "caller@example.com",
        "org-1",
        ROLE_SUPER_ADMIN,
    )
    assert result["status"] == "fail"


def test_remove_member_success_deletes_user_with_no_other_memberships(fake_db):
    _seed_org(fake_db)
    _seed_membership(fake_db, "target@example.com", "org-1", ROLE_MEMBER)
    fake_db["Users"].insert_one({"email": "target@example.com", "password": "x"})
    result = member_service.remove_member(
        RemoveMemberRequest(email="target@example.com"),
        "caller@example.com",
        "org-1",
        ROLE_SUPER_ADMIN,
    )
    assert result["status"] == "success"
    assert fake_db["Memberships"].find_one({"email": "target@example.com"}) is None
    assert fake_db["Users"].find_one({"email": "target@example.com"}) is None


def test_remove_member_removing_last_super_admin_fails(fake_db):
    _seed_org(fake_db)
    _seed_membership(fake_db, "root@example.com", "org-1", ROLE_SUPER_ADMIN)
    result = member_service.remove_member(
        RemoveMemberRequest(email="root@example.com"),
        "caller@example.com",
        "org-1",
        ROLE_SUPER_ADMIN,
    )
    assert result["status"] == "fail"
    assert "last super admin" in result["message"].lower()
