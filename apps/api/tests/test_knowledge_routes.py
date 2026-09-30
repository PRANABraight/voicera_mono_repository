"""Knowledge router tests: upload validation, preview, and delete branches."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import get_current_user
from app.routers import knowledge
from app.services import knowledge_service


def _admin(org_id: str | None = "org-1") -> dict[str, Any]:
    return {"email": "admin@example.com", "org_id": org_id, "role": "admin"}


def _make_client(user_factory=_admin) -> TestClient:
    app = FastAPI()
    app.include_router(knowledge.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = user_factory
    return TestClient(app)


def test_upload_rejects_wrong_filetype():
    client = _make_client()
    response = client.post(
        "/api/v1/knowledge/upload",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 400


def test_upload_rejects_empty_file():
    client = _make_client()
    response = client.post(
        "/api/v1/knowledge/upload",
        files={"file": ("doc.pdf", b"", "application/pdf")},
    )
    assert response.status_code == 400


def test_upload_rejects_oversized_file(monkeypatch):
    monkeypatch.setattr(knowledge.settings, "KB_MAX_UPLOAD_BYTES", 10)
    client = _make_client()
    response = client.post(
        "/api/v1/knowledge/upload",
        files={"file": ("doc.pdf", b"x" * 20, "application/pdf")},
    )
    assert response.status_code == 413


@patch("app.routers.knowledge.knowledge_service.update_document")
@patch(
    "app.routers.knowledge.knowledge_service.upload_pdf_to_minio",
    side_effect=RuntimeError("minio down"),
)
@patch(
    "app.routers.knowledge.knowledge_service.create_document_pending",
    return_value="doc-1",
)
def test_upload_pdf_to_minio_raises_marks_failed_and_500(
    _create_mock, _upload_mock, update_mock
):
    client = _make_client()
    response = client.post(
        "/api/v1/knowledge/upload",
        files={"file": ("doc.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert response.status_code == 500
    update_mock.assert_called_once()
    assert update_mock.call_args[1]["status"] == "failed"


@patch("app.routers.knowledge.knowledge_service.get_document", return_value=None)
def test_preview_document_not_found(_get_mock):
    client = _make_client()
    response = client.get("/api/v1/knowledge/doc-1/preview")
    assert response.status_code == 404


@patch(
    "app.routers.knowledge.knowledge_service.get_document",
    return_value={"document_id": "doc-1", "org_id": "org-1"},
)
def test_preview_document_no_storage_key(_get_mock):
    client = _make_client()
    response = client.get("/api/v1/knowledge/doc-1/preview")
    assert response.status_code == 404


@patch("app.routers.knowledge.MinIOStorage")
@patch(
    "app.routers.knowledge.knowledge_service.get_document",
    return_value={"document_id": "doc-1", "org_id": "org-1", "storage_key": "key-1"},
)
def test_preview_document_missing_object_in_minio(_get_mock, storage_cls):
    storage_cls.return_value.object_exists.return_value = False
    client = _make_client()
    response = client.get("/api/v1/knowledge/doc-1/preview")
    assert response.status_code == 404


@patch(
    "app.routers.knowledge.knowledge_service.delete_knowledge_document",
    side_effect=knowledge_service.KnowledgeDocumentNotFoundError(),
)
def test_delete_document_not_found(_delete_mock):
    client = _make_client()
    response = client.delete("/api/v1/knowledge/doc-1")
    assert response.status_code == 404


@patch(
    "app.routers.knowledge.knowledge_service.delete_knowledge_document",
    side_effect=knowledge_service.KnowledgeChromaDeleteError("chroma failed"),
)
def test_delete_document_chroma_error(_delete_mock):
    client = _make_client()
    response = client.delete("/api/v1/knowledge/doc-1")
    assert response.status_code == 500


@patch("app.routers.knowledge.knowledge_service.delete_knowledge_document")
def test_delete_document_success(delete_mock):
    client = _make_client()
    response = client.delete("/api/v1/knowledge/doc-1")
    assert response.status_code == 200
    assert response.json()["deleted"] is True
    delete_mock.assert_called_once_with("org-1", "doc-1")


def test_upload_no_org_in_token():
    client = _make_client(lambda: _admin(None))
    response = client.post(
        "/api/v1/knowledge/upload",
        files={"file": ("doc.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert response.status_code == 400
