# WebSocket API

The Voice Server exposes two WebSocket endpoints for real-time audio streaming from telephony providers. These endpoints are called by **Vobiz** and **Ubona** infrastructure — not by browsers directly.

> **Note:** The VoicEra dashboard does not connect to these WebSocket endpoints. All dashboard interactions go through the [Backend REST API](rest-api.md). The WebSocket endpoints documented here are the server-side interfaces that the telephony providers use to stream audio.

---

## Endpoints

| Endpoint | Provider | Audio Format |
|----------|----------|-------------|
| `WS /agent/{agent_id}` | Vobiz | μ-law 8kHz or L16 PCM 16kHz |
| `WS /ubona/stream/{agent_id}` | Ubona | μ-law 8kHz (PCMU) |

Both endpoints implement the **Pipecat Pipelined Processing** model: incoming audio is transcribed (STT) → fed to the LLM → synthesized (TTS) → streamed back as audio frames to the telephony provider.

---

## Vobiz Media Stream Protocol

### Connection flow

```
Vobiz infrastructure
    │
    │  1. POST /answer?agent_id=<id>  (Vobiz answer webhook)
    │◀──  Voice Server returns XML: <Stream bidirectional="true"> wss://.../agent/<id> </Stream>
    │
    │  2. WebSocket connect → wss://.../agent/<id>
    │──▶ Voice Server accepts
    │
    │  3. Client sends JSON: { "event": "start", "start": { "callSid": "...", "streamSid": "..." } }
    │──▶ Voice Server begins Pipecat pipeline
    │
    │  4. Client sends media frames (audio from caller)
    │──▶ STT → LLM → TTS
    │◀── Voice Server sends media frames (audio response)
    │
    │  5. Call ends: WebSocket closes
```

### XML response (answer webhook)

The `/answer` webhook returns XML instructing Vobiz to open a bidirectional WebSocket:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Stream bidirectional="true" keepCallAlive="true" contentType="audio/x-mulaw;rate=8000">
        wss://your-server/agent/{agent_id}
    </Stream>
</Response>
```

For 16kHz mode (`SAMPLE_RATE=16000`):

```xml
<Stream bidirectional="true" keepCallAlive="true" contentType="audio/x-l16;rate=16000">
    wss://your-server/agent/{agent_id}
</Stream>
```

### Message format (8kHz μ-law mode)

The Vobiz protocol is Plivo-compatible. All messages are JSON strings.

**Vobiz → Voice Server: call start**

```json
{
  "event": "start",
  "start": {
    "callSid": "CA1234567890",
    "streamSid": "MX1234567890"
  }
}
```

**Vobiz → Voice Server: inbound audio (media frame)**

```json
{
  "event": "media",
  "media": {
    "payload": "<base64-encoded μ-law audio>"
  },
  "streamId": "MX1234567890"
}
```

**Voice Server → Vobiz: outbound audio (8kHz μ-law)**

Handled by the Pipecat `PlivoFrameSerializer` base class.

### Message format (16kHz L16 mode)

**Vobiz → Voice Server: inbound audio**

```json
{
  "event": "media",
  "media": {
    "payload": "<base64-encoded raw PCM 16kHz>"
  }
}
```

**Voice Server → Vobiz: outbound audio**

```json
{
  "event": "playAudio",
  "media": {
    "contentType": "audio/x-l16",
    "sampleRate": 16000,
    "payload": "<base64-encoded raw PCM 16kHz>"
  },
  "streamId": "MX1234567890"
}
```

---

## Ubona Media Stream Protocol

### Connection flow

```
Ubona infrastructure
    │
    │  1. POST /ubona  (Ubona answer webhook)
    │◀──  Voice Server returns XML: <Stream bidirectional="true"> wss://.../ubona/stream/mahavistaar </Stream>
    │
    │  2. WebSocket connect → wss://.../ubona/stream/mahavistaar
    │──▶ Voice Server accepts
    │
    │  3. Client MAY send: { "event": "connected" }  (optional)
    │
    │  4. Client sends:    { "event": "start", "callId": "...", "streamId": "..." }
    │──▶ Voice Server begins Pipecat pipeline
    │
    │  5. Bidirectional audio exchange
    │
    │  6. WebSocket closes
```

!!! note
    The Ubona endpoint is hardcoded to the `Mahavistaar` agent. The path ID `mahavistaar` is fixed. To use Ubona with a different agent, the server code must be updated.

### XML response (Ubona answer webhook)

```xml
<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Stream bidirectional="true" keepCallAlive="true" contentType="audio/x-mulaw;rate=8000">
        wss://your-server/ubona/stream/mahavistaar
    </Stream>
</Response>
```

### Message format

All messages are JSON strings. Audio is base64-encoded μ-law (PCMU) at 8kHz.

**Ubona → Voice Server: connected (optional)**

```json
{ "event": "connected" }
```

**Ubona → Voice Server: call start**

```json
{
  "event": "start",
  "callId": "call-uuid",
  "streamId": "stream-uuid",
  "start": {
    "callId": "call-uuid",
    "streamId": "stream-uuid"
  }
}
```

**Ubona → Voice Server: inbound audio**

```json
{
  "event": "media",
  "media": {
    "payload": "<base64-encoded μ-law 8kHz audio>"
  }
}
```

**Ubona → Voice Server: DTMF tone**

```json
{
  "event": "dtmf",
  "dtmf": {
    "digit": "5"
  }
}
```

**Ubona → Voice Server: ping (keepalive)**

```json
{ "event": "ping", "ts": 1714545600000 }
```

**Voice Server → Ubona: pong**

```json
{ "event": "pong", "ts": 1714545600000 }
```

**Voice Server → Ubona: outbound audio**

```json
{
  "event": "media",
  "seqNum": 42,
  "streamId": "stream-uuid",
  "media": {
    "ts": 1714545600000,
    "payload": "<base64-encoded μ-law 8kHz audio>"
  }
}
```

**Voice Server → Ubona: interruption (barge-in)**

```json
{
  "event": "clear",
  "seqNum": 43,
  "streamId": "stream-uuid"
}
```

---

## Audio Formats

| Mode | Encoding | Sample Rate | Channels | Used by |
|------|----------|-------------|----------|---------|
| Default (Vobiz) | μ-law (PCMU) | 8000 Hz | 1 (mono) | Vobiz telephony (8kHz) |
| High quality (Vobiz) | L16 raw PCM | 16000 Hz | 1 (mono) | Vobiz telephony (16kHz) |
| Ubona | μ-law (PCMU) | 8000 Hz | 1 (mono) | Ubona telephony |

The audio sample rate is configured by the `SAMPLE_RATE` environment variable on the Voice Server.

---

## TCP_NODELAY

All WebSocket connections on the Voice Server have **TCP_NODELAY** enabled (Nagle's algorithm disabled). This reduces per-packet latency for small audio frames and is critical for real-time voice quality.

---

## Pipeline per call

When a WebSocket connection is accepted and the `start` event is received, the Voice Server:

1. Fetches the agent configuration from the backend (`GET /api/v1/agents/config/{agent_id}`)
2. Builds a Pipecat pipeline: **STT → LLM → TTS → audio output**
3. Streams audio frames from the telephony provider through the pipeline
4. Sends synthesized audio responses back to the telephony provider
5. On disconnect, saves the call recording and transcript to MinIO and notifies the backend

---

## Next Steps

- **[Voice Server](../services/voice-server.md)** — Voice Server configuration and agent setup
- **[REST API](rest-api.md)** — Backend HTTP API reference
- **[Analytics & Call Logs](../services/analytics.md)** — How call data is stored after sessions end
