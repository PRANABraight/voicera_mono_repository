"""Tests for the AgentConfig -> Pipecat service dispatch in apps.providers.factory."""

from __future__ import annotations

import pytest

from apps.providers.cloud.deepgram.config import DeepgramSTTConfig, DeepgramTTSConfig
from apps.providers.cloud.openai.config import OpenAILLMConfig
from apps.providers.factory import (
    AgentConfig,
    create_llm_service,
    create_stt_service,
    create_tts_service,
)


def test_create_stt_service_builds_pipecat_service():
    cfg = AgentConfig(stt_config=DeepgramSTTConfig(api_key="key"))
    service = create_stt_service(cfg)
    assert type(service).__name__ == "DeepgramSTTService"


def test_create_tts_service_builds_pipecat_service():
    cfg = AgentConfig(tts_config=DeepgramTTSConfig(api_key="key"))
    service = create_tts_service(cfg)
    assert type(service).__name__ == "DeepgramTTSService"


def test_create_llm_service_builds_pipecat_service():
    cfg = AgentConfig(llm_config=OpenAILLMConfig(api_key="key"))
    service = create_llm_service(cfg)
    assert type(service).__name__ == "OpenAILLMService"


def test_create_stt_service_requires_stt_config():
    cfg = AgentConfig()
    with pytest.raises(ValueError, match="stt_config is required"):
        create_stt_service(cfg)


def test_create_tts_service_requires_tts_config():
    cfg = AgentConfig()
    with pytest.raises(ValueError, match="tts_config is required"):
        create_tts_service(cfg)


def test_create_llm_service_requires_llm_config():
    cfg = AgentConfig()
    with pytest.raises(ValueError, match="llm_config is required"):
        create_llm_service(cfg)


def test_lazy_factory_attribute_access_on_package():
    import apps.providers as providers_pkg

    # First access triggers apps.providers.__getattr__, which lazily imports
    # .factory (pulls in loguru/pydantic union-type building) and caches the
    # result on the module for subsequent lookups.
    lazy_agent_config = providers_pkg.AgentConfig
    assert lazy_agent_config is AgentConfig
    assert providers_pkg.create_stt_service is create_stt_service


def test_lazy_factory_attribute_access_unknown_name_raises():
    import apps.providers as providers_pkg

    with pytest.raises(AttributeError, match="has no attribute 'not_a_real_symbol'"):
        providers_pkg.__getattr__("not_a_real_symbol")


def test_cloud_factory_reexports():
    from apps.providers.cloud.factory import (
        AgentConfig as CloudAgentConfig,
        create_llm_service as cloud_create_llm_service,
        create_stt_service as cloud_create_stt_service,
        create_tts_service as cloud_create_tts_service,
    )

    assert CloudAgentConfig is AgentConfig
    assert cloud_create_stt_service is create_stt_service
    assert cloud_create_tts_service is create_tts_service
    assert cloud_create_llm_service is create_llm_service
