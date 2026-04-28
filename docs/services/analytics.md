# Analytics & Call Logs

Comprehensive documentation for VoicEra's analytics, call logging, and call recording features.

## Overview

VoicEra captures every call as a **CallLog** record in MongoDB. On top of that, the backend exposes an **analytics API** that aggregates call data per organisation — total calls, connection rates, average duration, and a per-agent breakdown. After each call ends, the voice server uploads the WAV recording and transcript to **MinIO** and POSTs the URLs to the backend, linking them to the original CallLog.

```
Voice Server                   Backend                     Dashboard
    │                              │                            │
    │  Call starts                 │                            │
    │──POST /api/v1/meetings──────▶│  Creates CallLog           │
    │                              │                            │
    │  Call ends                   │                            │
    │──save WAV/transcript──▶ MinIO│                            │
    │──POST /api/v1/call-recordings│  Updates CallLog           │
    │                              │                            │
    │                              │◀──GET /api/v1/analytics────│
    │                              │   (dashboard user)         │
```

---

## CallLog data model

All call data is stored in the **`CallLogs`** MongoDB collection with a unique index on `meeting_id`.

| Field | Type | Description |
|-------|------|-------------|
| `meeting_id` | string | Unique call identifier (from telephony provider) |
| `org_id` | string | Organisation that owns the call |
| `agent_type` | string | Agent name / type used for this call |
| `phone_number` | string | Caller's phone number |
| `call_type` | string | `inbound` or `outbound` |
| `status` | string | Call status from telephony provider |
| `call_busy` | boolean | Whether the call was answered |
| `duration` | number | Call duration in seconds |
| `price` | string | Telephony cost |
| `start_time_utc` | string (ISO 8601) | Call start timestamp |
| `end_time_utc` | string (ISO 8601) | Call end timestamp |
| `created_at` | string (ISO 8601) | Record creation timestamp |
| `recording_url` | string | `minio://recordings/<...>.wav` |
| `transcript_url` | string | `minio://transcripts/<...>.txt` |
| `transcript_content` | string | Full transcript text |
| `call_duration` | number | Duration from recording (may differ from telephony duration) |

---

## Analytics API

### Get analytics

```
GET /api/v1/analytics
```

Returns aggregated call metrics for the authenticated user's organisation.

**Auth:** JWT Bearer token (dashboard users).

**Query parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `agent_type` | string | No | Filter by specific agent |
| `phone_number` | string | No | Filter by caller's number |
| `start_date` | string (ISO 8601) | No | Start of date range |
| `end_date` | string (ISO 8601) | No | End of date range |

!!! note
    When `start_date` or `end_date` are provided, `phone_number` filtering is not applied. Use date-range mode or phone filter mode, not both.

**Response:**

```json
{
  "org_id": "org-123",
  "calls_attempted": 250,
  "calls_connected": 198,
  "average_call_duration": 142.5,
  "total_minutes_connected": 470.25,
  "most_used_agent": "sales-agent-v2",
  "most_used_agent_count": 120,
  "agent_breakdown": [
    { "agent_type": "sales-agent-v2", "call_count": 120 },
    { "agent_type": "support-bot", "call_count": 78 }
  ],
  "calculated_at": "2024-05-01T10:00:00Z",
  "start_date": "2024-04-01T00:00:00Z",
  "end_date": "2024-04-30T23:59:59Z"
}
```

**Metric definitions:**

| Metric | How it is calculated |
|--------|----------------------|
| `calls_attempted` | Total CallLog records matching the filters |
| `calls_connected` | Records where `call_busy` is not `true` **and** either `end_time_utc` is set or `duration > 0` |
| `average_call_duration` | Mean of individual call durations (minutes), connected calls only |
| `total_minutes_connected` | Sum of durations for connected calls (minutes) |
| `most_used_agent` | Agent type with the highest call count |
| `agent_breakdown` | Per-agent count, sorted descending |

---

## Call recordings

The voice server stores recordings and transcripts in **MinIO** and links them to the CallLog in MongoDB.

### Storage layout

| Bucket | Content | Path pattern |
|--------|---------|--------------|
| `recordings` | WAV audio file | `<call_sid>.wav` |
| `transcripts` | Plain text transcript | `<call_sid>.txt` |

MinIO URLs stored in `CallLogs` use the `minio://` scheme (e.g., `minio://recordings/abc123.wav`).

### Save call recording (service-to-service)

```
POST /api/v1/call-recordings
```

Called by the voice server after the call ends. Not intended for direct use from the dashboard.

!!! warning
    This endpoint is currently **unauthenticated**. In production environments, restrict access at the network level or add an API key header.

**Request body:**

```json
{
  "call_sid": "CA1234567890",
  "recording_url": "minio://recordings/CA1234567890.wav",
  "transcript_url": "minio://transcripts/CA1234567890.txt",
  "transcript_content": "Hello, how can I help you today?...",
  "agent_type": "sales-agent-v2",
  "call_duration": 185,
  "end_time_utc": "2024-05-01T10:05:00Z",
  "org_id": "org-123"
}
```

The backend performs an **upsert** on `CallLogs` matched by `meeting_id = call_sid`.

---

## Call recording flow (voice server)

When a call ends, `voice_2_voice_server/api/bot.py` performs these steps in order:

```
1. save_recording_from_chunks(call_sid, audio_chunks)
      └─▶ MinIO bucket: recordings/<call_sid>.wav

2. save_transcript_from_lines(call_sid, transcript_lines)
      └─▶ MinIO bucket: transcripts/<call_sid>.txt

3. submit_call_recording(call_sid, org_id, agent_type, duration, end_time)
      ├─▶ Builds minio:// URLs
      ├─▶ Reads transcript text from MinIO
      └─▶ POST /api/v1/call-recordings  (INTERNAL_API_KEY)
```

---

## Health check

```
GET /health
```

Returns the backend's health status. No authentication required.

**Response (healthy):**

```json
{ "status": "healthy", "database": "connected" }
```

**Response (unhealthy):**

```json
{ "status": "unhealthy", "database": "disconnected" }
```

Internally performs a MongoDB `ping` command. Use this endpoint for load balancer health probes.

---

## Environment variables

| Variable | Description |
|----------|-------------|
| `MONGODB_HOST` | MongoDB host |
| `MONGODB_DATABASE` | Database name (default: `voicera`) |
| `INTERNAL_API_KEY` | Shared secret for voice server → backend calls |
| `MINIO_ENDPOINT` | MinIO server address (set in voice server config) |
| `MINIO_ACCESS_KEY` | MinIO access key |
| `MINIO_SECRET_KEY` | MinIO secret key |

---

## Dashboard usage

### Viewing analytics

Navigate to **Analytics** in the dashboard to view:

- Total and connected call counts
- Average call duration
- Total connected minutes
- Per-agent breakdown chart

Use the date range picker to filter by period, or filter by agent type from the dropdown.

### Accessing call recordings

Navigate to **Call History** (Meetings) to see individual call records. Each completed call shows:

- Duration and status
- Link to play the recording (WAV)
- Transcript text (expandable)

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| Analytics shows 0 calls | No calls completed for this org | Verify calls are reaching the voice server |
| Recordings missing from call history | MinIO unreachable from voice server | Check `MINIO_ENDPOINT` and credentials in voice server config |
| `calls_connected` lower than expected | `call_busy: true` set on all records | Check telephony webhook mapping in `meeting_service` |
| Transcript blank | Transcript file empty or upload failed | Check voice server logs after call teardown |
| `/health` returns `unhealthy` | MongoDB unreachable | Verify MongoDB container is running and credentials are correct |
