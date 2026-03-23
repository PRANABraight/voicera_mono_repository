"""
Single-file HTTP server: PDF upload -> extract -> chunk -> embed -> Chroma.

Delegates to ingest_pipeline.py. Does not remove the standalone CLI scripts.

Run from voicera_backend:

  cd voicera_backend
  uvicorn rag_system.rag_server:app --host 0.0.0.0 --port 8090 --reload
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from fastapi import FastAPI, File, Header, HTTPException, Query, UploadFile
from pydantic import BaseModel

from rag_system.ingest_pipeline import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_CHROMA_DIR,
    DEFAULT_COLLECTION,
    DEFAULT_EMBED_MODEL,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_OVERLAP,
    IngestPipelineError,
    delete_chunks_for_document,
    ingest_pdf_bytes,
)
from rag_system.rag_env import load_rag_env

load_rag_env()

app = FastAPI(
    title="Voicera RAG ingest server",
    description="Upload a PDF; text is chunked, embedded with OpenAI, and stored in Chroma.",
    version="1.0.0",
)


class DeleteDocumentResponse(BaseModel):
    deleted: bool


class IngestResponse(BaseModel):
    status: str
    ingest_id: str
    filename: str
    characters_extracted: int
    num_chunks: int
    embedding_dim: int
    chroma_dir: str
    collection: str
    embedding_model: str


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.delete("/document", response_model=DeleteDocumentResponse)
def delete_document_chunks(
    document_id: str = Query(..., description="Knowledge document_id / chunk_id_prefix"),
    chroma_dir: str = Query(..., description="Same persist dir used for ingest"),
    collection: str = DEFAULT_COLLECTION,
) -> DeleteDocumentResponse:
    """Remove all chunks for a document from Chroma (Knowledge Base delete via HTTP)."""
    delete_chunks_for_document(Path(chroma_dir), document_id, collection)
    return DeleteDocumentResponse(deleted=True)


@app.post("/ingest", response_model=IngestResponse)
async def ingest_pdf(
    file: UploadFile = File(..., description="PDF file (text-based; no OCR)"),
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_OVERLAP,
    collection: str = DEFAULT_COLLECTION,
    chroma_dir: str | None = None,
    embedding_model: str = DEFAULT_EMBED_MODEL,
    dimensions: int | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    reset_collection: bool = False,
    chunk_id_prefix: str | None = Query(
        None,
        description="Optional; default random UUID. Knowledge Base passes document_id.",
    ),
    org_id: str | None = Query(None, description="Stored in chunk metadata when set."),
    document_id: str | None = Query(None, description="Same as chunk_id_prefix for KB metadata."),
    x_openai_api_key: str | None = Header(
        None,
        alias="X-OpenAI-API-Key",
        description="Optional; overrides env OPENAI_API_KEY for this request.",
    ),
) -> IngestResponse:
    """
    Full pipeline: PDF -> text -> chunks -> OpenAI embeddings -> Chroma upsert.

    Each upload gets a unique id; chunk ids are ``{id}_{i}`` unless reset_collection clears the collection.
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Expected a .pdf file")

    content = await file.read()
    out_dir = Path(chroma_dir) if chroma_dir else DEFAULT_CHROMA_DIR
    raw_prefix = (chunk_id_prefix or document_id or "").strip()
    ingest_id = raw_prefix if raw_prefix else str(uuid.uuid4())

    try:
        result = ingest_pdf_bytes(
            pdf_bytes=content,
            filename=file.filename,
            chunk_id_prefix=ingest_id,
            chroma_dir=out_dir,
            collection=collection,
            embedding_model=embedding_model,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            dimensions=dimensions,
            batch_size=batch_size,
            reset_collection=reset_collection,
            openai_api_key=(x_openai_api_key.strip() if x_openai_api_key else None),
            org_id=org_id,
            document_id=document_id or ingest_id,
        )
    except IngestPipelineError as e:
        msg = str(e)
        code = 503 if "API key" in msg else 400
        raise HTTPException(status_code=code, detail=msg) from e

    return IngestResponse(
        status="ok",
        ingest_id=result.chunk_id_prefix,
        filename=result.filename,
        characters_extracted=result.characters_extracted,
        num_chunks=result.num_chunks,
        embedding_dim=result.embedding_dim,
        chroma_dir=result.chroma_dir,
        collection=result.collection,
        embedding_model=result.embedding_model,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("RAG_SERVER_PORT", "8090")),
    )
