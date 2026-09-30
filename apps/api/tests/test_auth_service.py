"""auth_service unit tests: provider auth persistence and secret masking."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from cryptography.fernet import Fernet

from app.config import get_settings
from app.services import auth_service


@pytest.fixture(autouse=True)
def _fernet_key(monkeypatch: pytest.MonkeyPatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("PROVIDER_AUTH_ENCRYPTION_KEY", key)
    get_settings.cache_clear()
    import app.config as config_mod
    import app.services.secret_crypto as crypto_mod

    config_mod.settings = get_settings()
    crypto_mod.settings = config_mod.settings
    yield
    get_settings.cache_clear()
    config_mod.settings = get_settings()


class FakeCollection:
    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []

    def insert_one(self, doc: dict[str, Any]) -> None:
        self.docs.append(dict(doc))

    def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        for doc in self.docs:
            if all(doc.get(k) == v for k, v in query.items()):
                return dict(doc)
        return None

    def update_one(self, query: dict[str, Any], update: dict[str, Any]) -> MagicMock:
        for doc in self.docs:
            if all(doc.get(k) == v for k, v in query.items()):
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
            if all(doc.get(k) == v for k, v in query.items()):
                self.docs.pop(idx)
                result = MagicMock()
                result.deleted_count = 1
                return result
        result = MagicMock()
        result.deleted_count = 0
        return result

    def distinct(self, field: str, query: dict[str, Any]) -> list[Any]:
        return sorted(
            {doc[field] for doc in self.docs if all(doc.get(k) == v for k, v in query.items())}
        )

    def find(self, query: dict[str, Any]):
        return [dict(d) for d in self.docs if all(d.get(k) == v for k, v in query.items())]


@pytest.fixture(autouse=True)
def fake_db(monkeypatch: pytest.MonkeyPatch) -> dict[str, FakeCollection]:
    db = {"ProviderAuth": FakeCollection()}
    monkeypatch.setattr(auth_service, "get_database", lambda: db)
    return db


def test_upsert_provider_auth_create_then_update(fake_db):
    created = auth_service.upsert_provider_auth("org-1", "openai", {"api_key": "sk-1234abcd"})
    assert created["auth"]["api_key"] == "sk-1234abcd"
    assert len(fake_db["ProviderAuth"].docs) == 1

    updated = auth_service.upsert_provider_auth("org-1", "openai", {"api_key": "sk-newkey999"})
    assert len(fake_db["ProviderAuth"].docs) == 1
    fetched = auth_service.get_provider_auth("org-1", "openai")
    assert fetched["auth"]["api_key"] == "sk-newkey999"


def test_get_provider_auth_missing_returns_none(fake_db):
    assert auth_service.get_provider_auth("org-1", "openai") is None


def test_get_provider_auth_masks_secrets(fake_db):
    auth_service.upsert_provider_auth("org-1", "openai", {"api_key": "sk-1234abcd"})
    fetched = auth_service.get_provider_auth("org-1", "openai", mask_secrets=True)
    assert fetched["auth"]["api_key"] != "sk-1234abcd"
    assert fetched["auth"]["api_key"].endswith("abcd")
    assert fetched["auth"]["api_key"].startswith("*")


def test_mask_auth_secrets_catalog_lookup_raises_masks_all(monkeypatch):
    monkeypatch.setattr(
        auth_service,
        "provider_auth_catalog",
        MagicMock(side_effect=RuntimeError("unknown provider")),
    )
    masked = auth_service.mask_auth_secrets("bogus", {"api_key": "sk-1234abcd", "other": "val1"})
    assert masked["api_key"].startswith("*")
    assert masked["other"] == "****"


def test_mask_secret_value_short_value_fully_masked():
    assert auth_service._mask_secret_value("abcd") == "****"
    assert auth_service._mask_secret_value("ab") == "****"


def test_mask_secret_value_list_recursively_masked():
    result = auth_service._mask_secret_value(["sk-1234abcd", "ab"])
    assert result == ["*******abcd", "****"]


def test_list_configured_providers(fake_db):
    auth_service.upsert_provider_auth("org-1", "openai", {"api_key": "sk-1234abcd"})
    auth_service.upsert_provider_auth(
        "org-1", "vobiz", {"auth_id": "id-12345678", "auth_token": "tok-12345678"}
    )
    assert auth_service.list_configured_providers("org-1") == ["openai", "vobiz"]
    assert auth_service.list_configured_providers("org-missing") == []


def test_delete_provider_auth_branches(fake_db):
    assert auth_service.delete_provider_auth("org-1", "openai") is False
    auth_service.upsert_provider_auth("org-1", "openai", {"api_key": "sk-1234abcd"})
    assert auth_service.delete_provider_auth("org-1", "openai") is True
