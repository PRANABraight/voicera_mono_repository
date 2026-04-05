"""FastAPI server for Vobiz telephony integration with optimized TCP settings."""

import os
import socket
import json
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any

from loguru import logger
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, Request, HTTPException
from fastapi.responses import JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests

from .bot import bot, ubona_bot
from .backend_utils import (
    create_meeting_in_backend,
    update_meeting_end_time,
    fetch_agent_config_from_backend,
    fetch_integration_key,
)


load_dotenv()

# Constants
AGENT_CONFIGS_DIR = Path("agent_configs")


# === TCP_NODELAY WebSocket Protocol ===

def create_nodelay_websocket_protocol():
    """Create a WebSocket protocol class with TCP_NODELAY enabled.
    
    This disables Nagle's algorithm for lower latency on small packets,
    which is critical for real-time voice applications.
    """
    try:
        from uvicorn.protocols.websockets.websockets_impl import WebSocketProtocol

        class NoDelayWebSocketProtocol(WebSocketProtocol):
            def connection_made(self, transport):
                # Set TCP_NODELAY before calling parent
                try:
                    sock = transport.get_extra_info("socket")
                    if sock is not None:
                        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                        logger.debug("TCP_NODELAY enabled on WebSocket connection")
                except Exception as e:
                    logger.warning(f"Failed to set TCP_NODELAY: {e}")
                
                super().connection_made(transport)

        return NoDelayWebSocketProtocol
    
    except ImportError:
        logger.warning("Could not import WebSocketProtocol from uvicorn, TCP_NODELAY not available")
        return None


# === In-memory session store for browser web calls ===
# Maps session_id → agent config dict; cleared when session connects.
_web_sessions: Dict[str, Dict[str, Any]] = {}


# === Pydantic Models ===

class OutboundCallRequest(BaseModel):
    """Request model for initiating outbound calls."""
    customer_number: str
    agent_id: str
    custom_field: Optional[str] = None
    caller_id: Optional[str] = None


class WebCallRequest(BaseModel):
    """Request model for browser-based web voice sessions.
    
    Compatible with the leadership-coach app's /api/voicera/call → /call/web flow.
    """
    systemPrompt: Optional[str] = None
    greeting: Optional[str] = None
    callId: Optional[str] = None
    webhookUrl: Optional[str] = None
    llm: Optional[Dict[str, Any]] = None
    stt: Optional[Dict[str, Any]] = None
    tts: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None


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
    caller_id: Optional[str] = None,
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
    auth_id = _get_env_or_raise("VOBIZ_AUTH_ID")
    auth_token = _get_env_or_raise("VOBIZ_AUTH_TOKEN")
    server_url = _get_env_or_raise("JOHNAIC_SERVER_URL")
    vobiz_api_base_url = _get_env_or_raise("VOBIZ_API_BASE")

    from_number = caller_id or os.environ.get("VOBIZ_CALLER_ID")
    if not from_number:
        raise ValueError("No caller_id provided and VOBIZ_CALLER_ID not set")

    headers = {
        "X-Auth-ID": auth_id,
        "X-Auth-Token": auth_token,
        "Content-Type": "application/json",
    }
    payload = {
        "from": from_number,
        "to": customer_number,
        "answer_url": f"{server_url}/answer?agent_id={agent_id}",
        "answer_method": "POST",
    }

    logger.info(f"📞 Outbound call: {from_number} → {customer_number} (agent: {agent_id})")
    
    vobiz_api_url = f"{vobiz_api_base_url}/Account/{auth_id}/Call/"
    response = requests.post(vobiz_api_url, json=payload, headers=headers, timeout=30)
    response.raise_for_status()

    result = response.json()
    logger.info(f"✅ Call initiated: {result.get('call_uuid', 'unknown')}")
    return result


def _build_stream_xml(websocket_url: str) -> str:
    """Build Vobiz XML response for WebSocket streaming."""
    sample_rate = int(os.environ.get("SAMPLE_RATE", "8000"))
    
    # Use L16 for 16kHz per Vobiz spec (μ-law is 8kHz only)
    if sample_rate == 16000:
        content_type = "audio/x-l16;rate=16000"
    else:
        content_type = f"audio/x-mulaw;rate={sample_rate}"
        
    logger.info(f"Sending XML with contentType: {content_type}")
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Stream bidirectional="true" keepCallAlive="true" contentType="{content_type}">
        {websocket_url}
    </Stream>
</Response>'''


# === FastAPI App ===

app = FastAPI(
    title="Vobiz Telephony Agent API",
    description="Voice bot API for Vobiz telephony integration",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
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


@app.post("/call/web")
async def create_web_call_session(request: WebCallRequest, req: Request):
    """Create a browser-based voice session.

    Accepts agent config inline (system prompt, LLM/STT/TTS config) and returns a
    session ID plus WebSocket URL for the browser to connect to.

    This endpoint is called by the leadership-coach app's /api/voicera/call Next.js route.
    """
    api_key = os.environ.get("INTERNAL_API_KEY")
    if api_key:
        auth_header = req.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer ") or auth_header[7:] != api_key:
            raise HTTPException(status_code=401, detail="Unauthorized")

    session_id = request.callId or str(uuid.uuid4())

    # Build an inline agent config from the request payload
    llm_config = request.llm or {}
    stt_config = request.stt or {}
    tts_config = request.tts or {}

    agent_config = {
        "agent_type": "web_session",
        "system_prompt": request.systemPrompt or "You are a helpful voice assistant.",
        "greeting_message": request.greeting or "",
        "session_timeout_minutes": 15,
        "llm_model": {
            "name": llm_config.get("provider", os.environ.get("VOICERA_LLM_PROVIDER", "openai")),
            "args": {
                "model": llm_config.get("model", os.environ.get("VOICERA_LLM_MODEL", "gpt-4o-mini")),
            },
        },
        "stt_model": {
            "name": stt_config.get("provider", "deepgram"),
            "language": stt_config.get("language", "English"),
            "args": stt_config.get("args", {"model": "nova-2"}),
        },
        "tts_model": {
            "name": tts_config.get("provider", os.environ.get("VOICERA_TTS_PROVIDER", "openai")),
            "language": "English",
            "args": tts_config.get("args", {
                "voice": os.environ.get("VOICERA_TTS_VOICE", "nova"),
            }),
        },
        "metadata": request.metadata or {},
        "webhook_url": request.webhookUrl,
    }

    _web_sessions[session_id] = agent_config
    logger.info(f"✅ Web session created: {session_id}")

    ws_base = os.environ.get("JOHNAIC_WEBSOCKET_URL", "")
    if not ws_base:
        server_url = os.environ.get("JOHNAIC_SERVER_URL", "")
        if server_url:
            ws_base = server_url.replace("https://", "wss://").replace("http://", "ws://")
    ws_base = ws_base.rstrip("/")

    ws_url = f"{ws_base}/browser/session/{session_id}"

    return JSONResponse({
        "sessionId": session_id,
        "session_id": session_id,
        "wsUrl": ws_url,
        "ws_url": ws_url,
    })


@app.websocket("/browser/session/{session_id}")
async def browser_session_websocket(websocket: WebSocket, session_id: str):
    """WebSocket endpoint for browser-based voice sessions created via /call/web.

    Uses inline agent config (no registered agent_id required).
    Sends back transcript events so the browser UI can show live transcripts.
    """
    await websocket.accept()
    logger.info(f"🔌 Browser session WebSocket connected: session={session_id}")

    agent_config = _web_sessions.pop(session_id, None)
    if not agent_config:
        logger.error(f"❌ No session config for session_id={session_id}")
        await websocket.close(code=1008, reason="Session not found or expired")
        return

    call_sid = None
    stream_sid = None

    try:
        first_message = await websocket.receive_text()
        data = json.loads(first_message)
        if data.get("event") != "start":
            logger.warning(f"⚠️ Expected 'start' event, got: {data.get('event')}")
            return

        start_info = data.get("start", {})
        call_sid = start_info.get("callSid") or start_info.get("callId", session_id)
        stream_sid = start_info.get("streamSid") or start_info.get("streamId", session_id)

        logger.info(f"📞 Browser session call: call_sid={call_sid}")

        async def send_transcript(role: str, content: str, timestamp: Optional[str]):
            await websocket.send_text(json.dumps({
                "event": "transcript",
                "role": role,
                "content": content,
                "timestamp": timestamp,
            }))

        await bot(
            websocket,
            stream_sid,
            call_sid,
            agent_config.get("agent_type", "web_session"),
            agent_config,
            transcript_callback=send_transcript,
            force_sample_rate=16000,  # Browser always sends 16kHz L16 PCM
        )

    except Exception as e:
        logger.error(f"❌ Browser session WebSocket error: {e}")
        logger.error(traceback.format_exc())
    finally:
        _web_sessions.pop(session_id, None)
        logger.info(f"🔌 Browser session WebSocket closed: call_sid={call_sid}")


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
            request.caller_id,
        )
        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "message": "Outbound call initiated",
                "customer_number": request.customer_number,
                "agent_id": request.agent_id,
                "caller_id": request.caller_id,
                "result": result,
            },
        )
    except ValueError as e:
        logger.error(f"❌ Invalid request: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"❌ Outbound call failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def log_meeting(agent_id: str, form_data_dict: dict):
    """Log meeting/call data to backend."""
    try:
        agent_config = await fetch_agent_config_from_backend(agent_id)
        agent_type = agent_config.get("agent_type")
        org_id = agent_config.get("org_id")

        direction = form_data_dict.get("Direction", "outbound")
        is_busy = form_data_dict.get("HangupCause", "unknown") == "USER_BUSY"
        start_time_utc = datetime.now(timezone.utc).isoformat()
        end_time_utc = start_time_utc if is_busy else ""

        meeting_data = {
            "meeting_id": form_data_dict.get("CallUUID", "unknown"),
            "agent_type": agent_type,
            "org_id": org_id,
            "start_time_utc": start_time_utc,
            "end_time_utc": end_time_utc,
            "inbound": direction == "inbound",
            "from_number": form_data_dict.get("From", "unknown"),
            "to_number": form_data_dict.get("To", "unknown"),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "call_busy": is_busy,
        }
        logger.info(f"Meeting data: {meeting_data}")
        await create_meeting_in_backend(meeting_data)
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Meeting log failed: {e}")
        return {"status": "error", "message": str(e)}


@app.api_route("/answer", methods=["GET", "POST"])
async def vobiz_answer_webhook(request: Request):
    """Vobiz answer webhook - returns XML with WebSocket URL.

    This endpoint is called by Vobiz when a call is answered.
    It returns XML instructing Vobiz to connect to our WebSocket.
    """
    agent_id = request.query_params.get("agent_id")
    form_data = await request.form()
    form_data_dict = dict(form_data)
    event = form_data_dict.get("Event", "unknown")
    hangup_cause = form_data_dict.get("HangupCause", "USER_BUSY")

    if event == "StartApp":
        await log_meeting(agent_id, form_data_dict)
        websocket_prefix = os.environ.get("JOHNAIC_WEBSOCKET_URL", "")
        websocket_url = f"{websocket_prefix}/agent/{agent_id}"
        return Response(
            content=_build_stream_xml(websocket_url),
            media_type="application/xml",
        )
    elif event == "Hangup" and hangup_cause == "USER_BUSY":
        logger.info("User hung up the call")
        await log_meeting(agent_id, form_data_dict)
    else:
        logger.info("Hang URL Event Sent")


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
        # Load agent configuration
        agent_config = await fetch_agent_config_from_backend(agent_id)
        agent_type = agent_config.get("agent_type")

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

    except FileNotFoundError as e:
        logger.error(f"❌ {e}")
        await websocket.close(code=1008, reason="Agent config not found")
    except Exception as e:
        logger.error(f"❌ WebSocket error: {e}")
        logger.debug(traceback.format_exc())
    finally:
        logger.info(f"🔌 WebSocket closed: call_sid={call_sid}")

@app.websocket("/browser/agent/{agent_id}")
async def browser_websocket_endpoint(websocket: WebSocket, agent_id: str):
    """WebSocket endpoint for browser-based voice sessions with live transcript events.

    Accepts the same PCM-over-WebSocket protocol as the telephony endpoint.
    Sends back `transcript` events so the browser UI can display live transcripts.
    """
    await websocket.accept()
    logger.info(f"🔌 Browser WebSocket connected: agent={agent_id}")

    call_sid = None
    stream_sid = None

    try:
        agent_config = await fetch_agent_config_from_backend(agent_id)
        agent_type = agent_config.get("agent_type")

        if not agent_config:
            logger.error(f"❌ Failed to fetch agent config from backend: {agent_id}")
            return

        first_message = await websocket.receive_text()
        data = json.loads(first_message)
        if data.get("event") != "start":
            logger.warning(f"⚠️ Expected 'start' event, got: {data.get('event')}")
            return

        start_info = data.get("start", {})
        call_sid = start_info.get("callSid") or start_info.get("callId", "unknown")
        stream_sid = start_info.get("streamSid") or start_info.get("streamId", "unknown")

        logger.info(f"📞 Browser call started: call_sid={call_sid}, stream_sid={stream_sid}")

        async def send_transcript(role: str, content: str, timestamp: Optional[str]):
            await websocket.send_text(json.dumps({
                "event": "transcript",
                "role": role,
                "content": content,
                "timestamp": timestamp,
            }))

        await bot(
            websocket,
            stream_sid,
            call_sid,
            agent_type,
            agent_config,
            transcript_callback=send_transcript,
        )

    except FileNotFoundError as e:
        logger.error(f"❌ {e}")
        await websocket.close(code=1008, reason="Agent config not found")
    except Exception as e:
        logger.error(f"❌ Browser WebSocket error: {e}")
        logger.debug(traceback.format_exc())
    finally:
        logger.info(f"🔌 Browser WebSocket closed: call_sid={call_sid}")


# Ubona: hardcoded to Mahavistaar agent; answer URL is https://vobiz.johnaic.com/ubona
UBONA_AGENT_TYPE = "Mahavistaar"
UBONA_STREAM_PATH_ID = "mahavistaar"


def _fetch_mahavistaar_config() -> Optional[dict]:
    """Fetch Mahavistaar agent config from backend (inline, no backend_utils)."""
    backend_url = os.environ.get("VOICERA_BACKEND_URL", "http://localhost:8000")
    api_key = os.environ.get("INTERNAL_API_KEY")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    endpoint = f"{backend_url}/api/v1/agents/config/{UBONA_AGENT_TYPE}"
    try:
        response = requests.get(endpoint, headers=headers, timeout=10)
        response.raise_for_status()
        agent_data = response.json()
        agent_config = agent_data.get("agent_config", {})
        if "org_id" in agent_data:
            agent_config["org_id"] = agent_data["org_id"]
        if "agent_type" in agent_data:
            agent_config["agent_type"] = agent_data["agent_type"]
        if "greeting_message" in agent_data:
            agent_config["greeting_message"] = agent_data["greeting_message"]
        return agent_config
    except Exception as e:
        logger.error(f"Failed to fetch Mahavistaar config: {e}")
        return None


@app.api_route("/ubona", methods=["GET", "POST"])
async def ubona_answer(request: Request):
    """Ubona answer webhook - returns XML with WebSocket URL. Hardcoded to Mahavistaar."""
    form_data = dict(await request.form())
    event = form_data.get("Event", "unknown")

    if event == "StartApp":
        await log_meeting(UBONA_STREAM_PATH_ID, form_data)
        ws_prefix = os.environ.get("JOHNAIC_WEBSOCKET_URL", "https://vobiz.johnaic.com")
        xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Stream bidirectional="true" keepCallAlive="true" contentType="audio/x-mulaw;rate=8000">
        {ws_prefix}/ubona/stream/{UBONA_STREAM_PATH_ID}
    </Stream>
</Response>'''
        return Response(content=xml, media_type="application/xml")
    elif event == "Hangup":
        await log_meeting(UBONA_STREAM_PATH_ID, form_data)

    return Response(status_code=200)


@app.websocket("/ubona/stream/{agent_id}")
async def ubona_stream(websocket: WebSocket, agent_id: str):
    """Ubona WebSocket endpoint for audio streaming. Hardcoded to Mahavistaar."""
    await websocket.accept()
    logger.info(f"🔌 Ubona WS connected: agent={agent_id}")

    call_id = stream_id = None

    try:
        agent_config = _fetch_mahavistaar_config()
        if not agent_config:
            logger.error(f"❌ No config for {UBONA_AGENT_TYPE}")
            return
        agent_type = agent_config.get("agent_type", UBONA_AGENT_TYPE)

        # Handle connected event (optional)
        msg = json.loads(await websocket.receive_text())
        if msg.get("event") == "connected":
            msg = json.loads(await websocket.receive_text())

        if msg.get("event") != "start":
            logger.warning(f"⚠️ Expected 'start', got: {msg.get('event')}")
            return

        # Spec: callId/streamId at top level or under start (Ubona Media Stream v1.0.0)
        start_obj = msg.get("start", {})
        call_id = msg.get("callId") or start_obj.get("callId", "unknown")
        stream_id = msg.get("streamId") or start_obj.get("streamId", "unknown")

        logger.info(f"📞 Ubona call: call_id={call_id}, stream_id={stream_id}")

        await ubona_bot(websocket, stream_id, call_id, agent_type, agent_config)

    except Exception as e:
        logger.error(f"❌ Ubona WS error: {e}")
        logger.debug(traceback.format_exc())
    finally:
        logger.info(f"🔌 Ubona WS closed: call_id={call_id}")

def run_server(host: str = "0.0.0.0", port: int = 7860, log_level: str = "info"):
    """Run the server with optimized settings for low-latency voice applications.
    
    Args:
        host: Host to bind to
        port: Port to bind to
        log_level: Logging level
    """
    import uvicorn

    # Create config with optimized settings
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level=log_level,
        # Use uvloop for better async performance (if available)
        loop="auto",
        # HTTP/1.1 settings
        http="auto",
        # WebSocket settings
        ws="websockets",
    )

    # Set custom WebSocket protocol with TCP_NODELAY
    nodelay_protocol = create_nodelay_websocket_protocol()
    if nodelay_protocol:
        config.ws_protocol_class = nodelay_protocol
        logger.info("✅ TCP_NODELAY enabled for WebSocket connections (Nagle's algorithm disabled)")
    else:
        logger.warning("⚠️ Could not enable TCP_NODELAY, latency may be affected")

    server = uvicorn.Server(config)
    server.run()


if __name__ == "__main__":
    run_server(host="0.0.0.0", port=7860, log_level="info")