# REST API Documentation

Complete REST API reference for the VoicEra Backend. All routes are prefixed with `/api/v1`.

For a quick endpoint lookup table, see the **[API Endpoints Reference](endpoints.md)**.

## Base URL

```
http://localhost:8000/api/v1        # Development
https://api.yourdomain.com/api/v1   # Production
```

## Authentication Schemes

| Scheme | Header | Used for |
|--------|--------|---------|
| **JWT Bearer** | `Authorization: Bearer <token>` | Dashboard users |
| **API Key** | `X-API-Key: <INTERNAL_API_KEY>` | Service-to-service (voice server) |
| **None** | — | Public endpoints (signup, login, password reset, call-recordings webhook) |

---

## User Endpoints

### Register

```http
POST /api/v1/users/signup
Content-Type: application/json

{
  "email": "user@example.com",
  "password": "SecurePass123!",
  "first_name": "Jane",
  "last_name": "Doe",
  "org_name": "Acme Corp"
}
```

**Response:** `201 Created`
```json
{
  "status": "success",
  "message": "User created",
  "org_id": "org-uuid"
}
```

### Login

```http
POST /api/v1/users/login
Content-Type: application/json

{
  "email": "user@example.com",
  "password": "SecurePass123!"
}
```

**Response:** `200 OK`
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "user": {
    "email": "user@example.com",
    "org_id": "org-uuid"
  }
}
```

### Get Current User

```http
GET /api/v1/users/me
Authorization: Bearer <token>
```

**Response:** `200 OK`
```json
{
  "email": "user@example.com",
  "first_name": "Jane",
  "last_name": "Doe",
  "org_id": "org-uuid",
  "role": "admin"
}
```

### Password Reset

```http
POST /api/v1/users/forgot-password
Content-Type: application/json

{ "email": "user@example.com" }
```

```http
POST /api/v1/users/reset-password
Content-Type: application/json

{
  "token": "reset-token-from-email",
  "new_password": "NewSecurePass456!"
}
```

---

## Agent Endpoints

### Create Agent

```http
POST /api/v1/agents
Authorization: Bearer <token>
Content-Type: application/json

{
  "agent_type": "sales-agent-v1",
  "org_id": "org-uuid",
  "agent_config": {
    "system_prompt": "You are a helpful sales agent.",
    "greeting_message": "Hello, how can I help you today?",
    "session_timeout_minutes": 10,
    "llm_model": {
      "name": "OpenAI",
      "model": "gpt-4o",
      "temperature": 0.7
    },
    "stt_model": {
      "name": "deepgram",
      "language": "English",
      "args": { "model": "nova-3" }
    },
    "tts_model": {
      "name": "cartesia",
      "language": "English",
      "args": {
        "model": "sonic-2",
        "voice_id": "bf0a246a-8642-498a-9950-80c35e9276b5"
      }
    }
  }
}
```

**Response:** `200 OK`
```json
{
  "status": "success",
  "agent_type": "sales-agent-v1",
  "org_id": "org-uuid"
}
```

### List Agents for an Organisation

```http
GET /api/v1/agents/org/{org_id}
Authorization: Bearer <token>
```

**Response:** `200 OK`
```json
[
  {
    "agent_type": "sales-agent-v1",
    "org_id": "org-uuid",
    "agent_config": { ... },
    "created_at": "2026-04-01T10:00:00Z"
  }
]
```

### Get Agent

```http
GET /api/v1/agents/{agent_type}
Authorization: Bearer <token>
```

### Update Agent

```http
PUT /api/v1/agents/{agent_type}
Authorization: Bearer <token>
Content-Type: application/json

{
  "agent_config": { ... }
}
```

### Delete Agent

```http
DELETE /api/v1/agents/{agent_type}
Authorization: Bearer <token>
```

**Response:** `200 OK`

### Get Agent Config (Voice Server)

```http
GET /api/v1/agents/config/{agent_type}
X-API-Key: <INTERNAL_API_KEY>
```

Returns the full agent configuration used by the voice server to initialise a call session.

---

## Campaign Endpoints

### Create Campaign

```http
POST /api/v1/campaigns
Authorization: Bearer <token>
Content-Type: application/json

{
  "campaign_name": "Q2-Outreach",
  "org_id": "org-uuid",
  "agent_type": "sales-agent-v1",
  "audience_name": "q2-leads",
  "caller_id": "+911234567890"
}
```

### List Campaigns for an Organisation

```http
GET /api/v1/campaigns/org/{org_id}
Authorization: Bearer <token>
```

### Get Campaign

```http
GET /api/v1/campaigns/{campaign_name}
Authorization: Bearer <token>
```

---

## Meetings (Call Logs)

### List Meetings

```http
GET /api/v1/meetings
Authorization: Bearer <token>

# Optional query parameter:
# ?agent_type=sales-agent-v1
```

**Response:** `200 OK`
```json
[
  {
    "meeting_id": "CA1234567890",
    "org_id": "org-uuid",
    "agent_type": "sales-agent-v1",
    "phone_number": "+919876543210",
    "call_type": "outbound",
    "status": "completed",
    "duration": 185,
    "start_time_utc": "2026-04-01T10:00:00Z",
    "end_time_utc": "2026-04-01T10:03:05Z",
    "transcript_content": "Agent: Hello... Caller: Hi...",
    "recording_url": "minio://recordings/CA1234567890.wav"
  }
]
```

### Get Single Meeting

```http
GET /api/v1/meetings/{meeting_id}
Authorization: Bearer <token>
```

### Stream Call Recording

```http
GET /api/v1/meetings/{meeting_id}/recording
Authorization: Bearer <token>
```

Returns the WAV audio stream for the specified call.

---

## Analytics

### Get Call Analytics

```http
GET /api/v1/analytics
Authorization: Bearer <token>

# Optional query parameters:
# ?agent_type=sales-agent-v1
# ?start_date=2026-04-01T00:00:00Z
# ?end_date=2026-04-30T23:59:59Z
# ?phone_number=%2B919876543210
```

**Response:** `200 OK`
```json
{
  "org_id": "org-uuid",
  "calls_attempted": 250,
  "calls_connected": 198,
  "average_call_duration": 142.5,
  "total_minutes_connected": 470.25,
  "most_used_agent": "sales-agent-v1",
  "most_used_agent_count": 120,
  "agent_breakdown": [
    { "agent_type": "sales-agent-v1", "call_count": 120 },
    { "agent_type": "support-bot", "call_count": 78 }
  ],
  "calculated_at": "2026-04-28T10:00:00Z"
}
```

---

## Integrations

### Add Integration

```http
POST /api/v1/integrations
Authorization: Bearer <token>
Content-Type: application/json

{
  "model": "openai",
  "api_key": "sk-...",
  "org_id": "org-uuid"
}
```

### List Integrations

```http
GET /api/v1/integrations
Authorization: Bearer <token>
```

### Delete Integration

```http
DELETE /api/v1/integrations/{model}
Authorization: Bearer <token>
```

### Get Integration Key (Voice Server)

```http
POST /api/v1/integrations/bot/get-api-key
X-API-Key: <INTERNAL_API_KEY>
Content-Type: application/json

{
  "org_id": "org-uuid",
  "model": "openai"
}
```

**Response:** `200 OK`
```json
{ "api_key": "sk-..." }
```

---

## Knowledge Base

### List Documents

```http
GET /api/v1/knowledge
Authorization: Bearer <token>
```

### Upload PDF

```http
POST /api/v1/knowledge/upload
Authorization: Bearer <token>
Content-Type: multipart/form-data

file=<PDF file>
org_id=org-uuid
```

**Response:** `202 Accepted`
```json
{
  "document_id": "doc-uuid",
  "filename": "product-manual.pdf",
  "status": "processing"
}
```

### Delete Document

```http
DELETE /api/v1/knowledge/{document_id}
Authorization: Bearer <token>
```

---

## RAG Retrieval (Voice Server)

```http
POST /api/v1/rag/retrieve
X-API-Key: <INTERNAL_API_KEY>
Content-Type: application/json

{
  "org_id": "org-uuid",
  "question": "What is the return policy?",
  "top_k": 3,
  "document_ids": ["doc-uuid"]
}
```

**Response:** `200 OK`
```json
{
  "chunks": [
    {
      "chunk_id": "doc-uuid-0",
      "document_id": "doc-uuid",
      "source_filename": "product-manual.pdf",
      "text": "Our return policy allows returns within 30 days...",
      "distance": 0.21
    }
  ]
}
```

---

## Health

```http
GET /health
```

**Response:** `200 OK`
```json
{ "status": "healthy", "database": "connected" }
```

---

## Error Responses

All errors return a consistent JSON body:

```json
{
  "detail": "Agent not found"
}
```

Common HTTP status codes:

| Code | Meaning |
|------|---------|
| 200 | Success |
| 201 | Created |
| 202 | Accepted (async processing started) |
| 400 | Bad Request |
| 401 | Unauthorized (invalid or missing token) |
| 403 | Forbidden |
| 404 | Not Found |
| 409 | Conflict |
| 500 | Internal Server Error |

---

## Next Steps

- **[API Endpoints Reference](endpoints.md)** — Complete table of all endpoints
- **[WebSocket API](websocket-api.md)** — Real-time voice communication
- **[Voice Server Service](../services/voice-server.md)** — Voice processing details
