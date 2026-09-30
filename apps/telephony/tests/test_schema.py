"""Tests for telephony provider schema catalog."""

from __future__ import annotations

from enum import Enum
from typing import Literal, Optional, Union

import pytest
from pydantic import BaseModel, Field

from apps.telephony import (
    Kind,
    PlivoClient,
    VobizClient,
    all_provider_schemas,
    configuration_telephony,
    create_client,
    provider_schemas,
)
from apps.telephony.providers.plivo.config import PlivoConfig
from apps.telephony.providers.vobiz.config import VobizConfig
from apps.telephony.schema import DEFAULT_SERVICE_PROVIDERS


def test_provider_schemas_keys():
    schemas = provider_schemas(Kind.TELEPHONY)
    assert set(schemas) == {"vobiz", "plivo"}


def test_all_provider_schemas_shape():
    schemas = all_provider_schemas()
    assert set(schemas) == {"telephony"}
    assert "vobiz" in schemas["telephony"]
    assert "plivo" in schemas["telephony"]


def test_vobiz_secrets_and_integration_models():
    schema = provider_schemas()["vobiz"]
    assert schema["provider"] == "vobiz"
    assert schema["name"] == "Vobiz"
    assert set(schema["secrets"]) == {"auth_id", "auth_token"}
    fields = schema["fields"]
    assert fields["auth_id"]["secret"] is True
    assert fields["auth_id"]["integration_model"] == "VobizAuthId"
    assert fields["auth_token"]["integration_model"] == "VobizAuthToken"
    assert "input_mode" not in fields["auth_id"]
    assert fields["base_url"]["default"] == "https://api.vobiz.ai/api/v1"
    assert fields["base_url"]["input_mode"] == "both"
    assert "kind" not in fields
    assert "provider" not in fields
    assert "name" not in fields


def test_plivo_secrets_and_integration_models():
    schema = provider_schemas()["plivo"]
    assert schema["name"] == "Plivo"
    assert set(schema["secrets"]) == {"auth_id", "auth_token"}
    fields = schema["fields"]
    assert fields["auth_id"]["integration_model"] == "PlivoAuthId"
    assert fields["auth_token"]["integration_model"] == "PlivoAuthToken"
    assert fields["base_url"]["default"] == "https://api.plivo.com/v1"


def test_catalog_omits_schema_noise():
    for provider, schema in provider_schemas().items():
        assert "$defs" not in schema, provider
        assert "$ref" not in schema, provider
        assert "properties" not in schema, provider
        assert "fields" in schema, provider
        assert "secrets" in schema, provider
        blob = str(schema)
        assert "allow_custom_input" not in blob, provider


def test_configuration_telephony_envelope():
    defaults = configuration_telephony()
    assert set(defaults["telephony"]) == {"vobiz", "plivo"}
    assert defaults["default_providers"] == DEFAULT_SERVICE_PROVIDERS
    assert DEFAULT_SERVICE_PROVIDERS["telephony"] == "vobiz"


def test_unsupported_kind():
    with pytest.raises(ValueError, match="Unsupported kind"):
        provider_schemas("stt")  # type: ignore[arg-type]


def test_create_client_from_config():
    vobiz = create_client(
        VobizConfig(auth_id="id", auth_token="tok")
    )
    assert isinstance(vobiz, VobizClient)
    assert vobiz.base_url == "https://api.vobiz.ai/api/v1"

    plivo = create_client(
        PlivoConfig(
            auth_id="id",
            auth_token="tok",
            base_url="https://api.plivo.com/v1/",
        )
    )
    assert isinstance(plivo, PlivoClient)
    assert plivo.base_url == "https://api.plivo.com/v1"


def test_list_providers_summary():
    from apps.telephony.schema import list_providers

    listed = list_providers()
    assert set(listed) == {"vobiz", "plivo"}
    assert listed["vobiz"] == {"provider": "vobiz", "name": "Vobiz"}
    assert "secrets" not in listed["vobiz"]
    assert "fields" not in listed["vobiz"]


def test_telephony_settings_and_auth_split():
    from apps.telephony.schema import UnknownProviderError, provider_auth, provider_settings

    settings = provider_settings("vobiz")
    assert set(settings["fields"]) == {"base_url"}
    assert "auth_id" not in settings["fields"]
    assert "secrets" not in settings

    auth = provider_auth("vobiz")
    assert set(auth["secrets"]) == {"auth_id", "auth_token"}
    assert set(auth["fields"]) == {"auth_id", "auth_token"}
    assert auth["fields"]["auth_id"]["integration_model"] == "VobizAuthId"

    with pytest.raises(UnknownProviderError):
        provider_auth("twilio")


def test_all_provider_auth_shape():
    from apps.telephony.schema import all_provider_auth

    auth = all_provider_auth()
    assert set(auth) == {"telephony"}
    assert set(auth["telephony"]) == {"vobiz", "plivo"}


def test_provider_level_auth_known_and_unknown():
    from apps.telephony.schema import provider_level_auth

    known = provider_level_auth("vobiz")
    assert known is not None
    assert known["kinds"] == ["telephony"]
    assert known["provider"] == "vobiz"

    assert provider_level_auth("twilio") is None


def test_all_provider_level_auth_shape():
    from apps.telephony.schema import all_provider_level_auth

    everyone = all_provider_level_auth()
    assert set(everyone) == {"vobiz", "plivo"}
    assert everyone["vobiz"]["kinds"] == ["telephony"]


def test_configuration_telephony_unregistered_default_provider(monkeypatch):
    from apps.telephony import schema as schema_mod

    monkeypatch.setitem(schema_mod.DEFAULT_SERVICE_PROVIDERS, "telephony", "twilio")
    with pytest.raises(ValueError, match="is not a registered provider"):
        schema_mod.configuration_telephony()


# --- Private helper edge cases exercised directly (mirrors apps.providers style) ---


def test_provider_id_missing_field_raises():
    from apps.telephony.schema import _provider_id

    class NoProviderField(BaseModel):
        name: str = "x"

    with pytest.raises(ValueError, match="has no 'provider' field"):
        _provider_id(NoProviderField)


def test_provider_id_no_default_raises():
    from apps.telephony.schema import _provider_id

    class NoDefaultProvider(BaseModel):
        provider: str

    with pytest.raises(ValueError, match="has no default discriminator value"):
        _provider_id(NoDefaultProvider)


def test_display_name_missing_field_raises():
    from apps.telephony.schema import _display_name

    class NoNameField(BaseModel):
        provider: str = "x"

    with pytest.raises(ValueError, match="has no 'name' field"):
        _display_name(NoNameField)


def test_display_name_no_default_raises():
    from apps.telephony.schema import _display_name

    class NoDefaultName(BaseModel):
        name: str

    with pytest.raises(ValueError, match="has no default display value"):
        _display_name(NoDefaultName)


def test_type_label_edge_cases():
    from apps.telephony.schema import _type_label

    assert _type_label(None) == "any"
    assert _type_label(Literal["a", "b"]) == "string"
    assert _type_label(Union[str, int]) == "string | integer"
    assert _type_label(list[int]) == "list[integer]"
    assert _type_label(dict[str, int]) == "dict"
    assert _type_label(type(None)) == "null"

    class Weird:
        pass

    instance = Weird()
    assert _type_label(instance) == str(instance)


def test_field_catalog_no_examples_input_mode():
    from apps.telephony.schema import _config_catalog

    class PlainEnum(Enum):
        FOO = "foo"

    class Plain(BaseModel):
        model_config = {"arbitrary_types_allowed": True}

        provider: str = "plain"
        name: str = "Plain"
        kind_field: PlainEnum = Field(default=PlainEnum.FOO)
        plain_field: str = Field(default="value")
        optional_field: Optional[str] = Field(default=None)

    catalog = _config_catalog(Plain)
    assert catalog["fields"]["plain_field"]["input_mode"] == "input"
    # Enum default is unwrapped via its `.value` attribute.
    assert catalog["fields"]["kind_field"]["default"] == "foo"
    # None default on a non-required field is preserved explicitly.
    assert catalog["fields"]["optional_field"]["default"] is None


def test_field_catalog_examples_without_allow_custom_is_options():
    from apps.telephony.schema import _config_catalog

    class WithExamples(BaseModel):
        provider: str = "with_examples"
        name: str = "WithExamples"
        choice: str = Field(
            default="a", json_schema_extra={"examples": ["a", "b"]}
        )

    catalog = _config_catalog(WithExamples)
    assert catalog["fields"]["choice"]["input_mode"] == "options"
