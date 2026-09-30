"""knowledge_service unit tests: Mongo CRUD against a hand-rolled fake."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.services import knowledge_service


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

    def find(self, query: dict[str, Any]):
        matched = [dict(d) for d in self.docs if all(d.get(k) == v for k, v in query.items())]

        class Cursor(list):
            def sort(self, field, direction=1):
                ordered = sorted(list(self), key=lambda d: d.get(field), reverse=direction == -1)
                self.clear()
                self.extend(ordered)
                return self

        return Cursor(matched)

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


@pytest.fixture(autouse=True)
def fake_db(monkeypatch: pytest.MonkeyPatch) -> dict[str, FakeCollection]:
    db = {knowledge_service.COLLECTION_NAME: FakeCollection()}
    monkeypatch.setattr(knowledge_service, "get_database", lambda: db)
    return db


def test_create_document_pending_and_get(fake_db):
    document_id = knowledge_service.create_document_pending("org-1", "doc.pdf")
    doc = knowledge_service.get_document("org-1", document_id)
    assert doc is not None
    assert doc["status"] == "processing"
    assert doc["original_filename"] == "doc.pdf"


def test_get_document_not_found(fake_db):
    assert knowledge_service.get_document("org-1", "missing") is None


def test_update_document_sets_status_and_clears_error_on_ready(fake_db):
    document_id = knowledge_service.create_document_pending("org-1", "doc.pdf")
    knowledge_service.update_document(
        document_id, "org-1", status="failed", error_message="boom"
    )
    doc = knowledge_service.get_document("org-1", document_id)
    assert doc["status"] == "failed"
    assert doc["error_message"] == "boom"

    knowledge_service.update_document(document_id, "org-1", status="ready", chunk_count=5)
    doc = knowledge_service.get_document("org-1", document_id)
    assert doc["status"] == "ready"
    assert doc["chunk_count"] == 5
    assert doc["error_message"] is None


def test_list_documents_filters_by_org(fake_db):
    knowledge_service.create_document_pending("org-1", "a.pdf")
    knowledge_service.create_document_pending("org-2", "b.pdf")
    result = knowledge_service.list_documents("org-1")
    assert len(result) == 1
    assert result[0]["original_filename"] == "a.pdf"


def test_delete_knowledge_document_not_found_raises(fake_db):
    with pytest.raises(knowledge_service.KnowledgeDocumentNotFoundError):
        knowledge_service.delete_knowledge_document("org-1", "missing")


def test_delete_knowledge_document_success(fake_db):
    document_id = knowledge_service.create_document_pending(
        "org-1", "doc.pdf", storage_key="knowledge/org-1/doc-1/doc.pdf"
    )
    with (
        patch.object(knowledge_service, "_delete_chroma_vectors") as delete_chroma,
        patch.object(knowledge_service, "_delete_minio_object") as delete_minio,
    ):
        knowledge_service.delete_knowledge_document("org-1", document_id)
    delete_chroma.assert_called_once_with("org-1", document_id)
    delete_minio.assert_called_once_with("knowledge/org-1/doc-1/doc.pdf")
    assert knowledge_service.get_document("org-1", document_id) is None


def test_delete_knowledge_document_chroma_error_propagates(fake_db):
    document_id = knowledge_service.create_document_pending("org-1", "doc.pdf")
    with patch.object(
        knowledge_service,
        "_delete_chroma_vectors",
        side_effect=knowledge_service.KnowledgeChromaDeleteError("chroma down"),
    ):
        with pytest.raises(knowledge_service.KnowledgeChromaDeleteError):
            knowledge_service.delete_knowledge_document("org-1", document_id)


def test_assert_documents_ready_empty_list_raises(fake_db):
    with pytest.raises(knowledge_service.KnowledgeDocumentNotReadyError):
        knowledge_service.assert_documents_ready("org-1", [])


def test_assert_documents_ready_missing_document_raises(fake_db):
    with pytest.raises(knowledge_service.KnowledgeDocumentNotReadyError, match="Unknown"):
        knowledge_service.assert_documents_ready("org-1", ["missing-doc"])


def test_assert_documents_ready_not_ready_raises(fake_db):
    document_id = knowledge_service.create_document_pending("org-1", "doc.pdf")
    with pytest.raises(knowledge_service.KnowledgeDocumentNotReadyError, match="not ready"):
        knowledge_service.assert_documents_ready("org-1", [document_id])


def test_assert_documents_ready_all_ready_passes(fake_db):
    document_id = knowledge_service.create_document_pending("org-1", "doc.pdf")
    knowledge_service.update_document(document_id, "org-1", status="ready")
    knowledge_service.assert_documents_ready("org-1", [document_id])  # should not raise


def test_kb_storage_key_uses_safe_filename():
    key = knowledge_service.kb_storage_key("org-1", "doc-1", "../../etc/passwd.pdf")
    assert key == "knowledge/org-1/doc-1/passwd.pdf"
