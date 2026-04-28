# API Endpoints Reference

Quick reference for all VoicEra Backend API endpoints. All routes are prefixed with `/api/v1`.

## Authentication Schemes

| Scheme | Used for |
|--------|---------|
| **JWT Bearer** | Dashboard users — attach `Authorization: Bearer <token>` |
| **API Key** | Service-to-service calls — attach `X-API-Key: <INTERNAL_API_KEY>` |
| **None** | Public endpoints (signup, login, password reset, call-recordings webhook) |

---

## Users

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/api/v1/users/signup` | None | Register new user / organisation |
| POST | `/api/v1/users/login` | None | Login, receive JWT |
| GET | `/api/v1/users/me` | JWT | Current user profile |
| GET | `/api/v1/users/{email}` | JWT | Look up user by email |
| POST | `/api/v1/users/forgot-password` | None | Request password reset email |
| POST | `/api/v1/users/reset-password` | None | Reset password using token |

---

## Agents (Assistants)

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/api/v1/agents` | JWT | Create a new agent |
| GET | `/api/v1/agents/org/{org_id}` | JWT | List all agents for an organisation |
| GET | `/api/v1/agents/{agent_type}` | JWT | Get agent by type/name |
| PUT | `/api/v1/agents/{agent_type}` | JWT | Update agent configuration |
| DELETE | `/api/v1/agents/{agent_type}` | JWT | Delete agent |
| GET | `/api/v1/agents/config/{agent_type}` | API Key | Get agent config (voice server → backend) |
| GET | `/api/v1/agents/config/id/{agent_id}` | API Key | Get agent config by ID |
| GET | `/api/v1/agents/by-phone/{phone_number}` | API Key | Get agent config by phone number |

---

## Meetings (Call Logs)

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/api/v1/meetings` | API Key | Create call log entry (voice server → backend) |
| PATCH | `/api/v1/meetings/{meeting_id}` | API Key | Update call end time |
| GET | `/api/v1/meetings` | JWT | List meetings for the current org (optional `?agent_type=`) |
| GET | `/api/v1/meetings/{meeting_id}` | JWT | Get single meeting details |
| GET | `/api/v1/meetings/{meeting_id}/recording` | JWT | Stream call recording audio |

---

## Call Recordings

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/api/v1/call-recordings` | None* | Save recording metadata (voice server → backend) |

*Unauthenticated by design — restrict at network level in production.

---

## Campaigns

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/api/v1/campaigns` | JWT | Create campaign |
| GET | `/api/v1/campaigns/org/{org_id}` | JWT | List all campaigns for an org |
| GET | `/api/v1/campaigns/{campaign_name}` | JWT | Get campaign by name |

---

## Audiences

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/api/v1/audience` | JWT | Create audience |
| GET | `/api/v1/audience` | JWT | List all audiences (optional `?phone_number=`) |
| GET | `/api/v1/audience/{audience_name}` | JWT | Get audience by name |

---

## Phone Numbers

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/api/v1/phone-numbers/org/{org_id}` | JWT | List all phone numbers for an org |
| GET | `/api/v1/phone-numbers/agent/{agent_type}` | JWT | Get number attached to agent |
| POST | `/api/v1/phone-numbers/attach` | JWT | Attach phone number to agent |
| DELETE | `/api/v1/phone-numbers/detach` | JWT | Detach phone number from agent |

---

## Analytics

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/api/v1/analytics` | JWT | Get call analytics for the org |

**Query parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `agent_type` | string | Filter by agent |
| `phone_number` | string | Filter by phone number |
| `start_date` | ISO 8601 string | Start of date range |
| `end_date` | ISO 8601 string | End of date range |

---

## Integrations

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/api/v1/integrations` | JWT | Add an integration (API key for a provider) |
| GET | `/api/v1/integrations` | JWT | List all integrations for the org |
| GET | `/api/v1/integrations/{model}` | JWT | Get integration by model name |
| DELETE | `/api/v1/integrations/{model}` | JWT | Delete integration |
| POST | `/api/v1/integrations/bot/get-api-key` | API Key | Retrieve integration key (voice server use) |

---

## Members

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/api/v1/members/add-member` | None | Invite / add member to org |
| GET | `/api/v1/members/{org_id}` | JWT | List org members |
| POST | `/api/v1/members/delete-member` | JWT | Remove member from org |

---

## Knowledge Base

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/api/v1/knowledge` | JWT | List knowledge documents for org |
| POST | `/api/v1/knowledge/upload` | JWT | Upload a PDF document (multipart form) |
| DELETE | `/api/v1/knowledge/{document_id}` | JWT | Delete a knowledge document |

---

## RAG / Retrieval

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/api/v1/rag/retrieve` | API Key | Retrieve relevant chunks for a query (voice server use) |

---

## Telemetry

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/api/v1/telemetry` | JWT | Live GPU/VRAM metrics from AI4Bharat STT and TTS servers |

---

## Vobiz Telephony

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/api/v1/vobiz/application` | JWT | Create Vobiz application |
| DELETE | `/api/v1/vobiz/application/{application_id}` | JWT | Delete Vobiz application |
| GET | `/api/v1/vobiz/numbers` | JWT | List available Vobiz numbers |
| POST | `/api/v1/vobiz/numbers/link` | JWT | Link number to application |
| DELETE | `/api/v1/vobiz/numbers/unlink` | JWT | Unlink number from application |

---

## Voice Server Endpoints

These endpoints are served by `voice_2_voice_server` on port **7860**.

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check |
| POST | `/outbound/call/` | Trigger outbound call via Vobiz |
| POST | `/answer` | Vobiz inbound call webhook — returns XML to start streaming |
| WS | `/agent/{agent_id}` | WebSocket audio stream for Vobiz calls |
| POST | `/ubona` | Ubona inbound call webhook |
| WS | `/ubona/stream/{agent_id}` | WebSocket audio stream for Ubona calls |

---

## Backend Health

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/health` | None | MongoDB connectivity check |
| GET | `/` | None | API root / version info |
| GET | `/docs` | None | Swagger UI |
| GET | `/redoc` | None | ReDoc UI |
