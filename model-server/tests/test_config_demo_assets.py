"""Edge cases in config.py and catalogue.py that the existing suites don't
exercise directly: the comma-separated demo-asset prefix parser, and the
catalogue path resolver's last-resort fallback when neither copy exists.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gateway"))

from app import catalogue  # noqa: E402
from app import config  # noqa: E402


def test_demo_assets_parses_comma_separated_prefixes(monkeypatch):
    monkeypatch.setenv("STT_DEMO_ASSETS", "static, /assets/ ,")
    assert config._demo_assets("stt") == ("/static", "/assets")


def test_demo_assets_defaults_to_empty(monkeypatch):
    monkeypatch.delenv("STT_DEMO_ASSETS", raising=False)
    assert config._demo_assets("stt") == ()


def test_find_catalogue_falls_back_to_the_image_location_when_neither_exists(monkeypatch):
    """Neither the image copy nor the checkout copy exists -- e.g. a broken
    install. The resolver must still return a path rather than raise."""
    monkeypatch.setattr(Path, "is_file", lambda self: False)
    result = catalogue._find_catalogue()
    assert result == Path(catalogue.__file__).resolve().parent / "models.yaml"
