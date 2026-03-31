"""FastAPI server for Vobiz telephony integration."""

import os
import json
import traceback
import uuid as _uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from loguru import logger
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, Request, HTTPException
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
import uvicorn

from .bot import bot
from .backend_utils import create_meeting_in_backend, update_meeting_end_time, fetch_agent_config_from_backend
load_dotenv()

# Constants
AGENT_CONFIGS_DIR = Path("agent_configs")

# In-memory session store: session_id → agent_config dict
# Used by /call/outbound to pass inline config to /answer and /agent/{id} without a backend
_session_store: Dict[str, dict] = {}

# Provider name normalisation (worker sends lowercase, Voicera services expect Title case)
_PROVIDER_MAP = {
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "grok": "Grok",
    "deepgram": "Deepgram",
    "google": "Google",
    "sarvam": "Sarvam",
    "ai4bharat": "AI4Bharat",
    "bhashini": "Bhashini",
    "elevenlabs": "ElevenLabs",
    "cartesia": "Cartesia",
}


def _normalise_provider(name: str) -> str:
    return _PROVIDER_MAP.get(str(name).strip().lower(), name)


# === Pydantic Models ===

class OutboundCallRequest(BaseModel):
    """Request model for initiating outbound calls."""
    customer_number: str
    agent_id: str
    custom_field: Optional[str] = None
    caller_id: Optional[str] = None


class InlineCallRequest(BaseModel):
    """Inline call request sent by the leadership-coach worker.

    The worker builds the full system prompt and LLM/STT/TTS config and passes
    it here directly, so no voicera_backend is needed.
    """
    phone: str
    systemPrompt: str
    greeting: str = ""
    llm: Dict[str, Any] = {}   # { "provider": "openai", "model": "gpt-4o-mini" }
    stt: Dict[str, Any] = {}   # { "provider": "deepgram", "language": "English", "args": {...} }
    tts: Dict[str, Any] = {}   # { "provider": "openai", "args": { "voice": "nova" } }
    webhookUrl: str = ""
    maxDurationSeconds: int = 600
    metadata: Dict[str, Any] = {}


# === Helper Functions ===

def _get_env_or_raise(key: str) -> str:
    """Get environment variable or raise ValueError."""
    value = os.environ.get(key)
    if not value:
        raise ValueError(f"Missing required environment variable: {key}")
    return value


def make_outbound_call_vobiz(
    customer_number: str,
    agent_id: str,
    caller_id: Optional[str] = None
) -> dict:
    """Make an outbound call using Vobiz API.
    
    Args:
        customer_number: Phone number to call
        agent_id: Agent ID to use
        caller_id: Optional caller ID (defaults to VOBIZ_CALLER_ID env var)
        
    Returns:
        Vobiz API response dictionary
        
    Raises:
        ValueError: If required credentials are missing
        requests.HTTPError: If API call fails
    """
    auth_id = _get_env_or_raise('VOBIZ_AUTH_ID')
    auth_token = _get_env_or_raise('VOBIZ_AUTH_TOKEN')
    server_url = _get_env_or_raise('JOHNAIC_SERVER_URL')
    vobiz_api_base_url = _get_env_or_raise("VOBIZ_API_BASE")

    
    from_number = caller_id or os.environ.get('VOBIZ_CALLER_ID')
    if not from_number:
        raise ValueError("No caller_id provided and VOBIZ_CALLER_ID not set")
    
    
    headers = {
        'X-Auth-ID': auth_id,
        'X-Auth-Token': auth_token,
        'Content-Type': 'application/json'
    }
    payload = {
        'from': from_number,
        'to': customer_number,
        'answer_url': f'{server_url}/answer?agent_id={agent_id}',
        'answer_method': 'POST',
    }  
    
    logger.info(f"📞 Outbound call: {from_number} → {customer_number} (agent: {agent_id})")
    logger.info(f"📞 Vobiz payload: {payload}")
    vobiz_api_base_url_api = f"{vobiz_api_base_url}/Account/{auth_id}/Call/"
    response = requests.post(vobiz_api_base_url_api, json=payload, headers=headers, timeout=30)
    if not response.ok:
        logger.error(f"❌ Vobiz error {response.status_code}: {response.text}")
        response.raise_for_status()
    
    result = response.json()
    logger.info(f"✅ Call initiated: {result.get('call_uuid', 'unknown')}")
    return result


def _build_stream_xml(websocket_url: str) -> str:
    """Build Vobiz XML response for WebSocket streaming."""
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Stream bidirectional="true" keepCallAlive="true" contentType="audio/x-mulaw;rate=8000">
        {websocket_url}
    </Stream>
</Response>'''


# === FastAPI App ===

app = FastAPI(
    title="Vobiz Telephony Agent API",
    description="Voice bot API for Vobiz telephony integration",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# === Routes ===

@app.get("/")
async def root():
    """Health check endpoint."""
    return {"service": "Vobiz Telephony Server", "status": "running"}


@app.get("/health")
async def health():
    """Detailed health check."""
    return {"status": "healthy"}

@app.post("/outbound/call/")
async def make_outbound_call(request: OutboundCallRequest):
    """Initiate an outbound call.
    
    Args:
        request: Outbound call parameters
        
    Returns:
        Call initiation result
    """
    try:
        result = make_outbound_call_vobiz(
            request.customer_number,
            request.agent_id,
            request.caller_id
        )
        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "message": "Outbound call initiated",
                "customer_number": request.customer_number,
                "agent_id": request.agent_id,
                "caller_id": request.caller_id,
                "result": result
            }
        )
    except ValueError as e:
        logger.error(f"❌ Invalid request: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"❌ Outbound call failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/call/outbound")
async def inline_outbound_call(request: InlineCallRequest):
    """Initiate an outbound call with an inline agent config (no backend required).

    Called by the leadership-coach worker. Converts the worker's payload into
    Voicera's internal agent_config format, stores it in _session_store, then
    triggers a Vobiz outbound call keyed to the session ID.
    """
    session_id = str(_uuid.uuid4())

    # Build agent_config in the format expected by bot.py / services.py
    llm_provider = _normalise_provider(request.llm.get("provider", "OpenAI"))
    llm_model = request.llm.get("model", "gpt-4o-mini")

    stt_provider = _normalise_provider(request.stt.get("provider", "Deepgram"))
    stt_language = request.stt.get("language", "English")
    stt_args = request.stt.get("args", {})
    stt_model = stt_args.get("model") or request.stt.get("model", "nova-2")

    tts_provider = _normalise_provider(request.tts.get("provider", "OpenAI"))
    tts_args = request.tts.get("args", {})

    agent_config = {
        "system_prompt": request.systemPrompt,
        "greeting_message": request.greeting,
        "session_timeout_minutes": request.maxDurationSeconds // 60,
        "webhook_url": request.webhookUrl,
        "metadata": request.metadata,
        "agent_type": "mira",
        "llm_model": {
            "name": llm_provider,
            "args": {"model": llm_model},
        },
        "stt_model": {
            "name": stt_provider,
            "language": stt_language,
            "args": {"model": stt_model, **{k: v for k, v in stt_args.items() if k != "model"}},
        },
        "tts_model": {
            "name": tts_provider,
            "args": tts_args,
        },
    }

    _session_store[session_id] = agent_config
    logger.info(f"[inline] Stored session {session_id}: llm={llm_provider}/{llm_model} stt={stt_provider} tts={tts_provider}")

    try:
        result = make_outbound_call_vobiz(request.phone, agent_id=session_id)
    except ValueError as e:
        _session_store.pop(session_id, None)
        logger.error(f"[inline] Call config error: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        _session_store.pop(session_id, None)
        logger.error(f"[inline] Outbound call failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    return JSONResponse(status_code=200, content={
        "callId": session_id,
        "success": True,
        "vobizResult": result,
    })


async def _resolve_agent_config(agent_id: str) -> dict:
    """Return agent config from in-memory store (inline calls) or backend (legacy calls)."""
    if agent_id in _session_store:
        logger.info(f"[config] Using inline session config for {agent_id}")
        return _session_store[agent_id]
    logger.info(f"[config] Falling back to backend for agent_id={agent_id}")
    return await fetch_agent_config_from_backend(agent_id)


async def log_meeting(agent_id: str, form_data_dict: dict):
    try:
        agent_config = await _resolve_agent_config(agent_id)
        agent_type = agent_config.get('agent_type')
        org_id = agent_config.get('org_id')

        direction = form_data_dict.get('Direction', 'outbound')
        is_busy = form_data_dict.get('HangupCause', 'unknown') == 'USER_BUSY'
        start_time_utc =  datetime.now(timezone.utc).isoformat()
        if is_busy:
            end_time_utc = start_time_utc
        else:
            end_time_utc = ""

        meeting_data =   {
            "meeting_id": form_data_dict.get('CallUUID', 'unknown'),
            "agent_type": agent_type,
            "org_id": org_id,
            "start_time_utc": start_time_utc,
            "end_time_utc": end_time_utc,
            "inbound": direction=='inbound',
            "from_number": form_data_dict.get('From', 'unknown'),
            "to_number": form_data_dict.get('To', 'unknown'),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "call_busy": is_busy
            }
        logger.info(f"Meeting data: {meeting_data}")
        await create_meeting_in_backend(meeting_data)
        return {'status': "success"}
    except Exception as e:
        logger.error(f" Meeting log failed: {e}")
        return {'status': "error", 'message': str(e)}

@app.api_route("/answer", methods=["GET", "POST"])
async def vobiz_answer_webhook(request: Request):
    """Vobiz answer webhook - returns XML with WebSocket URL.
    
    This endpoint is called by Vobiz when a call is answered.
    It returns XML instructing Vobiz to connect to our WebSocket.
    """
    agent_id = request.query_params.get('agent_id')
    form_data = await request.form()
    form_data_dict = dict(form_data)
    event = form_data_dict.get('Event', 'unknown')
    hangup_cause = form_data_dict.get('HangupCause', 'USER_BUSY')

    if event == 'StartApp':
        await log_meeting(agent_id, form_data_dict)
        websocket_prefix = os.environ.get('JOHNAIC_WEBSOCKET_URL', '')
        websocket_url = f"{websocket_prefix}/agent/{agent_id}"
        return Response(
            content=_build_stream_xml(websocket_url),
            media_type="application/xml"
        )
    elif event == 'Hangup' and hangup_cause == 'USER_BUSY':
        logger.info(f"User hung up the call")
        await log_meeting(agent_id, form_data_dict)
    else:
        logger.info(f"Hang URL Event Sent")

@app.websocket("/agent/{agent_id}")
async def websocket_endpoint(websocket: WebSocket, agent_id: str):
    """WebSocket endpoint for Vobiz audio streaming.
    
    Args:
        websocket: WebSocket connection
        agent_id: Agent ID to use
    """
    await websocket.accept()
    logger.info(f"🔌 WebSocket connected: agent={agent_id}")
    
    call_sid = None
    stream_sid = None
    
    try:
        # Load agent configuration — inline session store first, backend as fallback
        agent_config = await _resolve_agent_config(agent_id)
        agent_type = agent_config.get("agent_type", "mira")

        logger.info(f"📥 Agent config: {agent_config}")
        if not agent_config:
            logger.error(f"❌ Failed to fetch agent config from backend: {agent_id}")
            return
        
        # Wait for start event with call metadata
        first_message = await websocket.receive_text()
        data = json.loads(first_message)
        
        if data.get("event") != "start":
            logger.warning(f"⚠️ Expected 'start' event, got: {data.get('event')}")
            return
        
        start_info = data.get("start", {})
        call_sid = start_info.get("callSid") or start_info.get("callId", "unknown")
        stream_sid = start_info.get("streamSid") or start_info.get("streamId", "unknown")
        
        logger.info(f"📞 Call started: call_sid={call_sid}, stream_sid={stream_sid}")
        logger.debug(f"📋 Start info: {start_info}")

        await bot(websocket, stream_sid, call_sid, agent_type, agent_config)

        #await update_meeting_end_time(call_sid, datetime.now(timezone.utc).isoformat())
    except FileNotFoundError as e:
        logger.error(f"❌ {e}")
        await websocket.close(code=1008, reason="Agent config not found")
    except Exception as e:
        logger.error(f"❌ WebSocket error: {e}")
        logger.debug(traceback.format_exc())
    finally:
        logger.info(f"🔌 WebSocket closed: call_sid={call_sid}")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=7860, log_level="info")