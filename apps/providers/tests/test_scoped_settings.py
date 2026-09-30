"""Tests for scoped settings trees."""

from __future__ import annotations

import pytest

from apps.providers.scoped_settings import (
    SETTINGS_BY_MODEL_LANGUAGE_KEY,
    normalize_settings_by_model_language,
    resolve_settings,
    settings_schema_extra,
)


def test_normalize_and_resolve_canonical_keys():
    tree = normalize_settings_by_model_language(
        {
            "m1": {
                "en": {
                    "voice": {
                        "default": "a",
                        "options": ["a", "b"],
                        "input_type": "dropdown",
                    },
                    "speed": {
                        "default": 1.0,
                        "minimum": 0.5,
                        "maximum": 2.0,
                        "input_type": "slider",
                    },
                },
            },
            "m2": {
                "hi": {
                    "voice": {
                        "default": "c",
                        "options": ["c"],
                        "input_type": "both",
                        "allow_custom_input": True,
                    },
                },
            },
        }
    )
    assert resolve_settings(tree, "m1", "en")["voice"]["default"] == "a"
    assert resolve_settings(tree, "m2", "hi")["voice"]["default"] == "c"
    assert resolve_settings(tree, "m2", "en") == {}
    assert resolve_settings(tree, "missing", "en") == {}


def test_normalize_rejects_unknown_leaf_key():
    with pytest.raises(ValueError, match="Unknown settings leaf key"):
        normalize_settings_by_model_language(
            {"m": {"en": {"voice": {"default": "a", "bogus": 1}}}}
        )


def test_settings_schema_extra_key():
    extra = settings_schema_extra({"m": {"en": {}}})
    assert SETTINGS_BY_MODEL_LANGUAGE_KEY in extra
    assert extra[SETTINGS_BY_MODEL_LANGUAGE_KEY] == {"m": {"en": {}}}


def test_normalize_leaf_rejects_non_mapping_meta():
    with pytest.raises(TypeError, match="setting meta must be a mapping"):
        normalize_settings_by_model_language({"m": {"en": {"voice": "not-a-mapping"}}})


def test_normalize_leaf_rejects_non_sequence_options():
    with pytest.raises(TypeError, match="options must be a sequence"):
        normalize_settings_by_model_language(
            {"m": {"en": {"voice": {"options": 123}}}}
        )


def test_normalize_leaf_rejects_string_as_options():
    with pytest.raises(TypeError, match="options must be a sequence"):
        normalize_settings_by_model_language(
            {"m": {"en": {"voice": {"options": "not-a-list"}}}}
        )


def test_normalize_leaf_rejects_bad_input_type():
    with pytest.raises(ValueError, match="input_type must be one of"):
        normalize_settings_by_model_language(
            {"m": {"en": {"voice": {"input_type": "bogus"}}}}
        )


def test_normalize_rejects_invalid_model_key():
    with pytest.raises(ValueError, match="model key must be a non-empty str"):
        normalize_settings_by_model_language({"": {"en": {}}})


def test_normalize_rejects_non_mapping_by_lang():
    with pytest.raises(TypeError, match="must be a mapping"):
        normalize_settings_by_model_language({"m": "not-a-mapping"})


def test_normalize_rejects_invalid_language_key():
    with pytest.raises(ValueError, match="language key under"):
        normalize_settings_by_model_language({"m": {"": {}}})


def test_normalize_rejects_non_mapping_settings():
    with pytest.raises(TypeError, match=r"settings for \('m', 'en'\) must be a mapping"):
        normalize_settings_by_model_language({"m": {"en": "not-a-mapping"}})


def test_normalize_rejects_invalid_setting_name():
    with pytest.raises(ValueError, match="setting name under"):
        normalize_settings_by_model_language({"m": {"en": {"": {}}}})


def test_get_settings_by_model_language_from_json_schema_extra():
    from apps.providers.scoped_settings import get_settings_by_model_language

    class Cfg:
        model_config = {
            "json_schema_extra": {
                SETTINGS_BY_MODEL_LANGUAGE_KEY: {"m": {"en": {}}},
            }
        }

    result = get_settings_by_model_language(Cfg)
    assert result == {"m": {"en": {}}}


def test_get_settings_by_model_language_from_class_attribute():
    from apps.providers.scoped_settings import get_settings_by_model_language

    class Cfg:
        settings_by_model_language = {"m": {"en": {}}}

    assert get_settings_by_model_language(Cfg) == {"m": {"en": {}}}


def test_get_settings_by_model_language_none_when_absent():
    from apps.providers.scoped_settings import get_settings_by_model_language

    class Cfg:
        pass

    assert get_settings_by_model_language(Cfg) is None


def test_get_settings_by_model_language_none_when_extra_not_mapping():
    from apps.providers.scoped_settings import get_settings_by_model_language

    class Cfg:
        model_config = {"json_schema_extra": "not-a-mapping"}

    assert get_settings_by_model_language(Cfg) is None


def test_get_settings_by_model_language_none_when_tree_not_mapping():
    from apps.providers.scoped_settings import get_settings_by_model_language

    class Cfg:
        model_config = {
            "json_schema_extra": {SETTINGS_BY_MODEL_LANGUAGE_KEY: "not-a-mapping"}
        }

    assert get_settings_by_model_language(Cfg) is None
