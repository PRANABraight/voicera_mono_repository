"""/health and /models: the two read-only routes that describe the gateway's
own state rather than proxying to it.

/health's degraded branch and _probe's exception branch only fire when an
upstream is enabled but not answering -- distinct from the "not deployed"
shape covered elsewhere. /models merges the catalogue with what is actually
live, which needs a real catalogue entry to exercise the merge at all.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gateway"))

from app.config import Settings, Upstream  # noqa: E402
from app.main import create_app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def test_health_reports_degraded_when_a_deployed_upstream_is_unreachable():
    """_probe's except branch: an enabled slot whose /health call itself raises
    (nothing listening on the port) rather than merely answering slowly."""
    dead = "http://127.0.0.1:1"
    app = create_app(Settings(
        stt=Upstream("stt", dead, "indic-conformer"),
        tts=Upstream("tts", "", ""),
        llm=Upstream("llm", "", ""),
    ))
    with TestClient(app) as c:
        r = c.get("/health")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "degraded"
    stt = body["upstreams"]["stt"]
    assert stt["deployed"] is True
    assert stt["reachable"] is False
    assert "error" in stt, "the exception message should be surfaced, not swallowed"


def test_health_is_healthy_when_nothing_is_deployed():
    app = create_app(Settings(
        stt=Upstream("stt", "", ""),
        tts=Upstream("tts", "", ""),
        llm=Upstream("llm", "", ""),
    ))
    with TestClient(app) as c:
        r = c.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "healthy"
    assert r.json()["upstreams"]["stt"] == {"deployed": False}


def test_models_merges_the_catalogue_with_what_is_actually_deployed():
    """/models must mark the deployed entry as such and leave the rest alone,
    and it is deliberately distinct from /v1/models -- see its docstring."""
    app = create_app(Settings(
        stt=Upstream("stt", "http://stt:8001", "indic-conformer"),
        tts=Upstream("tts", "", ""),
        llm=Upstream("llm", "", ""),
    ))
    with TestClient(app) as c:
        r = c.get("/models")
    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "list"
    assert body["deployed"]["stt"] == "indic-conformer"
    assert body["deployed"]["tts"] is None
    entries = {e["id"]: e for e in body["data"] if e["kind"] == "stt"}
    assert entries["indic-conformer"]["deployed"] is True
    other_stt = [e for e in entries.values() if e["id"] != "indic-conformer"]
    assert other_stt and all(e["deployed"] is False for e in other_stt)
