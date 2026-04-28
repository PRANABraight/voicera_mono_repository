# GPU Telemetry

Live GPU and VRAM usage monitoring for the self-hosted AI4Bharat STT and TTS inference servers.

## Overview

When the AI4Bharat STT (port 8001) and/or TTS (port 8002) servers are deployed, they run PyTorch models on GPU. The telemetry feature exposes real-time GPU metrics — VRAM allocation, compute utilization, and temperature — through the dashboard.

```
Dashboard  ──GET /api/telemetry──▶  Backend
                                        │  parallel
                          ┌─────────────┴────────────┐
                          ▼                          ▼
              GET /metrics (STT :8001)    GET /metrics (TTS :8002)
```

Metrics are refreshed automatically every **5 seconds** on the dashboard page.

---

## Dashboard

Navigate to **Telemetry** in the sidebar. You will see one card per service showing:

| Metric | Description |
|--------|-------------|
| **VRAM Used / Total** | PyTorch-allocated VRAM vs. total GPU memory (MB) |
| **VRAM Used %** | Proportion of total VRAM currently allocated |
| **GPU Utilization %** | GPU compute utilisation (requires `nvidia-ml-py` installed on the server) |
| **Temperature** | GPU die temperature in °C (requires `nvidia-ml-py`) |
| **Model loaded** | Whether the inference model has finished loading |
| **Device** | GPU name (e.g., `NVIDIA Tesla T4`) or `cpu` |

If a server is unreachable (not deployed or offline), its card shows an **Offline** badge instead of metrics.

A **Combined VRAM** summary card below shows total allocation across both servers.

---

## API

### Get telemetry

```
GET /api/v1/telemetry
```

**Auth:** JWT Bearer token (dashboard users).

Queries both AI4Bharat servers in parallel and returns their metrics. Unreachable servers are represented with `reachable: false` and all metric fields set to `null`.

**Response:**

```json
{
  "stt": {
    "service": "stt",
    "reachable": true,
    "url": "http://ai4bharat_stt_server:8001",
    "device": "cuda:0",
    "device_name": "NVIDIA Tesla T4",
    "model_loaded": true,
    "gpu_available": true,
    "vram_total_mb": 16160,
    "vram_used_mb": 2048,
    "vram_reserved_mb": 2560,
    "vram_used_percent": 12.7,
    "gpu_utilization_percent": 34,
    "temperature_celsius": 61,
    "timestamp": "2024-05-01T10:00:00+00:00"
  },
  "tts": {
    "service": "tts",
    "reachable": true,
    "url": "http://ai4bharat_tts_server:8002",
    "device": "cuda:0",
    "device_name": "NVIDIA Tesla T4",
    "model_loaded": true,
    "gpu_available": true,
    "vram_total_mb": 16160,
    "vram_used_mb": 4096,
    "vram_reserved_mb": 4608,
    "vram_used_percent": 25.3,
    "gpu_utilization_percent": 12,
    "temperature_celsius": 58,
    "timestamp": "2024-05-01T10:00:00+00:00"
  }
}
```

!!! note
    `gpu_utilization_percent` and `temperature_celsius` are `null` when `nvidia-ml-py` is not installed on the inference server. VRAM metrics from `torch.cuda` are always available when a GPU is present.

---

## Service Metrics Endpoint

Each AI4Bharat server exposes its own metrics endpoint directly:

| Server | Endpoint |
|--------|----------|
| STT (IndicConformer) | `GET http://localhost:8001/metrics` |
| TTS (IndicParler) | `GET http://localhost:8002/metrics` |

These endpoints are **unauthenticated** — restrict network access to them in production (they should not be publicly reachable).

**Example STT response:**

```json
{
  "service": "stt",
  "device": "cuda:0",
  "device_name": "NVIDIA Tesla T4",
  "model_loaded": true,
  "gpu_available": true,
  "vram_total_mb": 16160,
  "vram_used_mb": 2048,
  "vram_reserved_mb": 2560,
  "vram_used_percent": 12.7,
  "gpu_utilization_percent": 34,
  "temperature_celsius": 61,
  "timestamp": "2024-05-01T10:00:00+00:00"
}
```

---

## Environment Variables

Add these to `voicera_backend/.env` to point the backend at your AI4Bharat servers:

| Variable | Default | Description |
|----------|---------|-------------|
| `INDIC_STT_SERVER_URL` | `http://localhost:8001` | Base URL of the AI4Bharat STT server |
| `INDIC_TTS_SERVER_URL` | `http://localhost:8002` | Base URL of the AI4Bharat TTS server |

In Docker Compose, use the service names instead of `localhost`:

```env
INDIC_STT_SERVER_URL=http://ai4bharat_stt_server:8001
INDIC_TTS_SERVER_URL=http://ai4bharat_tts_server:8002
```

---

## Optional: richer GPU metrics with `nvidia-ml-py`

By default, VRAM stats are collected using `torch.cuda` (always available). To also expose GPU **compute utilization** and **temperature**, install `nvidia-ml-py` on the inference server:

```bash
pip install nvidia-ml-py
```

No code changes are needed — the servers will detect and use it automatically.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| Server card shows **Offline** | AI4Bharat server not running, or wrong URL | Check `INDIC_STT_SERVER_URL` / `INDIC_TTS_SERVER_URL` and that the container is up |
| VRAM shows 0 MB | Model not yet loaded | Wait for the server to finish loading; check `/health` |
| `gpu_utilization_percent` is `null` | `nvidia-ml-py` not installed | `pip install nvidia-ml-py` on the inference server |
| Metrics stale | Network timeout | Backend uses a 5-second timeout per server; check connectivity |
