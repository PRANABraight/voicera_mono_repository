"""Tests for apps.providers.registry discovery/registration internals."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from apps.providers.base import Kind
from apps.providers.registry import (
    api_key,
    get_creator,
    llm_settings,
)


def test_get_creator_known_provider():
    creator = get_creator(Kind.STT, "deepgram")
    assert callable(creator)


def test_get_creator_unknown_provider():
    with pytest.raises(ValueError, match="Unsupported stt provider"):
        get_creator(Kind.STT, "not-a-real-provider")


def test_api_key_none():
    assert api_key(None) is None


def test_api_key_plain_string():
    assert api_key("secret") == "secret"


def test_api_key_rotation_list():
    assert api_key(["first", "second"]) == "first"


def test_api_key_empty_list_raises():
    with pytest.raises(ValueError, match="api_key list is empty"):
        api_key([])


def test_llm_settings_minimal():
    class Cfg:
        model = "gpt-4"
        temperature = None
        max_tokens = None

    assert llm_settings(Cfg()) == {"model": "gpt-4"}


def test_llm_settings_with_temperature_and_max_tokens():
    class Cfg:
        model = "gpt-4"
        temperature = 0.5
        max_tokens = 256

    assert llm_settings(Cfg()) == {
        "model": "gpt-4",
        "temperature": 0.5,
        "max_tokens": 256,
    }


def test_provider_id_error_branches():
    from apps.providers.registry import _provider_id

    class NoProviderField(BaseModel):
        name: str = "x"

    with pytest.raises(ValueError, match="has no 'provider' field"):
        _provider_id(NoProviderField)

    class NoDefaultProvider(BaseModel):
        provider: str

    with pytest.raises(ValueError, match="has no default discriminator value"):
        _provider_id(NoDefaultProvider)


def test_config_cls_from_creator_error_branches():
    from apps.providers.registry import _config_cls_from_creator

    def no_params() -> None:
        ...

    with pytest.raises(TypeError, match="must annotate its config parameter"):
        _config_cls_from_creator(no_params)

    def wrong_type(cfg: str) -> None:
        ...

    with pytest.raises(TypeError, match="must be a"):
        _config_cls_from_creator(wrong_type)


class _DupSTTCfg(BaseModel):
    provider: str = "dup-stt-provider"


class _DupSTTCfgB(BaseModel):
    provider: str = "dup-stt-provider"


def test_register_duplicate_provider_raises():
    from apps.providers.registry import STT_CONFIGS, STT_CREATORS, register_stt

    def creator_one(cfg: _DupSTTCfg):
        return cfg

    def creator_two(cfg: _DupSTTCfgB):
        return cfg

    try:
        register_stt(creator_one)
        with pytest.raises(ValueError, match="Duplicate stt provider"):
            register_stt(creator_two)
    finally:
        STT_CONFIGS.pop("dup-stt-provider", None)
        STT_CREATORS.pop("dup-stt-provider", None)


def test_register_same_config_class_twice_is_idempotent():
    from apps.providers.registry import STT_CONFIGS, STT_CREATORS, register_stt

    def creator(cfg: _DupSTTCfg):
        return cfg

    try:
        register_stt(creator)
        register_stt(creator)  # same fn+cfg re-registered: no raise.
        assert STT_CONFIGS["dup-stt-provider"] is _DupSTTCfg
    finally:
        STT_CONFIGS.pop("dup-stt-provider", None)
        STT_CREATORS.pop("dup-stt-provider", None)


def test_load_providers_skips_missing_vendor_root(monkeypatch):
    from apps.providers import registry as registry_mod

    monkeypatch.setattr(registry_mod, "_LOADED", False)
    monkeypatch.setattr(registry_mod, "_LOADING", False)

    def fake_import_module(name):
        raise ModuleNotFoundError(name, name=name)

    monkeypatch.setattr(registry_mod.importlib, "import_module", fake_import_module)
    registry_mod.load_providers()
    assert registry_mod._LOADED is True


def test_load_providers_skips_root_without_path(monkeypatch):
    from apps.providers import registry as registry_mod

    monkeypatch.setattr(registry_mod, "_LOADED", False)
    monkeypatch.setattr(registry_mod, "_LOADING", False)

    class FakeRoot:
        __path__ = None

    monkeypatch.setattr(
        registry_mod.importlib, "import_module", lambda name: FakeRoot()
    )
    registry_mod.load_providers()
    assert registry_mod._LOADED is True


def test_load_providers_skips_non_package_modules(monkeypatch):
    from apps.providers import registry as registry_mod

    monkeypatch.setattr(registry_mod, "_LOADED", False)
    monkeypatch.setattr(registry_mod, "_LOADING", False)

    class NonPkgMod:
        name = "not_a_package"
        ispkg = False

    class FakeRoot:
        __path__ = ["/fake/path"]

    monkeypatch.setattr(
        registry_mod.importlib, "import_module", lambda name: FakeRoot()
    )
    monkeypatch.setattr(
        registry_mod.pkgutil, "iter_modules", lambda paths: [NonPkgMod()]
    )
    registry_mod.load_providers()
    assert registry_mod._LOADED is True


def test_load_providers_swallows_matching_service_import_error(monkeypatch):
    from apps.providers import registry as registry_mod

    monkeypatch.setattr(registry_mod, "_LOADED", False)
    monkeypatch.setattr(registry_mod, "_LOADING", False)

    class FakeMod:
        name = "vendorx"
        ispkg = True

    class FakeRoot:
        __path__ = ["/fake/path"]

    def fake_import_module(name):
        if name.endswith((".cloud", ".adapters", ".local")):
            return FakeRoot()
        raise ModuleNotFoundError(name, name=name)

    monkeypatch.setattr(registry_mod.importlib, "import_module", fake_import_module)
    monkeypatch.setattr(
        registry_mod.pkgutil, "iter_modules", lambda paths: [FakeMod()]
    )
    registry_mod.load_providers()
    assert registry_mod._LOADED is True


def test_load_providers_reraises_unrelated_import_error(monkeypatch):
    from apps.providers import registry as registry_mod

    monkeypatch.setattr(registry_mod, "_LOADED", False)
    monkeypatch.setattr(registry_mod, "_LOADING", False)

    class FakeMod:
        name = "vendorx"
        ispkg = True

    class FakeRoot:
        __path__ = ["/fake/path"]

    def fake_import_module(name):
        if name.endswith((".cloud", ".adapters", ".local")):
            return FakeRoot()
        raise ModuleNotFoundError("some.other.module")

    monkeypatch.setattr(registry_mod.importlib, "import_module", fake_import_module)
    monkeypatch.setattr(
        registry_mod.pkgutil, "iter_modules", lambda paths: [FakeMod()]
    )
    with pytest.raises(ModuleNotFoundError):
        registry_mod.load_providers()
