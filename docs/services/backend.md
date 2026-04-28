# Backend API Service

Comprehensive documentation for the VoicEra Backend API service.

## Overview

The Backend API is the central orchestrator for the VoicEra platform, built with **FastAPI** and **Python 3.10+**.

**Key Responsibilities:**
- User authentication & authorization
- Agent and campaign management
- Call log storage and retrieval
- Analytics aggregation
- MinIO storage integration
- Integration with Voice Server

## Getting Started

### Prerequisites

- Python 3.10+
- pip
- MongoDB
- MinIO (optional for local development)

### Installation

```bash
cd voicera_backend

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp env.example .env
# Edit .env with your settings
```

### Running Locally

```bash
# Development mode (auto-reload)
uvicorn app.main:app --reload --port 8000

# Production mode
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
```

### Via Docker

```bash
# Build image
docker build -t voicera-backend .

# Run container
docker run -p 8000:8000 \
  -e MONGODB_HOST=localhost \
  -e MONGODB_PORT=27017 \
  voicera-backend
```

---

## Project Structure

```
voicera_backend/
├── app/
│   ├── __init__.py
│   ├── main.py                    # Application entry point
│   ├── auth.py                    # JWT authentication & API key verification
│   ├── config.py                  # Settings and configuration
│   ├── database.py                # MongoDB connection
│   ├── models/
│   │   └── __init__.py            # Pydantic schemas
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── users.py               # User signup, login, password reset
│   │   ├── agents.py              # Agent CRUD and configuration
│   │   ├── campaigns.py           # Campaign management
│   │   ├── meetings.py            # Call log (meetings) endpoints
│   │   ├── call_recordings.py     # Recording upload webhook
│   │   ├── analytics.py           # Analytics aggregation
│   │   ├── integrations.py        # Provider API key management
│   │   ├── audience.py            # Audience management
│   │   ├── phone_numbers.py       # Phone number management
│   │   ├── members.py             # Organisation member management
│   │   ├── knowledge.py           # Knowledge base document management
│   │   ├── rag.py                 # RAG retrieval endpoint
│   │   └── vobiz.py               # Vobiz telephony integration
│   ├── services/
│   │   ├── __init__.py
│   │   ├── agent_service.py       # Agent business logic
│   │   ├── campaign_service.py    # Campaign business logic
│   │   ├── call_recording_service.py  # Recording storage
│   │   ├── analytics_service.py   # Analytics queries
│   │   ├── meeting_service.py     # Call log management
│   │   ├── audience_service.py    # Audience management
│   │   ├── member_service.py      # Member management
│   │   ├── phone_number.py        # Phone number service
│   │   ├── user_service.py        # User management
│   │   ├── email_service.py       # Email notifications (Mailtrap)
│   │   └── vobiz.py               # Vobiz API integration
│   ├── storage/
│   │   └── minio_client.py        # MinIO wrapper for recordings
│   └── utils/
│       └── mongo_utils.py         # MongoDB utility helpers
├── requirements.txt
├── .env.example
├── docker-compose.yml
└── Dockerfile
```

---

## Key Models & Schemas

### User Model

```python
# MongoDB document
{
  "_id": ObjectId,
  "id": UUID,
  "email": str,
  "password_hash": str,
  "role": "admin" | "user",
  "first_name": str,
  "last_name": str,
  "is_active": bool,
  "created_at": datetime,
  "updated_at": datetime
}
```

### Agent Model

```python
{
  "_id": ObjectId,
  "id": UUID,
  "user_id": UUID,
  "name": str,
  "description": str,
  "llm_provider": str,  # "openai", "anthropic", "local"
  "llm_model": str,     # "gpt-4", "claude-3", etc.
  "stt_provider": str,  # "deepgram", "google", "ai4bharat"
  "tts_provider": str,  # "cartesia", "google", "ai4bharat"
  "system_prompt": str,
  "language": str,      # "en", "hi", etc.
  "voice_parameters": {
    "voice_id": str,
    "speed": float,
    "tone": str
  },
  "status": "active" | "inactive" | "archived",
  "created_at": datetime,
  "updated_at": datetime
}
```

### Campaign Model

```python
{
  "_id": ObjectId,
  "id": UUID,
  "user_id": UUID,
  "agent_id": UUID,
  "name": str,
  "description": str,
  "phone_numbers": [str],
  "status": "draft" | "scheduled" | "active" | "completed" | "paused",
  "start_time": datetime,
  "end_time": datetime,
  "max_concurrent_calls": int,
  "retry_config": {
    "max_retries": int,
    "retry_delay": int
  },
  "created_at": datetime,
  "updated_at": datetime
}
```

### CallLog (Meeting) Model

```python
{
  "_id": ObjectId,
  "meeting_id": str,           # Unique call identifier from telephony provider
  "org_id": str,               # Organisation that owns the call
  "agent_type": str,           # Agent name / type used for this call
  "phone_number": str,         # Caller's phone number
  "call_type": str,            # "inbound" or "outbound"
  "status": str,               # Call status from telephony provider
  "call_busy": bool,           # Whether the call was answered
  "duration": int,             # Call duration in seconds
  "price": str,                # Telephony cost
  "start_time_utc": str,       # ISO 8601 call start timestamp
  "end_time_utc": str,         # ISO 8601 call end timestamp
  "created_at": str,           # ISO 8601 record creation timestamp
  "recording_url": str,        # minio://recordings/<call_sid>.wav
  "transcript_url": str,       # minio://transcripts/<call_sid>.txt
  "transcript_content": str,   # Full transcript text
  "call_duration": int,        # Duration from recording
}
```

---

## Core Endpoints

All routes are prefixed with `/api/v1`. See [API Endpoints Reference](../api/endpoints.md) for the complete list.

### Users & Authentication

```
POST   /api/v1/users/signup             # User registration
POST   /api/v1/users/login              # User login, returns JWT
GET    /api/v1/users/me                 # Get current user profile
GET    /api/v1/users/{email}            # Look up user by email
POST   /api/v1/users/forgot-password    # Request password reset email
POST   /api/v1/users/reset-password     # Reset password using token
```

### Agents (Assistants)

```
POST   /api/v1/agents                          # Create agent
GET    /api/v1/agents/org/{org_id}             # List agents for an org
GET    /api/v1/agents/{agent_type}             # Get agent by type/name
PUT    /api/v1/agents/{agent_type}             # Update agent configuration
DELETE /api/v1/agents/{agent_type}             # Delete agent
GET    /api/v1/agents/config/{agent_type}      # Get agent config (voice server)
GET    /api/v1/agents/by-phone/{phone_number}  # Get agent config by phone number
```

### Campaigns

```
POST   /api/v1/campaigns                       # Create campaign
GET    /api/v1/campaigns/org/{org_id}          # List campaigns for an org
GET    /api/v1/campaigns/{campaign_name}       # Get campaign by name
```

### Meetings (Call Logs)

```
POST   /api/v1/meetings                        # Create call log (voice server)
PATCH  /api/v1/meetings/{meeting_id}           # Update call end time
GET    /api/v1/meetings                        # List meetings for current org
GET    /api/v1/meetings/{meeting_id}           # Get single meeting details
GET    /api/v1/meetings/{meeting_id}/recording # Stream call recording audio
```

### Call Recordings

```
POST   /api/v1/call-recordings                 # Save recording metadata (voice server)
```

### Analytics

```
GET    /api/v1/analytics                       # Call analytics for the org
```

### Integrations

```
POST   /api/v1/integrations                    # Add provider API key
GET    /api/v1/integrations                    # List integrations
DELETE /api/v1/integrations/{model}            # Delete integration
POST   /api/v1/integrations/bot/get-api-key   # Retrieve key (voice server)
```

### Knowledge Base & RAG

```
GET    /api/v1/knowledge                       # List knowledge documents
POST   /api/v1/knowledge/upload                # Upload PDF document
DELETE /api/v1/knowledge/{document_id}         # Delete knowledge document
POST   /api/v1/rag/retrieve                    # Retrieve chunks (voice server)
```

### Health

```
GET    /health                 # MongoDB connectivity check
GET    /docs                   # Swagger UI
GET    /redoc                  # ReDoc UI
```

---

## Authentication & Authorization

### JWT Token Structure

```json
{
  "sub": "user-uuid",
  "email": "user@example.com",
  "role": "admin",
  "permissions": ["read", "write", "delete"],
  "iat": 1674003600,
  "exp": 1674007200,
  "aud": "voicera-api"
}
```

### Request Header

```http
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

### Permission Scopes

- `read:agents` - List agents
- `write:agents` - Create/edit agents
- `delete:agents` - Delete agents
- `read:campaigns` - List campaigns
- `write:campaigns` - Create/edit campaigns
- `admin` - Full access

---

## Request/Response Examples

### Create Agent

**Request:**
```bash
curl -X POST http://localhost:8000/api/v1/agents \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_type": "sales-agent-v1",
    "org_id": "org-uuid",
    "agent_config": {
      "system_prompt": "You are a helpful sales agent...",
      "greeting_message": "Hello, how can I help you today?",
      "llm_model": { "name": "OpenAI", "model": "gpt-4o" },
      "stt_model": { "name": "deepgram", "language": "English" },
      "tts_model": { "name": "cartesia", "language": "English" }
    }
  }'
```

**Response:**
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "name": "Sales Agent",
  "status": "active",
  "created_at": "2024-01-29T10:30:00Z",
  "updated_at": "2024-01-29T10:30:00Z"
}
```

### Get Meeting (Call Log) with Transcript

**Request:**
```bash
curl http://localhost:8000/api/v1/meetings/CA1234567890 \
  -H "Authorization: Bearer <token>"
```

**Response:**
```json
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
  "transcript_content": "Agent: Hello, how can I help you?...",
  "recording_url": "minio://recordings/CA1234567890.wav"
}
```

---

## Environment Configuration

See [Environment Variables Reference](../deployment/environment.md) for the full list. Key variables:

```env
# MongoDB
MONGODB_HOST=mongodb
MONGODB_PORT=27017
MONGODB_USER=admin
MONGODB_PASSWORD=admin123
MONGODB_DATABASE=voicera

# MinIO (call recordings storage)
MINIO_ENDPOINT=minio:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin

# Security
SECRET_KEY=your-long-random-secret        # JWT signing key
INTERNAL_API_KEY=your-internal-api-key    # Voice server → backend auth

# Email (Mailtrap)
MAILTRAP_API_TOKEN=
MAILTRAP_FROM_EMAIL=noreply@voicera.com
FRONTEND_URL=http://localhost:3000

# Vobiz telephony
VOBIZ_API_BASE_URL=https://api.vobiz.ai/api/v1
VOBIZ_AUTH_ID=
VOBIZ_AUTH_TOKEN=

# RAG / Knowledge Base
CHROMA_BASE_DIR=/app/rag_system/chroma_data

# Application
DEBUG=false
```

---

## Error Handling

### Standard Error Response

```json
{
  "detail": "Authentication failed",
  "error_code": "AUTH_001",
  "status_code": 401,
  "timestamp": "2024-01-29T10:30:00Z"
}
```

### Common Status Codes

| Code | Meaning |
|------|---------|
| 200 | Success |
| 201 | Created |
| 400 | Bad Request |
| 401 | Unauthorized |
| 403 | Forbidden |
| 404 | Not Found |
| 409 | Conflict |
| 429 | Rate Limited |
| 500 | Server Error |

---

## Next Steps

- **[REST API Details](../api/rest-api.md)** - Complete API documentation
- **[Quick Start](../getting-started/quickstart.md)** - Get started quickly
- **[Configuration](../getting-started/configuration.md)** - Configuration options
