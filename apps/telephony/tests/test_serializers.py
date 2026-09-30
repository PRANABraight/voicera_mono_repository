"""Tests for telephony frame serializer factory."""

from __future__ import annotations

import base64
import json

import pytest
from pipecat.frames.frames import InputAudioRawFrame, OutputAudioRawFrame

from apps.telephony.providers.vobiz.serializers import VobizFrameSerializer
from apps.telephony.serializers import create_frame_serializer


@pytest.mark.parametrize("provider", ["vobiz", "plivo", "Vobiz", "Plivo"])
def test_create_frame_serializer_known_providers(provider: str) -> None:
    serializer = create_frame_serializer(
        provider,
        stream_sid="stream-1",
        call_sid="call-1",
        sample_rate=8000,
    )
    assert serializer is not None
    assert type(serializer).__name__ in ("VobizFrameSerializer", "PlivoFrameSerializer")


def test_plivo_serializer_reexport() -> None:
    from apps.telephony.providers.plivo.serializers import PlivoFrameSerializer

    serializer = create_frame_serializer(
        "plivo",
        stream_sid="stream-1",
        call_sid="call-1",
        sample_rate=8000,
    )
    assert isinstance(serializer, PlivoFrameSerializer)


def test_vobiz_16k_serializer() -> None:
    serializer = create_frame_serializer(
        "vobiz",
        stream_sid="stream-1",
        call_sid="call-1",
        sample_rate=16000,
    )
    assert type(serializer).__name__ == "VobizFrameSerializer"


def test_unsupported_provider() -> None:
    with pytest.raises(ValueError, match="Unsupported"):
        create_frame_serializer(
            "twilio",
            stream_sid="stream-1",
            call_sid="call-1",
        )


def test_empty_provider_rejected() -> None:
    with pytest.raises(ValueError, match="provider id is required"):
        create_frame_serializer(
            "",
            stream_sid="stream-1",
            call_sid="call-1",
        )


@pytest.mark.anyio
async def test_vobiz_16k_serialize_audio_frame_no_resample() -> None:
    serializer = VobizFrameSerializer(
        stream_sid="stream-1",
        call_sid="call-1",
        params=VobizFrameSerializer.InputParams(vobiz_sample_rate=16000),
    )
    frame = OutputAudioRawFrame(audio=b"\x01\x02", sample_rate=16000, num_channels=1)
    out = await serializer.serialize(frame)
    payload = json.loads(out)
    assert payload["event"] == "playAudio"
    assert payload["media"]["contentType"] == "audio/x-l16"
    assert payload["media"]["sampleRate"] == 16000
    assert base64.b64decode(payload["media"]["payload"]) == b"\x01\x02"
    assert payload["streamId"] == "stream-1"


@pytest.mark.anyio
async def test_vobiz_16k_serialize_resamples_when_rate_mismatched() -> None:
    serializer = VobizFrameSerializer(
        stream_sid="stream-1",
        call_sid="call-1",
        params=VobizFrameSerializer.InputParams(vobiz_sample_rate=16000),
    )
    frame = OutputAudioRawFrame(audio=b"\x01\x02", sample_rate=8000, num_channels=1)
    out = await serializer.serialize(frame)
    payload = json.loads(out)
    assert payload["media"]["sampleRate"] == 16000
    # Resampled payload differs in length/content from the raw 8kHz bytes.
    assert base64.b64decode(payload["media"]["payload"]) != b"\x01\x02"


@pytest.mark.anyio
async def test_vobiz_16k_serialize_non_audio_frame_falls_back(monkeypatch) -> None:
    serializer = VobizFrameSerializer(
        stream_sid="stream-1",
        call_sid="call-1",
        params=VobizFrameSerializer.InputParams(vobiz_sample_rate=16000),
    )

    called = {}

    async def fake_super_serialize(self, frame):
        called["frame"] = frame
        return "fallback"

    monkeypatch.setattr(
        "pipecat.serializers.plivo.PlivoFrameSerializer.serialize",
        fake_super_serialize,
    )
    frame = object()
    result = await serializer.serialize(frame)
    assert result == "fallback"
    assert called["frame"] is frame


@pytest.mark.anyio
async def test_vobiz_16k_deserialize_media_payload() -> None:
    serializer = VobizFrameSerializer(
        stream_sid="stream-1",
        call_sid="call-1",
        params=VobizFrameSerializer.InputParams(vobiz_sample_rate=16000),
    )
    payload = base64.b64encode(b"\x03\x04").decode("utf-8")
    data = json.dumps({"event": "media", "media": {"payload": payload}})
    frame = await serializer.deserialize(data)
    assert isinstance(frame, InputAudioRawFrame)
    assert frame.audio == b"\x03\x04"
    assert frame.sample_rate == 16000


@pytest.mark.anyio
async def test_vobiz_16k_deserialize_media_missing_payload() -> None:
    serializer = VobizFrameSerializer(
        stream_sid="stream-1",
        call_sid="call-1",
        params=VobizFrameSerializer.InputParams(vobiz_sample_rate=16000),
    )
    data = json.dumps({"event": "media", "media": {}})
    assert await serializer.deserialize(data) is None


@pytest.mark.anyio
async def test_vobiz_16k_deserialize_invalid_json() -> None:
    serializer = VobizFrameSerializer(
        stream_sid="stream-1",
        call_sid="call-1",
        params=VobizFrameSerializer.InputParams(vobiz_sample_rate=16000),
    )
    assert await serializer.deserialize("not json") is None


@pytest.mark.anyio
async def test_vobiz_16k_deserialize_non_media_event_falls_back(monkeypatch) -> None:
    serializer = VobizFrameSerializer(
        stream_sid="stream-1",
        call_sid="call-1",
        params=VobizFrameSerializer.InputParams(vobiz_sample_rate=16000),
    )

    async def fake_super_deserialize(self, data):
        return "fallback-frame"

    monkeypatch.setattr(
        "pipecat.serializers.plivo.PlivoFrameSerializer.deserialize",
        fake_super_deserialize,
    )
    data = json.dumps({"event": "other"})
    result = await serializer.deserialize(data)
    assert result == "fallback-frame"
