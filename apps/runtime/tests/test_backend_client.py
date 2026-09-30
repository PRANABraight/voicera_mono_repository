"""Unit tests for BackendClient (apps/runtime/services/backend.py).

Uses the house httpx.MockTransport pattern (see apps/telephony/tests/test_clients.py):
monkeypatch httpx.AsyncClient used by the backend module with one bound to a
MockTransport handler function.
"""

from __future__ import annotations

import json
from typing import Any, Callable

import httpx
import pytest

from apps.runtime.services.backend import BackendClient, BackendError


def _json_response(data: Any, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code,
        headers={"Content-Type": "application/json"},
        content=json.dumps(data).encode("utf-8"),
    )


def _patch_client(monkeypatch: pytest.MonkeyPatch, handler: Callable) -> None:
    transport = httpx.MockTransport(handler)

    class _PatchedAsyncClient(httpx.AsyncClient):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("apps.runtime.services.backend.httpx.AsyncClient", _PatchedAsyncClient)


@pytest.fixture(autouse=True)
def _api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTERNAL_API_KEY", "test-internal-key")
    monkeypatch.setenv("API_BASE_URL", "https://api.example.com/api/v1")


# --- get_bot_token ---


@pytest.mark.asyncio
async def test_get_bot_token_missing_org_id() -> None:
    client = BackendClient()
    with pytest.raises(BackendError, match="org_id is required"):
        await client.get_bot_token("   ")


@pytest.mark.asyncio
async def test_get_bot_token_missing_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INTERNAL_API_KEY", raising=False)
    client = BackendClient()
    with pytest.raises(BackendError, match="INTERNAL_API_KEY"):
        await client.get_bot_token("org-1")


@pytest.mark.asyncio
async def test_get_bot_token_caches_without_http_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return _json_response({"access_token": "tok-1"})

    _patch_client(monkeypatch, handler)
    client = BackendClient()
    first = await client.get_bot_token("org-1")
    second = await client.get_bot_token("org-1")
    assert first == "tok-1"
    assert second == "tok-1"
    assert calls["count"] == 1


@pytest.mark.asyncio
async def test_get_bot_token_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    _patch_client(monkeypatch, handler)
    client = BackendClient()
    with pytest.raises(BackendError, match="bot/token failed"):
        await client.get_bot_token("org-1")


@pytest.mark.asyncio
async def test_get_bot_token_missing_access_token(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response({})

    _patch_client(monkeypatch, handler)
    client = BackendClient()
    with pytest.raises(BackendError, match="missing access_token"):
        await client.get_bot_token("org-1")


# --- get_agent (401 refresh) ---


@pytest.mark.asyncio
async def test_get_agent_retries_once_on_401(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/bot/token"):
            return _json_response({"access_token": f"tok-{len(calls)}"})
        calls.append(request.headers.get("authorization", ""))
        if len(calls) == 1:
            return httpx.Response(401, text="unauthorized")
        return _json_response({"agent_id": "agent-1"})

    _patch_client(monkeypatch, handler)
    client = BackendClient()
    agent = await client.get_agent("agent-1", "org-1")
    assert agent == {"agent_id": "agent-1"}
    assert len(calls) == 2
    assert calls[0] != calls[1]


@pytest.mark.asyncio
async def test_get_agent_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/bot/token"):
            return _json_response({"access_token": "tok"})
        return httpx.Response(404, text="nope")

    _patch_client(monkeypatch, handler)
    client = BackendClient()
    with pytest.raises(BackendError, match="Agent not found"):
        await client.get_agent("agent-1", "org-1")


# --- update_call / update_call_by_provider_sid ---


@pytest.mark.asyncio
async def test_update_call_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/bot/token"):
            return _json_response({"access_token": "tok"})
        return httpx.Response(404, text="missing")

    _patch_client(monkeypatch, handler)
    client = BackendClient()
    with pytest.raises(BackendError, match="Call log not found"):
        await client.update_call("call-1", "org-1", {"status": "completed"})


@pytest.mark.asyncio
async def test_update_call_generic_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/bot/token"):
            return _json_response({"access_token": "tok"})
        return httpx.Response(500, text="server exploded")

    _patch_client(monkeypatch, handler)
    client = BackendClient()
    with pytest.raises(BackendError, match="server exploded"):
        await client.update_call("call-1", "org-1", {"status": "completed"})


@pytest.mark.asyncio
async def test_update_call_by_provider_sid_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/bot/token"):
            return _json_response({"access_token": "tok"})
        return httpx.Response(404, text="missing")

    _patch_client(monkeypatch, handler)
    client = BackendClient()
    with pytest.raises(BackendError, match="Call log not found for provider sid"):
        await client.update_call_by_provider_sid("org-1", "sid-1", {"status": "x"})


@pytest.mark.asyncio
async def test_update_call_by_provider_sid_generic_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/bot/token"):
            return _json_response({"access_token": "tok"})
        return httpx.Response(400, text="bad patch")

    _patch_client(monkeypatch, handler)
    client = BackendClient()
    with pytest.raises(BackendError, match="bad patch"):
        await client.update_call_by_provider_sid("org-1", "sid-1", {"status": "x"})


# --- notify_campaign_call_status ---


@pytest.mark.asyncio
async def test_notify_campaign_call_status_no_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("INTERNAL_API_KEY", raising=False)
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return _json_response({})

    _patch_client(monkeypatch, handler)
    client = BackendClient()
    await client.notify_campaign_call_status("org-1", "call-1", "answered")
    assert calls["count"] == 0


@pytest.mark.asyncio
async def test_notify_campaign_call_status_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        body = json.loads(request.content)
        assert body == {
            "org_id": "org-1",
            "call_id": "call-1",
            "call_response": "answered",
        }
        return _json_response({})

    _patch_client(monkeypatch, handler)
    client = BackendClient()
    await client.notify_campaign_call_status("org-1", "call-1", "answered")


@pytest.mark.asyncio
async def test_notify_campaign_call_status_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="fail")

    _patch_client(monkeypatch, handler)
    client = BackendClient()
    with pytest.raises(BackendError, match="campaign/call-status failed"):
        await client.notify_campaign_call_status("org-1", "call-1", "answered")


# --- retrieve_knowledge_chunks (fail-open) ---


@pytest.mark.asyncio
async def test_retrieve_knowledge_chunks_no_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("INTERNAL_API_KEY", raising=False)
    client = BackendClient()
    chunks = await client.retrieve_knowledge_chunks(org_id="org-1", question="hi")
    assert chunks == []


@pytest.mark.asyncio
async def test_retrieve_knowledge_chunks_transport_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    _patch_client(monkeypatch, handler)
    client = BackendClient()
    chunks = await client.retrieve_knowledge_chunks(org_id="org-1", question="hi")
    assert chunks == []


@pytest.mark.asyncio
async def test_retrieve_knowledge_chunks_malformed_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response({"chunks": "not-a-list"})

    _patch_client(monkeypatch, handler)
    client = BackendClient()
    chunks = await client.retrieve_knowledge_chunks(org_id="org-1", question="hi")
    assert chunks == []


@pytest.mark.asyncio
async def test_retrieve_knowledge_chunks_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["question"] == "hi"
        return _json_response({"chunks": [{"text": "a"}]})

    _patch_client(monkeypatch, handler)
    client = BackendClient()
    chunks = await client.retrieve_knowledge_chunks(org_id="org-1", question="hi")
    assert chunks == [{"text": "a"}]
