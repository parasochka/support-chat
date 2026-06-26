"""Language catalogue: ISO-validated supported set + custom names merge."""
from __future__ import annotations

import pytest

import language
import settings


@pytest.fixture(autouse=True)
def _clean():
    settings.invalidate()
    yield
    settings.invalidate()


def test_validate_rejects_non_iso_supported_code():
    with pytest.raises(ValueError):
        settings.validate_setting("language", {"default": "en", "supported": ["en", "zz"]})


def test_validate_accepts_iso_codes():
    out = settings.validate_setting(
        "language", {"default": "de", "supported": ["en", "de", "fr"]})
    assert out["supported"] == ["en", "de", "fr"]


def test_validate_rejects_bad_custom_name_code():
    with pytest.raises(ValueError):
        settings.validate_setting("language", {
            "supported": ["en"], "names": {"zzz": "Nonsense"}})


def test_custom_names_merge_over_builtins():
    # A language added via the panel (here Klingon-style: use a real ISO code 'eu')
    settings._cache["language"] = {"default": "en", "supported": ["en", "eu"],
                                   "names": {"eu": "Euskara"}}
    names = language.all_language_names()
    assert names["eu"] == "Euskara"     # custom override
    assert names["en"] == "English"     # built-in preserved
    assert language.language_name("eu") == "Euskara"


def test_selectable_includes_supported_and_builtins():
    settings._cache["language"] = {"default": "en", "supported": ["en", "ja"],
                                   "names": {}}
    codes = {l["code"] for l in language.selectable_languages()}
    assert "ja" in codes      # supported but not built-in
    assert "ru" in codes      # built-in
    # ISO name falls through for a supported-but-unnamed language
    ja = next(l for l in language.selectable_languages() if l["code"] == "ja")
    assert ja["name"] == "Japanese"
