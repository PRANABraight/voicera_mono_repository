# Voice Server

A FastAPI-based telephony server for real-time voice-to-voice interactions using the Vobiz telephony platform and Pipecat audio pipeline.

## Project Structure

```
voice_2_voice_server/
├── api/                           # API layer
│   ├── __init__.py
│   ├── server.py                  # FastAPI endpoints and Vobiz/Ubona webhooks
│   ├── bot.py                     # Pipecat voice bot pipeline
│   ├── services.py                # Provider factory functions (LLM, STT, TTS)
│   ├── backend_utils.py           # Backend API helpers (agent config, meetings, RAG)
│   └── call_recording_utils.py    # Call recording save/upload helpers
├── config/                        # Configuration and language mappings
│   ├── __init__.py
│   ├── llm_mappings.py            # LLM provider mappings
│   ├── stt_mappings.py            # STT language code mappings
│   ├── tts_mappings.py            # TTS language code mappings
│   ├── config.yaml                # Server config (gitignored)
│   └── config.example.yaml        # Example configuration
├── services/                      # Custom provider implementations
│   ├── ai4bharat/                 # AI4Bharat Indic STT/TTS client
│   │   ├── stt.py                 # IndicConformer REST STT service
│   │   └── tts.py                 # IndicParler REST TTS service
│   ├── audio/                     # Audio processing utilities
│   │   └── greeting_interruption_filter.py
│   ├── bhashini/                  # Bhashini Indic STT/TTS client
│   │   ├── stt.py
│   │   └── tts.py
│   ├── kenpath_llm/               # Kenpath custom LLM client
│   │   └── llm.py
│   └── openai_kb_llm.py           # OpenAI LLM with Knowledge Base (RAG) support
├── serializer/                    # Audio frame serializers
│   ├── vobiz_serializer.py        # Vobiz protocol serializer (Plivo-compatible)
│   └── ubona_serializer.py        # Ubona Media Stream serializer
├── storage/                       # Object storage integration
│   └── minio_client.py            # MinIO client for recordings and transcripts
├── agent_configs/                 # Agent JSON configuration files
│   ├── default_agent.json
│   ├── sales_agent.json
│   └── indic_english.json
├── main.py                        # Application entry point
├── requirements.txt
├── Dockerfile
└── .env.example
```

## Quick Start

1. **Install dependencies:**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. **Configure environment variables:**
   ```bash
   cp .env.example .env
   # Edit .env with your credentials and API keys
   ```

3. **Run the server:**
   ```bash
   python main.py
   ```

   Or with uvicorn directly:
   ```bash
   uvicorn api.server:app --host 0.0.0.0 --port 7860
   ```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Service status |
| `/health` | GET | Health check |
| `/docs` | GET | Swagger API documentation |
| `/redoc` | GET | ReDoc API documentation |
| `/outbound/call/` | POST | Initiate an outbound call via Vobiz |
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

## Agent Configuration

Agents are defined as JSON files in `agent_configs/`. Each agent independently specifies its LLM, STT, and TTS providers.

```json
{
  "system_prompt": "You are a helpful voice assistant.",
  "greeting_message": "Hello, how can I help you?",
  "session_timeout_minutes": 10,

  "llm_model": {
    "name": "OpenAI",
    "model": "gpt-4o",
    "temperature": 0.7
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

## Supported Providers

### LLM

| Provider | `name` | Notes |
|----------|--------|-------|
| OpenAI | `OpenAI` | GPT-4o, GPT-4o-mini, GPT-3.5-turbo |
| Anthropic | `anthropic` | Claude models (claude-3-5-sonnet etc.) |
| Grok (xAI) | `Grok` | xAI Grok models (grok-2-1212 etc.) |
| Kenpath | `Kenpath` | Custom LLM for Vistaar/government platforms |

### STT (Speech-to-Text)

| Provider | `name` | Notes |
|----------|--------|-------|
| Deepgram | `deepgram` | nova-3, nova-2, flux-general-en |
| Google | `google` | chirp_3, chirp_2, telephony |
| OpenAI Whisper | `openai` | whisper-1 |
| ElevenLabs | `elevenlabs` | Real-time streaming |
| Sarvam | `sarvam` | Indic languages via Sarvam AI |
| Bhashini | `bhashini` | Indic languages via Bhashini API |
| AI4Bharat Indic | `indic-conformer-stt` | Indic languages, self-hosted |

### TTS (Text-to-Speech)

| Provider | `name` | Notes |
|----------|--------|-------|
| Cartesia | `cartesia` | sonic-3, sonic-2, sonic-multilingual |
| Deepgram Aura | `deepgram` | Aura voice family (e.g., `aura-2-helena-en`) |
| Google | `google` | WaveNet, Neural2 voices |
| OpenAI | `openai` | alloy, echo, fable, onyx, nova, shimmer |
| ElevenLabs | `elevenlabs` | High-quality streaming synthesis |
| Sarvam | `sarvam` | Indic languages via Sarvam AI |
| Bhashini | `bhashini` | Indic languages via Bhashini API |
| AI4Bharat Indic | `indic-parler-tts` | Indic languages, self-hosted |

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      Vobiz Telephony                        │
└─────────────────────┬───────────────────────────────────────┘
                      │ WebSocket (audio stream)
                      ▼
┌─────────────────────────────────────────────────────────────┐
│                    FastAPI Server                           │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐ │
│  │   /answer   │  │ /outbound/  │  │  /agent/{type}      │ │
│  │  (webhook)  │  │   call/     │  │  (WebSocket)        │ │
│  └─────────────┘  └─────────────┘  └──────────┬──────────┘ │
└───────────────────────────────────────────────┼────────────┘
                                                │
                          ┌─────────────────────▼──────────────────────┐
                          │              Pipecat Pipeline               │
                          │  ┌─────┐  ┌─────┐  ┌─────┐  ┌─────┐       │
                          │  │ STT │→ │ LLM │→ │ TTS │→ │ Out │       │
                          │  └─────┘  └─────┘  └─────┘  └─────┘       │
                          └────────────────────────────────────────────┘
```

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
| `MINIO_ENDPOINT` | Yes | — | MinIO server endpoint |
| `MINIO_ACCESS_KEY` | Yes | — | MinIO access key |
| `MINIO_SECRET_KEY` | Yes | — | MinIO secret key |
| `MINIO_SECURE` | No | `false` | Use HTTPS for MinIO |
| `VOICERA_BACKEND_URL` | No | `http://localhost:8000` | Backend API URL |
| `INTERNAL_API_KEY` | No | — | Internal API key for backend communication |
| `OPENAI_API_KEY` | * | — | OpenAI API key |
| `ANTHROPIC_API_KEY` | * | — | Anthropic API key |
| `XAI_API_KEY` | * | — | xAI (Grok) API key |
| `DEEPGRAM_API_KEY` | * | — | Deepgram API key |
| `CARTESIA_API_KEY` | * | — | Cartesia API key |
| `ELEVENLABS_API_KEY` | * | — | ElevenLabs API key |
| `SARVAM_API_KEY` | * | — | Sarvam AI API key |
| `GOOGLE_STT_CREDENTIALS_PATH` | * | `credentials/google_stt.json` | Google STT service account file |
| `GOOGLE_TTS_CREDENTIALS_PATH` | * | `credentials/google_tts.json` | Google TTS service account file |
| `BHASHINI_API_KEY` | * | — | Bhashini API key |
| `BHASHINI_SOCKET_URL` | * | — | Bhashini WebSocket URL |
| `INDIC_STT_SERVER_URL` | * | — | Self-hosted Indic STT server URL |
| `INDIC_TTS_SERVER_URL` | * | — | Self-hosted Indic TTS server URL |

\* Required only for the providers configured in your deployed agent JSON files.

## Development

### Adding a New Provider

1. Create a service implementation in `services/` implementing the Pipecat `STTService` or `TTSService` interface.
2. Register a factory function in `api/services.py`.
3. Add language code mappings in `config/stt_mappings.py` or `config/tts_mappings.py` as needed.
