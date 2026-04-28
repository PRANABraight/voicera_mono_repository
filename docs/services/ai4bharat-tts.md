# TTS Providers

VoicEra treats Text-to-Speech (TTS) as a **swappable provider slot**. Each agent specifies its TTS provider by name in its JSON configuration file. No infrastructure changes are required to switch providers — simply update the agent config.

## Provider Overview

| Provider | Name in Config | Best For | Requires |
|----------|----------------|----------|----------|
| Cartesia | `cartesia` | English, expressive voices, low latency | `CARTESIA_API_KEY` |
| Deepgram Aura | `deepgram` | English, natural Aura voices | `DEEPGRAM_API_KEY` |
| Google | `google` | Broad language support, WaveNet/Neural2 | Service account credentials |
| OpenAI | `openai` | General purpose, multiple voice personas | `OPENAI_API_KEY` |
| ElevenLabs | `elevenlabs` | High-quality voices, streaming | `ELEVENLABS_API_KEY` |
| Sarvam | `sarvam` | Indic languages, cloud API | `SARVAM_API_KEY` |
| Bhashini | `bhashini` | Indic languages, government API | `BHASHINI_TTS_SERVER_URL`, `BHASHINI_TTS_AUTH_TOKEN` |
| AI4Bharat Indic | `indic-parler-tts` | Indic languages, self-hosted | `INDIC_TTS_SERVER_URL` |

---

## Configuring TTS per Agent

TTS is set in the agent's JSON configuration file under `tts_model`:

```json
{
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

The `name` field selects the provider. The `language` field is resolved to the provider-specific language code via the mappings in `voice_2_voice_server/config/tts_mappings.py`. Additional provider-specific parameters go under `args`.

---

## Provider Reference

### Cartesia

Cartesia is the recommended default for English deployments. It offers low-latency streaming synthesis with expressive, natural-sounding voices.

**Environment variable:**

```bash
CARTESIA_API_KEY=your-cartesia-api-key
```

**Agent config example:**

```json
{
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

**Available models:** `sonic-3`, `sonic-2`, `sonic-multilingual`

Voice IDs are found in the [Cartesia Voice Library](https://play.cartesia.ai/voices).

---

### Deepgram Aura

Deepgram provides the Aura family of voices for low-latency TTS optimized for telephony.

**Environment variable:**

```bash
DEEPGRAM_API_KEY=your-deepgram-api-key
```

**Agent config example:**

```json
{
  "tts_model": {
    "name": "deepgram",
    "language": "English",
    "args": {
      "voice": "aura-2-helena-en"
    }
  }
}
```

Voice names follow the pattern `aura-2-{name}-{lang}`. See the [Deepgram Aura docs](https://developers.deepgram.com/docs/tts-models) for the full list.

---

### Google

Google TTS supports a wide range of languages using WaveNet and Neural2 voices, and includes telephony-optimized options.

**Environment variables:**

```bash
GOOGLE_TTS_CREDENTIALS_PATH=credentials/google_tts.json
```

Place your Google Cloud service account JSON file at the path above (relative to `voice_2_voice_server/`).

**Agent config example:**

```json
{
  "tts_model": {
    "name": "google",
    "language": "English",
    "args": {
      "voice": "en-US-Neural2-F"
    }
  }
}
```

---

### OpenAI

OpenAI provides several voice personas for TTS via the standard API.

**Environment variable:**

```bash
OPENAI_API_KEY=sk-your-openai-api-key
```

**Agent config example:**

```json
{
  "tts_model": {
    "name": "openai",
    "language": "English",
    "args": {
      "voice": "nova"
    }
  }
}
```

**Available voices:** `alloy`, `echo`, `fable`, `onyx`, `nova`, `shimmer`

---

### ElevenLabs

ElevenLabs provides high-quality voice synthesis with streaming support.

**Environment variable:**

```bash
ELEVENLABS_API_KEY=your-elevenlabs-api-key
```

**Agent config example:**

```json
{
  "tts_model": {
    "name": "elevenlabs",
    "language": "English",
    "args": {
      "voice_id": "your-voice-id"
    }
  }
}
```

---

### Sarvam

Sarvam AI provides cloud-based TTS optimised for Indian languages.

**Environment variable:**

```bash
SARVAM_API_KEY=your-sarvam-api-key
```

**Agent config example:**

```json
{
  "tts_model": {
    "name": "sarvam",
    "language": "Hindi"
  }
}
```

**Supported languages:** Hindi, Bengali, Gujarati, Kannada, Malayalam, Marathi, Odia, Punjabi, Tamil, Telugu, English.

---

### Bhashini

Bhashini is a Government of India initiative providing TTS APIs optimized for Indian languages. The TTS path uses a dedicated streaming HTTP server (separate from the STT socket connection).

**Environment variables:**

```bash
BHASHINI_TTS_SERVER_URL=https://your-bhashini-tts-server
BHASHINI_TTS_AUTH_TOKEN=your-bhashini-tts-token
```

**Agent config example:**

```json
{
  "tts_model": {
    "name": "bhashini",
    "language": "Hindi"
  }
}
```

**Supported languages:** Hindi, Tamil, Telugu, Kannada, Malayalam, Bengali, Punjabi, Marathi, Gujarati, and more.

---

### AI4Bharat Indic (Self-Hosted)

The AI4Bharat Indic TTS provider runs the IndicParler model on your own infrastructure. This is suitable for deployments that require data sovereignty or offline operation.

The `ai4bharat_tts_server/` folder in this repository contains a FastAPI server that wraps the IndicParler model and exposes a streaming-compatible REST API.

**Environment variable:**

```bash
INDIC_TTS_SERVER_URL=http://localhost:8002
```

**Agent config example:**

```json
{
  "tts_model": {
    "name": "indic-parler-tts",
    "language": "Hindi"
  }
}
```

**Running the self-hosted server:**

```bash
cd ai4bharat_tts_server

python -m venv venv
source venv/bin/activate   # On Windows: venv\Scripts\activate
pip install -r requirements.txt

# Copy and configure environment
cp .env.example .env

# Start the server (default port 8002)
python server.py
```

**With Docker (GPU recommended for production):**

```bash
# CPU
docker build -t indic-tts .
docker run -p 8002:8002 --env-file .env indic-tts

# GPU
docker run --gpus all -p 8002:8002 --env-file .env indic-tts
```

**Environment variables for the self-hosted server:**

```bash
HF_TOKEN=your-huggingface-token   # Required if model is gated on HuggingFace
PORT=8002
```

**Supported languages:** Hindi, Tamil, Telugu, Kannada, Malayalam, Bengali, Punjabi, Marathi, Gujarati, and more.

**Performance notes:**

| Language | Quality | GPU Recommended |
|----------|---------|-----------------|
| Hindi | High | Yes |
| Tamil | High | Yes |
| Telugu | High | Yes |
| Kannada | High | Yes |

---

## AI4Bharat TTS Server API

The `ai4bharat_tts_server/` directory runs the IndicParler model and streams audio as NDJSON chunks.

### Endpoints

**`POST /tts/stream`**

Request body:

```json
{
  "text": "नमस्ते, मैं आपकी कैसे मदद कर सकता हूँ?",
  "description": "A calm female voice with clear pronunciation",
  "speaker": "Divya",
  "play_steps_in_s": 0.5
}
```

| Field | Type | Range | Default | Notes |
|-------|------|-------|---------|-------|
| `text` | string | 1–5000 chars | required | Text to synthesize |
| `description` | string | — | generic string | Voice style conditioning |
| `speaker` | string | — | `Divya` | Speaker name |
| `play_steps_in_s` | float | 0–2 | `0.5` | Streaming chunk size (seconds) |

**Response:** `application/x-ndjson` — one JSON line per chunk:

```json
{"audio": "<base64 int16 PCM>", "sample_rate": 24000, "samples": 12000}
{"audio": "<base64 int16 PCM>", "sample_rate": 24000, "samples": 12000}
{"done": true}
```

**`GET /health`**

```json
{ "status": "ok", "device": "cuda", "sample_rate": 24000, "model_loaded": true }
```

### Model details

| Property | Value |
|----------|-------|
| Model | `ai4bharat/indic-parler-tts` |
| Base image | `pytorch/pytorch:2.1.2-cuda11.8-cudnn8-runtime` |
| Output format | int16 PCM (base64) |
| Sample rate | 24000 Hz |
| Precision | `bfloat16` on CUDA, `float32` on CPU |
| Port | 8002 |

### Running with GPU

```bash
docker run --gpus all \
  -p 8002:8002 \
  -e HF_TOKEN=your_token \
  ai4bharat-tts
```

---

## Adding a New TTS Provider

1. Create a service class in `voice_2_voice_server/services/` implementing the Pipecat `TTSService` interface.
2. Register it in `voice_2_voice_server/api/services.py` under the appropriate provider name.
3. Add language code mappings in `voice_2_voice_server/config/tts_mappings.py` if needed.
4. Document the required environment variables.

---

## Related

- **[STT Providers](ai4bharat-stt.md)** — Speech-to-Text provider reference
- **[Voice Server](voice-server.md)** — Voice Server architecture and configuration
- **[Configuration Guide](../getting-started/configuration.md)** — Full environment configuration reference
