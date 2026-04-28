# Knowledge Base

Comprehensive documentation for the VoicEra Knowledge Base service — RAG-powered document retrieval for voice agents.

## Overview

The Knowledge Base feature lets you attach PDF documents to an agent so it can answer questions grounded in your content during live calls. It uses **Retrieval-Augmented Generation (RAG)**: PDFs are chunked and embedded into a per-organisation [ChromaDB](https://www.trychroma.com/) vector store at upload time; at call time the voice server queries the backend for the most relevant chunks and injects them into the LLM context.

```
Browser / Dashboard
       │ JWT
       ▼
Next.js API proxy
       │ JWT
       ▼
Backend  ── knowledge router  ──→  MongoDB (KnowledgeDocuments)
         ── rag router        ──→  Chroma on disk  ◀── ingest pipeline
                                                            ▲
                                                    OpenAI embeddings
                                                    (via Integrations)
```

---

## Architecture

### Storage layers

| Layer | What is stored | Where |
|-------|---------------|-------|
| **MongoDB `KnowledgeDocuments`** | Document metadata: filename, status, chunk count, errors | Backend MongoDB |
| **ChromaDB (disk)** | Vector embeddings + chunk text | `CHROMA_BASE_DIR/orgs/<sha256(org_id)>/` |

### Isolation model

Each organisation gets its own **hashed subdirectory** under `CHROMA_BASE_DIR`. Two orgs can never see each other's vectors.

### Embedding model

Chunks are embedded using **OpenAI `text-embedding-3-small`**. The same model is used at ingest and at retrieval, so distances are meaningful. The OpenAI API key comes from the org's configured **Integration** (not from a global `.env` key).

---

## Document lifecycle

```
Upload (PDF ≤ 25 MB)
        │
        ▼
  status: "processing"  ◀── Mongo row inserted, HTTP 202 returned
        │
        ▼  background task
  ingest_pipeline.ingest_pdf_bytes
    ├── pdf → text (PyMuPDF)
    ├── text → chunks
    ├── chunks → OpenAI embeddings
    └── upsert into Chroma  (collection: rag_docs, metadata: document_id)
        │
        ├── success → status: "ready", chunk_count updated
        └── failure → status: "failed", error_message set
```

---

## API Endpoints

All knowledge endpoints are prefixed with `/api/v1`.

### List documents

```
GET /api/v1/knowledge
```

Returns all knowledge documents belonging to the caller's organisation.

**Auth:** JWT Bearer token (dashboard users).

**Response:**

```json
[
  {
    "document_id": "a1b2c3d4-...",
    "filename": "product-manual.pdf",
    "status": "ready",
    "chunk_count": 142,
    "embedding_model": "text-embedding-3-small",
    "error_message": null,
    "created_at": "2024-05-01T10:00:00Z"
  }
]
```

**Status values:**

| Status | Meaning |
|--------|---------|
| `processing` | Ingest pipeline is running in the background |
| `ready` | Embedded and queryable |
| `failed` | Ingest failed — see `error_message` |

---

### Upload a PDF

```
POST /api/v1/knowledge/upload
Content-Type: multipart/form-data
```

**Auth:** JWT Bearer token.

**Form fields:**

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `file` | PDF file | Yes | `.pdf` extension only, max **25 MB** |
| `org_id` | string | Yes | Must match the caller's organisation |

**Response:**

```json
{
  "document_id": "a1b2c3d4-...",
  "filename": "product-manual.pdf",
  "status": "processing"
}
```

The ingest pipeline runs as a **background task**; the response is returned immediately with `status: processing`.

---

### Delete a document

```
DELETE /api/v1/knowledge/{document_id}
```

**Auth:** JWT Bearer token.

Deletes the Mongo metadata row **and** removes the corresponding vector chunks from Chroma on disk.

**Response:**

```json
{ "deleted": true }
```

**Error codes:**

| Code | Reason |
|------|--------|
| `404` | Document not found for this org |
| `500` | Chroma deletion failed |

---

### Retrieve chunks (service-to-service)

```
POST /api/v1/rag/retrieve
```

Used at call time by the voice server to fetch the most relevant document chunks for a given user utterance.

**Auth:** `X-API-Key: <INTERNAL_API_KEY>` header (not JWT — this is a service-to-service call).

**Request body:**

```json
{
  "org_id": "org-123",
  "question": "What is the return policy?",
  "top_k": 3,
  "document_ids": ["a1b2c3d4-...", "e5f6g7h8-..."]
}
```

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `org_id` | string | required | |
| `question` | string | required | The user's utterance |
| `top_k` | integer | `3` | Number of chunks to return |
| `document_ids` | list[string] | optional | Filter to specific documents; omit to search all |

**Response:**

```json
{
  "chunks": [
    {
      "chunk_id": "a1b2c3d4-0",
      "document_id": "a1b2c3d4-...",
      "source_filename": "product-manual.pdf",
      "text": "Our return policy allows returns within 30 days...",
      "distance": 0.21
    }
  ]
}
```

---

## Agent configuration

Enable knowledge base on an agent from the **Assistants** page. The following fields are stored in the agent's `agent_config`:

| Field | Type | Description |
|-------|------|-------------|
| `knowledge_base_enabled` | boolean | Whether retrieval is active for this agent |
| `knowledge_document_ids` | list[string] | Which uploaded documents this agent may search |
| `knowledge_top_k` | integer | Number of chunks injected per user turn (default 3) |

!!! note
    Knowledge base is currently supported only when the LLM provider is set to **OpenAI** (requires the org's OpenAI Integration to be configured).

### Runtime retrieval flow

```
User speaks
     │
Voice server transcribes (STT)
     │
OpenAIKnowledgeLLMService._process_context
     │ if knowledge_base_enabled and document_ids present
     ▼
POST /api/v1/rag/retrieve  ──→  Backend embeds question → Chroma ANN search
     │                          returns top_k chunks
     ▼
Chunks prepended to LLM prompt as context
     │
LLM generates grounded response
     │
Agent speaks (TTS)
```

---

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CHROMA_BASE_DIR` | `voicera_backend/rag_system/chroma_data` | Root directory for per-org Chroma stores |
| `INTERNAL_API_KEY` | *(required)* | Shared secret for voice server → backend calls |

!!! warning
    `CHROMA_BASE_DIR` must resolve to the **same path** from both the backend container and any process that performs retrieval. In Docker Compose, use a named volume or a bind mount.

---

## Docker / deployment notes

The RAG ingest pipeline runs **inside the backend container** — no separate service is needed.

The `voicera_backend/Dockerfile` installs `chromadb` and its native dependencies (`gcc`, `g++`, `libgomp1` on slim images). Chroma data lands under `CHROMA_BASE_DIR` on the host when using a bind mount.

```yaml
# docker-compose.yml (excerpt)
backend:
  build: ./voicera_backend
  volumes:
    - ./voicera_backend:/app   # dev bind mount; chroma_data is inside
  environment:
    - CHROMA_BASE_DIR=/app/rag_system/chroma_data
    - INTERNAL_API_KEY=${INTERNAL_API_KEY}
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| Document stuck in `processing` | Backend crash during ingest | Check backend logs; re-upload |
| Document `failed` | Missing OpenAI Integration for org | Configure OpenAI API key under **Integrations** |
| Agent returns no KB context on calls | `CHROMA_BASE_DIR` mismatch between containers | Ensure both containers mount the same Chroma directory |
| `500` on `/rag/retrieve` | Chroma collection not found for org | Delete and re-upload the document |
| Upload rejected with 400 | File is not a PDF or exceeds 25 MB | Convert or compress the file |
