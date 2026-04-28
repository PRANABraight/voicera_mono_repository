# Environment Variables Reference

Complete reference for all environment variables used in VoicEra services.

## Table of Contents

1. [Backend (`voicera_backend/.env`)](#backend-environment)
2. [Voice Server (`voice_2_voice_server/.env`)](#voice-server-environment)
3. [Frontend (`voicera_frontend/.env.local`)](#frontend-environment)
4. [AI4Bharat STT Server](#ai4bharat-stt-server)
5. [AI4Bharat TTS Server](#ai4bharat-tts-server)

---

## Backend Environment

### File: `voicera_backend/.env`  (see `voicera_backend/env.example`)

#### MongoDB

```env
MONGODB_HOST=mongodb
MONGODB_PORT=27017
MONGODB_USER=admin
MONGODB_PASSWORD=admin123
MONGODB_DATABASE=voicera
MONGODB_AUTH_SOURCE=admin
```

#### Application

```env
DEBUG=false            # true enables uvicorn auto-reload
SECRET_KEY=            # Required — long random string for JWT signing
```

#### Email (Mailtrap)

```env
MAILTRAP_API_TOKEN=
MAILTRAP_FROM_EMAIL=noreply@voicera.com
MAILTRAP_FROM_NAME=VoicEra
FRONTEND_URL=http://localhost:3000    # Used in password-reset email links
```

#### Internal Service Auth

```env
INTERNAL_API_KEY=      # Shared secret for voice server → backend calls
```

#### MinIO (Call Recordings Storage)

```env
MINIO_ENDPOINT=minio:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
```

#### RAG / Knowledge Base (ChromaDB)

```env
# Optional — defaults to voicera_backend/rag_system/chroma_data
CHROMA_BASE_DIR=/app/rag_system/chroma_data
```

#### Vobiz Telephony

```env
VOBIZ_API_BASE_URL=https://api.vobiz.ai/api/v1
VOBIZ_ACCOUNT_ID=
VOBIZ_AUTH_ID=
VOBIZ_AUTH_TOKEN=
```

#### Defaults (not in env, set in code)

| Setting | Default | Notes |
|---------|---------|-------|
| `API_V1_PREFIX` | `/api/v1` | Hardcoded in `config.py` |
| Port | `8000` | Set via uvicorn CLI |

---

## Voice Server Environment

### File: `voice_2_voice_server/.env`  (see `.env.example`)

#### Backend Connection

```env
VOICERA_BACKEND_URL=http://backend:8000
INTERNAL_API_KEY=      # Must match backend's INTERNAL_API_KEY
```

#### Vobiz Telephony

```env
VOBIZ_AUTH_ID=
VOBIZ_AUTH_TOKEN=
VOBIZ_API_BASE=https://api.vobiz.ai/api/v1
VOBIZ_CALLER_ID=       # Caller ID for outbound calls

# WebSocket URL the voice server advertises to Vobiz for streaming
JOHNAIC_WEBSOCKET_URL=wss://yourdomain.com
JOHNAIC_SERVER_URL=https://yourdomain.com
```

#### Audio

```env
SAMPLE_RATE=16000      # 16000 for Vobiz (L16), 8000 for Ubona (PCMU)
```

#### LLM Providers

```env
# OpenAI
OPENAI_API_KEY=sk-...

# Anthropic
ANTHROPIC_API_KEY=sk-ant-...

# Grok / xAI
XAI_API_KEY=
GROK_API_KEY=          # Alternative name for xAI key

# Kenpath / Vistaar (custom)
KENPATH_JWT_PRIVATE_KEY_PATH=/path/to/private_key.pem
KENPATH_JWT_PHONE=
KENPATH_VISTAAR_API_URL=https://voice-prod.mahapocra.gov.in
```

#### STT Providers

```env
# Deepgram
DEEPGRAM_API_KEY=

# Google (STT)
GOOGLE_STT_CREDENTIALS_PATH=/path/to/service-account.json

# ElevenLabs
ELEVENLABS_API_KEY=

# Sarvam
SARVAM_API_KEY=

# Bhashini
BHASHINI_API_KEY=
BHASHINI_SOCKET_URL=wss://dhruva-api.bhashini.gov.in  # Optional override

# AI4Bharat Indic STT
INDIC_STT_SERVER_URL=http://ai4bharat_stt_server:8001
```

#### TTS Providers

```env
# Cartesia
CARTESIA_API_KEY=

# Google (TTS)
GOOGLE_TTS_CREDENTIALS_PATH=/path/to/service-account.json

# Bhashini TTS
BHASHINI_TTS_SERVER_URL=
BHASHINI_TTS_AUTH_TOKEN=

# AI4Bharat Indic TTS
INDIC_TTS_SERVER_URL=http://ai4bharat_tts_server:8002
```

#### MinIO (for storing recordings)

```env
MINIO_ENDPOINT=minio:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_SECURE=false     # true for HTTPS
```

---

## Frontend Environment

### File: `voicera_frontend/.env.local`

```env
# Backend API base URL
NEXT_PUBLIC_API_URL=http://localhost:8000

# Voice server URL (for outbound call API route)
VOICE_SERVER_URL=http://localhost:7860

# App name shown in the UI
NEXT_PUBLIC_APP_NAME=VoicEra
```

!!! note
    In Docker Compose these are set to internal service names:
    `NEXT_PUBLIC_API_URL=http://backend:8000` and `VOICE_SERVER_URL=http://voice_server:7860`.

---

## AI4Bharat STT Server

### File: `ai4bharat_stt_server/.env`

```env
HF_TOKEN=              # Optional — for gated Hugging Face models
PORT=8001              # Documentation only; port is set in uvicorn CLI
```

---

## AI4Bharat TTS Server

### File: `ai4bharat_tts_server/.env`

```env
HF_TOKEN=              # Optional — consumed in server.py for from_pretrained()
PORT=8002              # Documentation only; port is set in uvicorn CLI
```

---

## Docker Compose defaults

The root `docker-compose.yml` sets these automatically when you run `docker compose up`:

| Variable | Value | Service |
|----------|-------|---------|
| `MONGODB_HOST` | `mongodb` | backend |
| `NEXT_PUBLIC_API_URL` | `http://backend:8000` | frontend |
| `VOICE_SERVER_URL` | `http://voice_server:7860` | frontend |
| `VOICERA_BACKEND_URL` | `http://backend:8000` | voice_server |
| `MINIO_ENDPOINT` | `minio:9000` | voice_server |
| `MINIO_ACCESS_KEY` | `minioadmin` | voice_server |
| `MINIO_SECRET_KEY` | `minioadmin` | voice_server |
| `MINIO_SECURE` | `false` | voice_server |

---

## Minimal production checklist

| Variable | Service | Why it must be set |
|----------|---------|-------------------|
| `SECRET_KEY` | backend | JWT signing — leave blank → tokens rejected |
| `INTERNAL_API_KEY` | backend + voice_server | Service-to-service auth |
| `MONGODB_PASSWORD` | backend | Change default `admin123` |
| `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` | backend + voice_server | Change default `minioadmin` |
| `OPENAI_API_KEY` (or Integration) | voice_server | Required for OpenAI LLM and KB embeddings |
| `JOHNAIC_WEBSOCKET_URL` | voice_server | Publicly reachable URL Vobiz calls back to |
| `MAILTRAP_API_TOKEN` | backend | Required for password-reset emails |
