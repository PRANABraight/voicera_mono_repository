# STT Providers

VoicEra treats Speech-to-Text (STT) as a **swappable provider slot**. Each agent specifies its STT provider by name in its JSON configuration file. No infrastructure changes are required to switch providers — simply update the agent config.

## Provider Overview

| Provider | Name in Config | Best For | Requires |
|----------|----------------|----------|----------|
| Deepgram | `deepgram` | English, low latency, high accuracy | `DEEPGRAM_API_KEY` |
| Google | `google` | Broad language support, telephony models | Service account credentials |
| OpenAI Whisper | `openai` | General purpose, multilingual | `OPENAI_API_KEY` |
| ElevenLabs | `elevenlabs` | Real-time streaming, high quality | `ELEVENLABS_API_KEY` |
| Sarvam | `sarvam` | Indic languages, cloud API | `SARVAM_API_KEY` |
| Bhashini | `bhashini` | Indic languages, government API | `BHASHINI_API_KEY`, `BHASHINI_SOCKET_URL` |
| AI4Bharat Indic | `indic-conformer-stt` | Indic languages, self-hosted | `INDIC_STT_SERVER_URL` |

---

## Configuring STT per Agent

STT is set in the agent's JSON configuration file under `stt_model`:

```json
{
  "stt_model": {
    "name": "deepgram",
    "language": "English",
    "args": {
      "model": "nova-3"
    }
  }
}
```

The `name` field selects the provider. The `language` field is resolved to the provider-specific language code via the mappings in `voice_2_voice_server/config/stt_mappings.py`. Additional provider-specific parameters go under `args`.

---

## Provider Reference

### Deepgram

Deepgram is the recommended default for English deployments. It offers real-time streaming transcription with high accuracy and low latency.

**Environment variable:**

```bash
DEEPGRAM_API_KEY=your-deepgram-api-key
```

**Agent config example:**

```json
{
  "stt_model": {
    "name": "deepgram",
    "language": "English",
    "args": {
      "model": "nova-3"
    }
  }
}
```

**Available models:** `nova-3`, `nova-2`, `flux-general-en`

---

### Google

Google STT supports a wide range of languages and includes telephony-optimized models designed for 8 kHz audio.

**Environment variables:**

```bash
GOOGLE_STT_CREDENTIALS_PATH=credentials/google_stt.json
```

Place your Google Cloud service account JSON file at the path above (relative to `voice_2_voice_server/`).

**Agent config example:**

```json
{
  "stt_model": {
    "name": "google",
    "language": "English",
    "args": {
      "model": "chirp_3"
    }
  }
}
```

**Available models:** `chirp_3`, `chirp_2`, `telephony`

---

### OpenAI Whisper

OpenAI's Whisper model provides general-purpose multilingual transcription.

**Environment variable:**

```bash
OPENAI_API_KEY=sk-your-openai-api-key
```

**Agent config example:**

```json
{
  "stt_model": {
    "name": "openai",
    "language": "English",
    "args": {
      "model": "whisper-1"
    }
  }
}
```

---

### ElevenLabs

ElevenLabs provides real-time streaming speech recognition with high accuracy.

**Environment variable:**

```bash
ELEVENLABS_API_KEY=your-elevenlabs-api-key
```

**Agent config example:**

```json
{
  "stt_model": {
    "name": "elevenlabs",
    "language": "English"
  }
}
```

---

### Sarvam

Sarvam AI provides cloud-based STT optimised for Indian languages and English.

**Environment variable:**

```bash
SARVAM_API_KEY=your-sarvam-api-key
```

**Agent config example:**

```json
{
  "stt_model": {
    "name": "sarvam",
    "language": "Hindi"
  }
}
```

**Supported languages:** Hindi, Bengali, Gujarati, Kannada, Malayalam, Marathi, Odia, Punjabi, Tamil, Telugu, English.

---

### Bhashini

Bhashini is a Government of India initiative providing STT APIs optimized for Indian languages. It is accessed via a WebSocket streaming interface.

**Environment variables:**

```bash
BHASHINI_API_KEY=your-bhashini-api-key
BHASHINI_SOCKET_URL=wss://dhruva-api.bhashini.gov.in
```

**Agent config example:**

```json
{
  "stt_model": {
    "name": "bhashini",
    "language": "Hindi"
  }
}
```

**Supported languages:** Hindi, Tamil, Telugu, Kannada, Malayalam, Bengali, Punjabi, Marathi, Gujarati, and more.

---

### AI4Bharat Indic (Self-Hosted)

The AI4Bharat Indic STT provider runs the IndicConformer model on your own infrastructure. This is suitable for deployments that require data sovereignty or offline operation.

The `ai4bharat_stt_server/` folder in this repository contains a minimal FastAPI server that wraps the IndicConformer model and exposes a REST API compatible with this provider.

**Environment variable:**

```bash
INDIC_STT_SERVER_URL=http://localhost:8001
```

**Agent config example:**

```json
{
  "stt_model": {
    "name": "indic-conformer-stt",
    "language": "Hindi"
  }
}
```

**Running the self-hosted server:**

```bash
cd ai4bharat_stt_server

python -m venv venv
source venv/bin/activate   # On Windows: venv\Scripts\activate
pip install -r requirements.txt

# Copy and configure environment
cp .env.example .env

# Start the server (default port 8001)
python server.py --port 8001
```

**With Docker (GPU recommended for production):**

```bash
# CPU
docker build -t indic-stt .
docker run -p 8001:8001 --env-file .env indic-stt

# GPU
docker run --gpus all -p 8001:8001 --env-file .env indic-stt
```

**Environment variables for the self-hosted server:**

```bash
HF_TOKEN=your-huggingface-token   # Required if model is gated on HuggingFace
PORT=8001
```

**Supported languages:** Hindi, Tamil, Telugu, Kannada, Malayalam, Bengali, Punjabi, Marathi, Gujarati, and more.

**Performance notes:**

| Language | Accuracy | GPU Recommended |
|----------|----------|-----------------|
| Hindi | 95%+ | Yes |
| Tamil | 92%+ | Yes |
| Telugu | 93%+ | Yes |
| Kannada | 91%+ | Yes |

---

## AI4Bharat STT Server API

The `ai4bharat_stt_server/` directory exposes a minimal REST API. Two server variants are included:

### `server.py` — Base64 JSON API (port 8001)

Used by the voice server's `IndicConformerRESTSTTService`.

**`POST /transcribe`**

```json
{
  "audio_b64": "<base64-encoded raw int16 PCM at 16 kHz>",
  "language_id": "hi"
}
```

Response:

```json
{ "text": "Transcribed text here" }
```

**`GET /health`**

```json
{ "status": "ok", "device": "cuda" }
```

**Model:** `ai4bharat/indic-conformer-600m-multilingual` (Hugging Face, decode path: RNNT)

### `model.py` — Multipart Upload API (port 8000)

Alternative variant that accepts audio files directly.

**`POST /transcribe`** (`multipart/form-data`)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `audio` | file | required | WAV / FLAC / MP3 |
| `language` | string | `hi` | Language code |
| `decoder` | string | `ctc` | `ctc` or `rnnt` |

Response:

```json
{ "text": "...", "language": "hi" }
```

Auto-resamples to 16 kHz.

---

## Adding a New STT Provider

1. Create a service class in `voice_2_voice_server/services/` implementing the Pipecat `STTService` interface.
2. Register it in `voice_2_voice_server/api/services.py` under the appropriate provider name.
3. Add language code mappings in `voice_2_voice_server/config/stt_mappings.py` if needed.
4. Document the required environment variables.

---

## Related

- **[TTS Providers](ai4bharat-tts.md)** — Text-to-Speech provider reference
- **[Voice Server](voice-server.md)** — Voice Server architecture and configuration
- **[Configuration Guide](../getting-started/configuration.md)** — Full environment configuration reference
