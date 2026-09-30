"""Tests for apps/runtime/services/storage/{object_storage,call_artifacts,transcript}.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.runtime.services.storage import call_artifacts, object_storage, transcript


# --- object_storage.py ---


def test_object_key_sanitizes_unsafe_characters() -> None:
    key = object_storage.object_key("org/1", "call 2!", "transcript.txt")
    assert key == "org_1/call_2_/transcript.txt"


def test_object_key_empty_segment_falls_back_to_unknown() -> None:
    assert object_storage.object_key("", "call-1", "f.txt") == "unknown/call-1/f.txt"


def test_minio_uri_uses_configured_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINIO_BUCKET", "custom-bucket")
    uri = object_storage.minio_uri("org-1", "call-1", "recording.wav")
    assert uri == "minio://custom-bucket/org-1/call-1/recording.wav"


@pytest.mark.asyncio
async def test_upload_bytes_success(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = MagicMock()
    object_storage._minio_client.cache_clear()
    monkeypatch.setattr(object_storage, "_minio_client", lambda: fake_client)

    uri = await object_storage.upload_bytes(
        "org-1", "call-1", "transcript.txt", b"hello", "text/plain"
    )

    assert uri == object_storage.minio_uri("org-1", "call-1", "transcript.txt")
    fake_client.put_object.assert_called_once()
    args, kwargs = fake_client.put_object.call_args
    assert args[0] == object_storage._minio_bucket()
    assert args[1] == object_storage.object_key("org-1", "call-1", "transcript.txt")
    assert kwargs["length"] == len(b"hello")
    assert kwargs["content_type"] == "text/plain"


@pytest.mark.asyncio
async def test_upload_bytes_client_raises_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = MagicMock()
    fake_client.put_object.side_effect = RuntimeError("minio down")
    monkeypatch.setattr(object_storage, "_minio_client", lambda: fake_client)

    result = await object_storage.upload_bytes(
        "org-1", "call-1", "transcript.txt", b"hello", "text/plain"
    )
    assert result is None


# --- call_artifacts.py: save_and_link ---


@pytest.mark.asyncio
async def test_save_and_link_skips_without_call_id() -> None:
    result = await call_artifacts.save_and_link(
        org_id="org-1",
        call_id=None,
        filename="f.txt",
        data=b"x",
        content_type="text/plain",
        url_field="transcript_url",
        linked=True,
    )
    assert result is True


@pytest.mark.asyncio
async def test_save_and_link_upload_failure_returns_linked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        call_artifacts, "upload_bytes", AsyncMock(return_value=None)
    )
    result = await call_artifacts.save_and_link(
        org_id="org-1",
        call_id="call-1",
        filename="f.txt",
        data=b"x",
        content_type="text/plain",
        url_field="transcript_url",
        linked=False,
    )
    assert result is False


@pytest.mark.asyncio
async def test_save_and_link_skips_patch_when_link_once_and_already_linked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        call_artifacts, "upload_bytes", AsyncMock(return_value="minio://bucket/key")
    )
    update_call = AsyncMock()
    monkeypatch.setattr(call_artifacts.backend_client, "update_call", update_call)

    result = await call_artifacts.save_and_link(
        org_id="org-1",
        call_id="call-1",
        filename="f.txt",
        data=b"x",
        content_type="text/plain",
        url_field="recording_url",
        link_once=True,
        linked=True,
    )

    assert result is True
    update_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_save_and_link_patches_call_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        call_artifacts, "upload_bytes", AsyncMock(return_value="minio://bucket/key")
    )
    update_call = AsyncMock()
    monkeypatch.setattr(call_artifacts.backend_client, "update_call", update_call)

    result = await call_artifacts.save_and_link(
        org_id="org-1",
        call_id="call-1",
        filename="f.txt",
        data=b"x",
        content_type="text/plain",
        url_field="transcript_url",
    )

    assert result is True
    update_call.assert_awaited_once_with(
        "call-1", "org-1", {"transcript_url": "minio://bucket/key"}
    )


@pytest.mark.asyncio
async def test_save_and_link_patch_failure_returns_linked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        call_artifacts, "upload_bytes", AsyncMock(return_value="minio://bucket/key")
    )
    monkeypatch.setattr(
        call_artifacts.backend_client,
        "update_call",
        AsyncMock(side_effect=RuntimeError("api down")),
    )

    result = await call_artifacts.save_and_link(
        org_id="org-1",
        call_id="call-1",
        filename="f.txt",
        data=b"x",
        content_type="text/plain",
        url_field="transcript_url",
        linked=False,
    )
    assert result is False


# --- transcript.py: TranscriptWriter + register_transcript_file_logging ---


def test_transcript_writer_append_and_object_uri() -> None:
    writer = transcript.TranscriptWriter(org_id="org-1", call_id="call-1")
    assert writer.object_uri == object_storage.minio_uri(
        "org-1", "call-1", "transcript.txt"
    )
    writer.append("user", "t1", "hello")
    assert "hello" in writer._buffer
    assert writer._has_content is True


@pytest.mark.asyncio
async def test_transcript_writer_flush_noop_without_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    save_and_link = AsyncMock()
    monkeypatch.setattr(transcript, "save_and_link", save_and_link)
    writer = transcript.TranscriptWriter(org_id="org-1", call_id="call-1")

    await writer.flush()

    save_and_link.assert_not_awaited()


@pytest.mark.asyncio
async def test_transcript_writer_flush_uploads_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    save_and_link = AsyncMock()
    monkeypatch.setattr(transcript, "save_and_link", save_and_link)
    writer = transcript.TranscriptWriter(org_id="org-1", call_id="call-1")
    writer.append("user", "t1", "hello")

    await writer.flush()
    await writer.flush()

    save_and_link.assert_awaited_once()
    _, kwargs = save_and_link.await_args
    assert kwargs["url_field"] == "transcript_url"
    assert kwargs["org_id"] == "org-1"
    assert kwargs["call_id"] == "call-1"


class _FakeAggregator:
    """Minimal stand-in exposing the ``event_handler`` decorator API."""

    def __init__(self) -> None:
        self._handlers: dict[str, list] = {}

    def event_handler(self, name: str):
        def decorator(fn):
            self._handlers.setdefault(name, []).append(fn)
            return fn

        return decorator

    async def fire(self, name: str, *args) -> None:
        for handler in self._handlers.get(name, []):
            await handler(self, *args)


@pytest.mark.asyncio
async def test_register_transcript_file_logging_buffers_turns() -> None:
    user_agg = _FakeAggregator()
    assistant_agg = _FakeAggregator()

    writer = transcript.register_transcript_file_logging(
        user_agg, assistant_agg, org_id="org-1", call_id="call-1"
    )

    user_message = type("Msg", (), {"timestamp": "t1", "content": "hi"})()
    await user_agg.fire("on_user_turn_stopped", None, user_message)

    assistant_message = type("Msg", (), {"timestamp": "t2", "content": "hello back"})()
    await assistant_agg.fire("on_assistant_turn_stopped", assistant_message)

    empty_message = type("Msg", (), {"timestamp": "t3", "content": ""})()
    await assistant_agg.fire("on_assistant_turn_stopped", empty_message)

    assert "hi" in writer._buffer
    assert "hello back" in writer._buffer
