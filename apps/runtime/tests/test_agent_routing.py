"""Unit tests for agent category / telephony provider routing helpers."""

from __future__ import annotations

import pytest

from apps.runtime.services.agent_routing import (
    AgentRoutingError,
    agent_category,
    telephony_provider,
)


def test_agent_category_defaults_to_websocket() -> None:
    assert agent_category({}) == "websocket"


def test_agent_category_normalizes_case() -> None:
    assert agent_category({"agent_category": "Telephony"}) == "telephony"
    assert agent_category({"agent_category": "WEBSOCKET"}) == "websocket"


def test_agent_category_invalid_raises() -> None:
    with pytest.raises(AgentRoutingError, match="Unsupported agent_category"):
        agent_category({"agent_category": "sms"})


def test_telephony_provider_rejects_non_telephony_agent() -> None:
    with pytest.raises(AgentRoutingError, match="not a telephony agent"):
        telephony_provider({"agent_category": "websocket"})


def test_telephony_provider_missing_provider_raises() -> None:
    with pytest.raises(AgentRoutingError, match="no telephony provider"):
        telephony_provider({"agent_category": "telephony", "telephony": {}})


def test_telephony_provider_missing_telephony_key_raises() -> None:
    with pytest.raises(AgentRoutingError, match="no telephony provider"):
        telephony_provider({"agent_category": "telephony"})


def test_telephony_provider_lower_cases_result() -> None:
    agent = {"agent_category": "telephony", "telephony": {"provider": "PLIVO"}}
    assert telephony_provider(agent) == "plivo"
