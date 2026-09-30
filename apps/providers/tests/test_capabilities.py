"""Tests for vendor capabilities helpers."""

from __future__ import annotations

import pytest

from apps.providers.capabilities import (
    expand_settings,
    languages_map,
    model_ids,
    normalize_capabilities,
    settings_tree,
)
from apps.providers.scoped_settings import resolve_settings


def test_expand_settings_fills_all_vendor_codes():
    languages = {"hi-IN": "hi", "en-IN": "en"}
    meta = {
        "voice": {"default": "a", "options": ["a"], "input_type": "dropdown"},
    }
    expanded = expand_settings(languages, meta)
    assert set(expanded) == {"hi-IN", "en-IN"}
    assert "*" not in expanded
    assert expanded["hi-IN"]["voice"]["default"] == "a"
    expanded["en-IN"]["voice"]["default"] = "b"
    assert expanded["hi-IN"]["voice"]["default"] == "a"


def test_normalize_rejects_star_in_settings():
    with pytest.raises(ValueError, match="must not use"):
        normalize_capabilities(
            {
                "m": {
                    "languages": {"en": "en"},
                    "settings": {"*": {}},
                }
            }
        )


def test_normalize_requires_settings_for_every_language():
    with pytest.raises(ValueError, match="missing language keys"):
        normalize_capabilities(
            {
                "m": {
                    "languages": {"en": "en", "hi": "hi"},
                    "settings": {"en": {}},
                }
            }
        )


def test_settings_tree_rekeys_to_canonical():
    caps = {
        "bulbul:v2": {
            "languages": {"hi-IN": "hi", "en-IN": "en"},
            "settings": expand_settings(
                {"hi-IN": "hi", "en-IN": "en"},
                {
                    "voice": {
                        "default": "anushka",
                        "options": ["anushka"],
                        "input_type": "both",
                        "allow_custom_input": True,
                    },
                },
            ),
        }
    }
    tree = settings_tree(caps)
    assert set(tree["bulbul:v2"]) == {"hi", "en"}
    assert resolve_settings(tree, "bulbul:v2", "hi")["voice"]["default"] == "anushka"
    assert model_ids(caps) == ("bulbul:v2",)
    assert languages_map(caps)["bulbul:v2"]["hi-IN"] == "hi"


def test_normalize_rejects_non_mapping_capabilities():
    with pytest.raises(TypeError, match="must be a mapping"):
        normalize_capabilities(["not", "a", "mapping"])  # type: ignore[arg-type]


def test_normalize_rejects_invalid_model_key():
    with pytest.raises(ValueError, match="model key must be a non-empty str"):
        normalize_capabilities({"": {"languages": {"en": "en"}, "settings": {"en": {}}}})


def test_normalize_rejects_non_mapping_entry():
    with pytest.raises(TypeError, match=r"capabilities\['m'\] must be a mapping"):
        normalize_capabilities({"m": "not-a-mapping"})  # type: ignore[dict-item]


def test_normalize_requires_languages_and_settings_keys():
    with pytest.raises(ValueError, match="must have 'languages' and 'settings'"):
        normalize_capabilities({"m": {"languages": {"en": "en"}}})


def test_normalize_rejects_empty_languages_map():
    with pytest.raises(ValueError, match="languages must be a non-empty mapping"):
        normalize_capabilities({"m": {"languages": {}, "settings": {}}})


def test_normalize_rejects_non_mapping_settings():
    with pytest.raises(TypeError, match=r"settings must be a mapping"):
        normalize_capabilities(
            {"m": {"languages": {"en": "en"}, "settings": "not-a-mapping"}}
        )


def test_normalize_rejects_invalid_vendor_code():
    with pytest.raises(ValueError, match="must be a non-empty str"):
        normalize_capabilities(
            {"m": {"languages": {"": "en"}, "settings": {}}}
        )


def test_normalize_rejects_star_in_languages():
    with pytest.raises(ValueError, match="languages must not use"):
        normalize_capabilities(
            {"m": {"languages": {"*": "en"}, "settings": {}}}
        )


def test_normalize_rejects_invalid_canonical_id():
    with pytest.raises(ValueError, match="canonical id for"):
        normalize_capabilities(
            {"m": {"languages": {"en": ""}, "settings": {}}}
        )


def test_normalize_rejects_settings_key_not_in_languages():
    with pytest.raises(ValueError, match="is not in languages"):
        normalize_capabilities(
            {"m": {"languages": {"en": "en"}, "settings": {"hi": {}}}}
        )


def test_normalize_rejects_non_mapping_settings_meta():
    with pytest.raises(TypeError, match="must be a mapping"):
        normalize_capabilities(
            {"m": {"languages": {"en": "en"}, "settings": {"en": "not-a-mapping"}}}
        )


def test_api_capabilities_uses_canonical_language_keys():
    from apps.providers.capabilities import api_capabilities

    caps = {
        "m": {
            "languages": {"hi-IN": "hi", "en-IN": "en"},
            "settings": expand_settings(
                {"hi-IN": "hi", "en-IN": "en"},
                {"voice": {"default": "a", "options": ["a"], "input_type": "dropdown"}},
            ),
        }
    }
    dumped = api_capabilities(caps)
    assert set(dumped["m"]["languages"]) == {"hi", "en"}
    assert dumped["m"]["languages"]["hi"] == "hi-IN"
    assert dumped["m"]["languages"]["en"] == "en-IN"
    assert set(dumped["m"]["settings"]) == {"hi", "en"}
    assert dumped["m"]["settings"]["hi"]["voice"]["default"] == "a"
