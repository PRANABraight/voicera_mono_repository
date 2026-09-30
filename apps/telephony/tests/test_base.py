"""Tests for shared telephony HTTP helpers (apps.telephony.base)."""

from __future__ import annotations

from typing import Any, Callable

import httpx
import pytest

from apps.telephony.base import (
    ApiResult,
    extract_recording_id,
    extract_recording_url,
    request_bytes,
    request_json,
)


def _patch_client(monkeypatch: pytest.MonkeyPatch, handler: Callable):
    transport = httpx.MockTransport(handler)

    class _PatchedAsyncClient(httpx.AsyncClient):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("apps.telephony.base.httpx.AsyncClient", _PatchedAsyncClient)


def test_api_result_ok_property():
    assert ApiResult(status="success").ok is True
    assert ApiResult(status="fail").ok is False


def test_extract_recording_id_non_dict():
    assert extract_recording_id("not-a-dict") is None
    assert extract_recording_id(None) is None


def test_extract_recording_url_non_dict():
    assert extract_recording_url(["nope"]) is None
    assert extract_recording_url(None) is None


@pytest.mark.anyio
async def test_request_json_empty_body(monkeypatch: pytest.MonkeyPatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(204, content=b"")

    _patch_client(monkeypatch, handler)
    data, err = await request_json("GET", "https://example.com/x")
    assert err is None
    assert data == {}


@pytest.mark.anyio
async def test_request_json_invalid_json_body(monkeypatch: pytest.MonkeyPatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    _patch_client(monkeypatch, handler)
    data, err = await request_json("GET", "https://example.com/x")
    assert err is None
    assert data == {}


@pytest.mark.anyio
async def test_request_json_connection_error(monkeypatch: pytest.MonkeyPatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    _patch_client(monkeypatch, handler)
    data, err = await request_json("GET", "https://example.com/x", provider_label="X")
    assert data is None
    assert "Failed to connect to X API" in err


@pytest.mark.anyio
async def test_request_json_unexpected_error(monkeypatch: pytest.MonkeyPatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise RuntimeError("kaboom")

    _patch_client(monkeypatch, handler)
    data, err = await request_json("GET", "https://example.com/x", provider_label="X")
    assert data is None
    assert "X request error" in err


@pytest.mark.anyio
async def test_request_bytes_error(monkeypatch: pytest.MonkeyPatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    _patch_client(monkeypatch, handler)
    result = await request_bytes("https://example.com/x")
    assert result is None
