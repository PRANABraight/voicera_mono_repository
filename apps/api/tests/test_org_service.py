"""org_service unit tests against a hand-rolled in-memory Mongo fake."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from app.services import org_service


def _matches(doc: dict[str, Any], query: dict[str, Any]) -> bool:
    for key, value in query.items():
        if isinstance(value, dict) and "$in" in value:
            if doc.get(key) not in value["$in"]:
                return False
        else:
            if doc.get(key) != value:
                return False
    return True


class FakeCollection:
    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []

    def insert_one(self, doc: dict[str, Any]) -> None:
        self.docs.append(dict(doc))

    def find_one(self, query: dict[str, Any], *_args) -> dict[str, Any] | None:
        for doc in self.docs:
            if _matches(doc, query):
                return dict(doc)
        return None

    def find(self, query: dict[str, Any] | None = None, *_args):
        query = query or {}
        return [dict(d) for d in self.docs if _matches(d, query)]

    def update_many(self, query: dict[str, Any], update: dict[str, Any]) -> MagicMock:
        count = 0
        for doc in self.docs:
            if _matches(doc, query):
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


@pytest.fixture(autouse=True)
def fake_db(monkeypatch: pytest.MonkeyPatch) -> dict[str, FakeCollection]:
    db = {
        "Organizations": FakeCollection(),
        "Memberships": FakeCollection(),
        "Users": FakeCollection(),
    }

    def get_database():
        return db

    monkeypatch.setattr(org_service, "get_database", get_database)
    return db


def test_get_organisation_found(fake_db):
    fake_db["Organizations"].insert_one({"org_id": "org-1", "name": "Acme"})
    org = org_service.get_organisation("org-1")
    assert org is not None
    assert org["name"] == "Acme"
    assert "_id" not in org


def test_get_organisation_not_found(fake_db):
    assert org_service.get_organisation("missing") is None


def test_get_organisation_db_exception_returns_none(monkeypatch):
    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(org_service, "get_database", boom)
    assert org_service.get_organisation("org-1") is None


def test_create_organisation_generated_id(fake_db):
    org = org_service.create_organisation(name="Acme", created_by_email="a@example.com")
    assert org["org_id"]
    assert len(org["org_id"]) == 6


def test_create_organisation_explicit_id(fake_db):
    org = org_service.create_organisation(
        name="Acme", created_by_email="a@example.com", org_id="fixed1"
    )
    assert org["org_id"] == "fixed1"


def test_get_organisations_by_ids_empty_list(fake_db):
    assert org_service.get_organisations_by_ids([]) == {}


def test_get_organisations_by_ids_multiple(fake_db):
    fake_db["Organizations"].insert_one({"org_id": "org-1", "name": "Acme"})
    fake_db["Organizations"].insert_one({"org_id": "org-2", "name": "Beta"})
    result = org_service.get_organisations_by_ids(["org-1", "org-2", "org-missing"])
    assert set(result) == {"org-1", "org-2"}
    assert result["org-1"]["name"] == "Acme"


def test_delete_organisation_not_found(fake_db):
    result = org_service.delete_organisation("missing")
    assert result["status"] == "fail"


def test_delete_organisation_removes_memberships(fake_db):
    fake_db["Organizations"].insert_one({"org_id": "org-1", "name": "Acme"})
    fake_db["Memberships"].insert_one({"email": "a@example.com", "org_id": "org-1"})
    fake_db["Users"].insert_one({"email": "a@example.com", "default_org_id": "org-1"})
    result = org_service.delete_organisation("org-1")
    assert result["status"] == "success"
    assert result["memberships_removed"] == 1
    assert org_service.get_organisation("org-1") is None
    assert fake_db["Memberships"].find({"org_id": "org-1"}) == []
