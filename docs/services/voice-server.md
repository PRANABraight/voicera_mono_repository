# Voice Server Service

Comprehensive documentation for the VoicEra Voice Server service.

## Overview

The Voice Server is the real-time voice processing engine for VoicEra, built with **Pipecat** and **Python 3.10+**.

**Key responsibilities:**

- Establish and maintain WebSocket connections with the Vobiz telephony platform
- Process real-time audio streams from callers
- Orchestrate the STT → LLM → TTS pipeline for each active call
- Manage concurrent voice sessions
- Store call recordings and transcripts in MinIO
- Communicate call metadata to the Backend API

## Getting Started

### Prerequisites

- Python 3.10+
- pip
- Vobiz telephony credentials (for production)
- API keys for the AI providers used by your agents

### Installation

```bash
cd voice_2_voice_server

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### Configuration

```bash
# Copy example config
cp .env.example .env

# Edit with your settings
nano .env
```

### Running Locally

```bash
# Development mode
python main.py

# Via Docker
docker build -t voicera-voice-server .
docker run -p 7860:7860 --env-file .env voicera-voice-server
```

---

## Project Structure

```
voice_2_voice_server/
├── api/
│   ├── __init__.py
│   ├── server.py              # FastAPI server, Vobiz/Ubona webhooks, WebSocket endpoints
│   ├── bot.py                 # Pipecat voice bot pipeline
│   ├── services.py            # Provider factory functions (LLM, STT, TTS)
│   ├── backend_utils.py       # Backend API helpers (agent config, meetings, RAG)
│   └── call_recording_utils.py # Call recording save/upload helpers
├── config/
│   ├── __init__.py
│   ├── llm_mappings.py        # LLM provider configuration
│   ├── stt_mappings.py        # STT language code mappings
│   ├── tts_mappings.py        # TTS language code mappings
│   ├── config.yaml            # Server config (gitignored)
│   └── config.example.yaml    # Example configuration
├── services/
│   ├── ai4bharat/             # AI4Bharat Indic STT/TTS client
│   │   ├── __init__.py
│   │   ├── stt.py             # IndicConformer REST STT service
│   │   └── tts.py             # IndicParler REST TTS service
│   ├── audio/
│   │   ├── __init__.py
│   │   └── greeting_interruption_filter.py
│   ├── bhashini/              # Bhashini Indic STT/TTS client
│   │   ├── __init__.py
│   │   ├── stt.py
│   │   └── tts.py
│   ├── kenpath_llm/           # Kenpath custom LLM client
│   │   ├── __init__.py
│   │   └── llm.py
│   ├── openai_kb_llm.py       # OpenAI LLM with Knowledge Base (RAG) support
│   └── __init__.py
├── serializer/
│   ├── __init__.py
│   ├── vobiz_serializer.py    # Vobiz audio protocol serializer (Plivo-compatible)
│   └── ubona_serializer.py    # Ubona Media Stream serializer
├── storage/
│   ├── __init__.py
│   └── minio_client.py        # MinIO integration for recordings
├── agent_configs/             # Agent JSON configuration files
│   ├── default_agent.json
│   ├── sales_agent.json
│   └── indic_english.json
├── main.py                    # Application entry point
├── requirements.txt
├── Dockerfile
└── .env.example
```

---

## Core Components

### Voice Bot Pipeline

The Pipecat pipeline orchestrates STT, LLM, and TTS for each active call session.

```
Audio Input (from Vobiz)
       │
       ▼
  STT Provider  ──► Transcript text
       │
       ▼
  LLM Provider  ──► Response text
       │
       ▼
  TTS Provider  ──► Synthesized audio
       │
       ▼
Audio Output (to Vobiz)
```

The pipeline is assembled per session based on the agent's JSON configuration. Providers are instantiated by factory functions in `api/services.py`.

### Agent Configuration

Each agent is defined by a JSON file in `agent_configs/`. The STT, LLM, and TTS providers are all specified within the agent config, making each agent independently configurable without changing environment variables or code.

```json
{
  "system_prompt": "You are a helpful sales assistant.",
  "greeting_message": "Hello, how can I help you today?",
  "session_timeout_minutes": 10,

  "llm_model": {
    "name": "openai",
    "args": {
      "model": "gpt-4o",
      "temperature": 0.7
    }
  },

  "stt_model": {
    "name": "deepgram",
    "language": "English",
    "args": {
      "model": "nova-3"
    }
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
```

See [STT Providers](ai4bharat-stt.md) and [TTS Providers](ai4bharat-tts.md) for the full list of available provider names and their configuration options.

---

## Service Providers

### LLM Providers

| Provider | `name` in Config | Notes |
|----------|------------------|-------|
| OpenAI | `openai` | GPT-4o, GPT-4o-mini, GPT-3.5-turbo; requires `OPENAI_API_KEY` |
| Anthropic | `anthropic` | Claude models (claude-3-5-sonnet etc.); requires `ANTHROPIC_API_KEY` |
| Grok (xAI) | `Grok` | xAI Grok models; requires `XAI_API_KEY` or `GROK_API_KEY` |
| Kenpath | `kenpath` | Custom LLM endpoint for Vistaar/government platforms |

### STT Providers

| Provider | `name` in Config | Notes |
|----------|------------------|-------|
| Deepgram | `deepgram` | Requires `DEEPGRAM_API_KEY` |
| Google | `google` | Requires `GOOGLE_STT_CREDENTIALS_PATH` |
| OpenAI Whisper | `openai` | Requires `OPENAI_API_KEY` |
| Bhashini | `bhashini` | Requires `BHASHINI_API_KEY`, `BHASHINI_SOCKET_URL` |
| AI4Bharat Indic | `indic-conformer-stt` | Requires `INDIC_STT_SERVER_URL` (self-hosted) |

Full details: **[STT Providers](ai4bharat-stt.md)**

### TTS Providers

| Provider | `name` in Config | Notes |
|----------|------------------|-------|
| Cartesia | `cartesia` | Requires `CARTESIA_API_KEY` |
| Deepgram Aura | `deepgram` | Requires `DEEPGRAM_API_KEY` |
| Google | `google` | Requires `GOOGLE_TTS_CREDENTIALS_PATH` |
| OpenAI | `openai` | Requires `OPENAI_API_KEY` |
| Bhashini | `bhashini` | Requires `BHASHINI_API_KEY`, `BHASHINI_SOCKET_URL` |
| AI4Bharat Indic | `indic-parler-tts` | Requires `INDIC_TTS_SERVER_URL` (self-hosted) |

Full details: **[TTS Providers](ai4bharat-tts.md)**

---

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Service status |
| `/health` | GET | Detailed health check |
| `/docs` | GET | Swagger API documentation |
| `/redoc` | GET | ReDoc API documentation |
| `/outbound/call/` | POST | Initiate an outbound call |
| `/answer` | GET/POST | Vobiz inbound call webhook |
| `/agent/{agent_id}` | WebSocket | Audio streaming endpoint (Vobiz) |
| `/ubona` | GET/POST | Ubona inbound call webhook |
| `/ubona/stream/{agent_id}` | WebSocket | Audio streaming endpoint (Ubona) |

### Initiate an Outbound Call

```bash
curl -X POST "http://localhost:7860/outbound/call/" \
  -H "Content-Type: application/json" \
  -d '{
    "customer_number": "+919876543210",
    "agent_id": "sales_agent",
    "caller_id": "+911234567890"
  }'
```

---

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `VOBIZ_API_BASE` | Yes | — | Vobiz API base URL |
| `VOBIZ_AUTH_ID` | Yes | — | Vobiz account auth ID |
| `VOBIZ_AUTH_TOKEN` | Yes | — | Vobiz account auth token |
| `VOBIZ_CALLER_ID` | No | — | Default caller ID for outbound calls |
| `JOHNAIC_SERVER_URL` | Yes | — | Public server URL for Vobiz webhooks |
| `JOHNAIC_WEBSOCKET_URL` | Yes | — | Public WebSocket URL |
| `SAMPLE_RATE` | No | `8000` | Audio sample rate in Hz |
| `MINIO_ENDPOINT` | Yes | — | MinIO server endpoint (e.g., `localhost:9000`) |
| `MINIO_ACCESS_KEY` | Yes | — | MinIO access key |
| `MINIO_SECRET_KEY` | Yes | — | MinIO secret key |
| `MINIO_SECURE` | No | `false` | Use HTTPS for MinIO |
| `VOICERA_BACKEND_URL` | No | `http://localhost:8000` | Backend API URL |
| `INTERNAL_API_KEY` | No | — | Internal API key for backend communication |
| `OPENAI_API_KEY` | * | — | OpenAI API key |
| `DEEPGRAM_API_KEY` | * | — | Deepgram API key |
| `CARTESIA_API_KEY` | * | — | Cartesia API key |
| `GOOGLE_STT_CREDENTIALS_PATH` | * | `credentials/google_stt.json` | Google STT service account file |
| `GOOGLE_TTS_CREDENTIALS_PATH` | * | `credentials/google_tts.json` | Google TTS service account file |
| `BHASHINI_API_KEY` | * | — | Bhashini API key |
| `BHASHINI_SOCKET_URL` | * | — | Bhashini WebSocket URL |
| `INDIC_STT_SERVER_URL` | * | — | Self-hosted Indic STT server URL |
| `INDIC_TTS_SERVER_URL` | * | — | Self-hosted Indic TTS server URL |

\* Required depending on the providers configured in your agent JSON files.

---

## Monitoring and Health

### Health Check

```bash
curl http://localhost:7860/health
```

The response includes the status of each dependent service and the count of active sessions.

### Key Metrics to Monitor

- **Active session count** — Concurrent voice calls being processed
- **Audio round-trip latency** — Time from audio received to response audio sent back
- **STT / LLM / TTS error rate** — Provider-level failures
- **Service uptime** — Overall availability

---

## Error Handling

The Voice Server implements retry logic with exponential backoff for transient provider failures. If an STT transcription fails, the agent prompts the caller to repeat. If an LLM or TTS failure occurs, a fallback response is synthesized and the session is cleanly terminated if recovery is not possible.

---

## Adding a New Provider

1. Create a service implementation in `voice_2_voice_server/services/` implementing the appropriate Pipecat interface (`STTService` or `TTSService`).
2. Register a factory function in `voice_2_voice_server/api/services.py`.
3. Add language code mappings in `voice_2_voice_server/config/stt_mappings.py` or `tts_mappings.py` as needed.

---

## Next Steps

- **[STT Providers](ai4bharat-stt.md)** — Full STT provider reference
- **[TTS Providers](ai4bharat-tts.md)** — Full TTS provider reference
- **[WebSocket API](../api/websocket-api.md)** — Protocol specifications
- **[Configuration Guide](../getting-started/configuration.md)** — Environment configuration
