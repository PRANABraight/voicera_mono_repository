"""Tests for telephony registry creator maps."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from apps.telephony.registry import (
    ANSWER_XML_BUILDERS,
    CLIENT_CREATORS,
    FRAME_SERIALIZER_FACTORIES,
    TELEPHONY_CONFIGS,
    build_config,
    create_client,
    get_answer_xml_builder,
    get_client_creator,
    get_frame_serializer_factory,
    load_frame_serializers,
    load_providers,
    registered_providers,
)
from apps.telephony.providers.plivo.config import PlivoConfig
from apps.telephony.providers.vobiz.config import VobizConfig
from apps.telephony.providers.vobiz import VobizClient
from apps.telephony.providers.plivo import PlivoClient


class _DupCfg(BaseModel):
    provider: str = "dup-client"
    name: str = "Dup"


@pytest.fixture(autouse=True)
def _ensure_providers_loaded() -> None:
    load_providers()


def test_registered_providers_include_vobiz_and_plivo() -> None:
    assert registered_providers() == frozenset({"vobiz", "plivo"})


@pytest.mark.parametrize("provider", ["vobiz", "plivo"])
def test_each_provider_has_config_client_and_xml(provider: str) -> None:
    assert provider in TELEPHONY_CONFIGS
    assert provider in CLIENT_CREATORS
    assert provider in ANSWER_XML_BUILDERS


@pytest.mark.parametrize("provider", ["vobiz", "plivo"])
def test_each_provider_has_frame_serializer_after_lazy_load(provider: str) -> None:
    load_frame_serializers()
    assert provider in FRAME_SERIALIZER_FACTORIES


def test_get_answer_xml_builder_known_provider() -> None:
    builder = get_answer_xml_builder("vobiz")
    assert callable(builder)


def test_get_frame_serializer_factory_known_provider() -> None:
    factory = get_frame_serializer_factory("vobiz")
    assert callable(factory)


def test_build_config_known_provider() -> None:
    cfg = build_config("vobiz", auth_id="id", auth_token="tok")
    assert cfg.provider == "vobiz"


def test_get_client_creator_unknown_provider() -> None:
    with pytest.raises(ValueError, match="Unsupported telephony provider"):
        get_client_creator("twilio")


def test_get_answer_xml_builder_unknown_provider() -> None:
    with pytest.raises(ValueError, match="Unsupported telephony provider for XML"):
        get_answer_xml_builder("twilio")


def test_get_frame_serializer_factory_unknown_provider() -> None:
    with pytest.raises(ValueError, match="Unsupported telephony provider for serializer"):
        get_frame_serializer_factory("twilio")


def test_create_client_from_registered_creators() -> None:
    vobiz = create_client(VobizConfig(auth_id="id", auth_token="tok"))
    assert isinstance(vobiz, VobizClient)

    plivo = create_client(
        PlivoConfig(
            auth_id="id",
            auth_token="tok",
            base_url="https://api.plivo.com/v1/",
        )
    )
    assert isinstance(plivo, PlivoClient)


def test_build_config_rejects_empty_provider() -> None:
    with pytest.raises(ValueError, match="provider id is required"):
        build_config("")


def test_build_config_unsupported_provider() -> None:
    with pytest.raises(ValueError, match="Unsupported telephony provider"):
        build_config("twilio")


def test_create_client_missing_provider() -> None:
    class FakeConfig:
        provider = None

    with pytest.raises(ValueError, match="missing provider"):
        create_client(FakeConfig())


def test_config_classes_rejects_wrong_kind() -> None:
    from apps.telephony.registry import config_classes

    with pytest.raises(ValueError, match="Unsupported kind"):
        config_classes("stt")  # type: ignore[arg-type]


def test_config_classes_valid_kind_returns_list() -> None:
    from apps.telephony.base import Kind
    from apps.telephony.registry import config_classes

    classes = config_classes(Kind.TELEPHONY)
    assert len(classes) >= 2


def test_provider_id_error_branches() -> None:
    from pydantic import BaseModel

    from apps.telephony.registry import _provider_id

    class NoProviderField(BaseModel):
        name: str = "x"

    with pytest.raises(ValueError, match="has no 'provider' field"):
        _provider_id(NoProviderField)

    class NoDefaultProvider(BaseModel):
        provider: str

    with pytest.raises(ValueError, match="has no default discriminator value"):
        _provider_id(NoDefaultProvider)


def test_config_cls_from_creator_error_branches() -> None:
    from apps.telephony.registry import _config_cls_from_creator

    def no_params() -> None:
        ...

    with pytest.raises(TypeError, match="must annotate its config parameter"):
        _config_cls_from_creator(no_params)

    def wrong_type(cfg: str) -> None:
        ...

    with pytest.raises(TypeError, match="must be a"):
        _config_cls_from_creator(wrong_type)


def test_register_telephony_duplicate_provider_raises() -> None:
    from pydantic import BaseModel

    from apps.telephony.registry import TELEPHONY_CONFIGS, register_telephony

    class DupA(BaseModel):
        provider: str = "dup-provider"
        name: str = "A"

    class DupB(BaseModel):
        provider: str = "dup-provider"
        name: str = "B"

    try:
        register_telephony(DupA)
        with pytest.raises(ValueError, match="Duplicate telephony provider id"):
            register_telephony(DupB)
    finally:
        TELEPHONY_CONFIGS.pop("dup-provider", None)


def test_register_client_duplicate_creator_raises() -> None:
    from apps.telephony.registry import CLIENT_CREATORS, register_client

    def creator_one(cfg: _DupCfg):
        return cfg

    def creator_two(cfg: _DupCfg):
        return cfg

    try:
        register_client(creator_one)
        with pytest.raises(ValueError, match="Duplicate telephony client creator"):
            register_client(creator_two)
    finally:
        CLIENT_CREATORS.pop("dup-client", None)


def test_register_answer_xml_duplicate_raises() -> None:
    from apps.telephony.registry import ANSWER_XML_BUILDERS, register_answer_xml

    @register_answer_xml("dup-xml")
    def builder_one(*args, **kwargs) -> str:
        return "one"

    try:
        with pytest.raises(ValueError, match="Duplicate telephony answer XML"):

            @register_answer_xml("dup-xml")
            def builder_two(*args, **kwargs) -> str:
                return "two"

    finally:
        ANSWER_XML_BUILDERS.pop("dup-xml", None)


def test_load_providers_handles_missing_root_module(monkeypatch) -> None:
    from apps.telephony import registry as registry_mod

    monkeypatch.setattr(registry_mod, "_LOADED", False)
    monkeypatch.setattr(registry_mod, "_LOADING", False)

    def fake_import_module(name):
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(registry_mod.importlib, "import_module", fake_import_module)
    registry_mod.load_providers()
    assert registry_mod._LOADED is False


def test_load_providers_handles_missing_root_path(monkeypatch) -> None:
    from apps.telephony import registry as registry_mod

    monkeypatch.setattr(registry_mod, "_LOADED", False)
    monkeypatch.setattr(registry_mod, "_LOADING", False)

    class FakeRoot:
        __path__ = None

    monkeypatch.setattr(
        registry_mod.importlib, "import_module", lambda name: FakeRoot()
    )
    registry_mod.load_providers()
    assert registry_mod._LOADED is False


def test_load_providers_reraises_unrelated_import_error(monkeypatch) -> None:
    from apps.telephony import registry as registry_mod

    monkeypatch.setattr(registry_mod, "_LOADED", False)
    monkeypatch.setattr(registry_mod, "_LOADING", False)

    class FakeMod:
        name = "vobiz"
        ispkg = True

    class FakeRoot:
        __path__ = ["/fake/path"]

    def fake_import_module(name):
        if name.endswith(".providers"):
            return FakeRoot()
        raise ModuleNotFoundError("apps.telephony.providers.vobiz.some_other_dep")

    monkeypatch.setattr(registry_mod.importlib, "import_module", fake_import_module)
    monkeypatch.setattr(
        registry_mod.pkgutil, "iter_modules", lambda paths: [FakeMod()]
    )
    with pytest.raises(ModuleNotFoundError):
        registry_mod.load_providers()


def test_load_frame_serializers_handles_missing_root_module(monkeypatch) -> None:
    from apps.telephony import registry as registry_mod

    monkeypatch.setattr(registry_mod, "_LOADED", True)
    monkeypatch.setattr(registry_mod, "_LOADING", False)
    monkeypatch.setattr(registry_mod, "_SERIALIZERS_LOADED", False)
    monkeypatch.setattr(registry_mod, "_SERIALIZERS_LOADING", False)

    def fake_import_module(name):
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(registry_mod.importlib, "import_module", fake_import_module)
    registry_mod.load_frame_serializers()
    assert registry_mod._SERIALIZERS_LOADED is False


def test_load_frame_serializers_handles_missing_root_path(monkeypatch) -> None:
    from apps.telephony import registry as registry_mod

    monkeypatch.setattr(registry_mod, "_LOADED", True)
    monkeypatch.setattr(registry_mod, "_LOADING", False)
    monkeypatch.setattr(registry_mod, "_SERIALIZERS_LOADED", False)
    monkeypatch.setattr(registry_mod, "_SERIALIZERS_LOADING", False)

    class FakeRoot:
        __path__ = None

    monkeypatch.setattr(
        registry_mod.importlib, "import_module", lambda name: FakeRoot()
    )
    registry_mod.load_frame_serializers()
    assert registry_mod._SERIALIZERS_LOADED is False


def test_load_providers_skips_non_package_modules(monkeypatch) -> None:
    from apps.telephony import registry as registry_mod

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


def test_load_providers_swallows_matching_submodule_import_error(monkeypatch) -> None:
    from apps.telephony import registry as registry_mod

    monkeypatch.setattr(registry_mod, "_LOADED", False)
    monkeypatch.setattr(registry_mod, "_LOADING", False)

    class FakeMod:
        name = "vobiz"
        ispkg = True

    class FakeRoot:
        __path__ = ["/fake/path"]

    def fake_import_module(name):
        if name.endswith(".providers"):
            return FakeRoot()
        # exc.name matches the submodule exactly -> should be swallowed.
        raise ModuleNotFoundError(name, name=name)

    monkeypatch.setattr(registry_mod.importlib, "import_module", fake_import_module)
    monkeypatch.setattr(
        registry_mod.pkgutil, "iter_modules", lambda paths: [FakeMod()]
    )
    registry_mod.load_providers()
    assert registry_mod._LOADED is True


def test_load_frame_serializers_skips_non_package_modules(monkeypatch) -> None:
    from apps.telephony import registry as registry_mod

    monkeypatch.setattr(registry_mod, "_LOADED", True)
    monkeypatch.setattr(registry_mod, "_LOADING", False)
    monkeypatch.setattr(registry_mod, "_SERIALIZERS_LOADED", False)
    monkeypatch.setattr(registry_mod, "_SERIALIZERS_LOADING", False)

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
    registry_mod.load_frame_serializers()
    assert registry_mod._SERIALIZERS_LOADED is True


def test_load_frame_serializers_swallows_matching_submodule_import_error(
    monkeypatch,
) -> None:
    from apps.telephony import registry as registry_mod

    monkeypatch.setattr(registry_mod, "_LOADED", True)
    monkeypatch.setattr(registry_mod, "_LOADING", False)
    monkeypatch.setattr(registry_mod, "_SERIALIZERS_LOADED", False)
    monkeypatch.setattr(registry_mod, "_SERIALIZERS_LOADING", False)

    class FakeMod:
        name = "vobiz"
        ispkg = True

    class FakeRoot:
        __path__ = ["/fake/path"]

    def fake_import_module(name):
        if name.endswith(".providers"):
            return FakeRoot()
        raise ModuleNotFoundError(name, name=name)

    monkeypatch.setattr(registry_mod.importlib, "import_module", fake_import_module)
    monkeypatch.setattr(
        registry_mod.pkgutil, "iter_modules", lambda paths: [FakeMod()]
    )
    registry_mod.load_frame_serializers()
    assert registry_mod._SERIALIZERS_LOADED is True


def test_load_frame_serializers_reraises_unrelated_import_error(monkeypatch) -> None:
    from apps.telephony import registry as registry_mod

    monkeypatch.setattr(registry_mod, "_LOADED", True)
    monkeypatch.setattr(registry_mod, "_LOADING", False)
    monkeypatch.setattr(registry_mod, "_SERIALIZERS_LOADED", False)
    monkeypatch.setattr(registry_mod, "_SERIALIZERS_LOADING", False)

    class FakeMod:
        name = "vobiz"
        ispkg = True

    class FakeRoot:
        __path__ = ["/fake/path"]

    def fake_import_module(name):
        if name.endswith(".providers"):
            return FakeRoot()
        raise ModuleNotFoundError("apps.telephony.providers.vobiz.some_other_dep")

    monkeypatch.setattr(registry_mod.importlib, "import_module", fake_import_module)
    monkeypatch.setattr(
        registry_mod.pkgutil, "iter_modules", lambda paths: [FakeMod()]
    )
    with pytest.raises(ModuleNotFoundError):
        registry_mod.load_frame_serializers()
