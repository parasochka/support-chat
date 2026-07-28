"""Language catalogue: ISO-validated supported set + custom names merge."""
from __future__ import annotations

import pytest

from app.i18n import language
from app.core import settings


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


def test_language_name_prefers_the_codes_own_name(monkeypatch):
    """A supported language with no custom name must not borrow the DEFAULT
    language's name.

    `validate_setting` accepts any ISO code without requiring a name, and the
    admin SPA stores only non-empty ones, so "supported but unnamed" is the
    ordinary state of a freshly added language — `selectable_languages` above
    already relies on the ISO fall-through. `language_name` nested that lookup
    INSIDE the default's `.get()` default, so the default won first: with
    default 'ru' and no names, `language_name('de')` returned "Russian" and the
    Layer-3 directive told the model to answer a German session in Russian.
    """
    monkeypatch.setattr(settings, "_cache",
                        {"language": {"default": "ru",
                                      "supported": ["ru", "de"],
                                      "names": {}}})
    assert language.language_name("de") == "German"
    assert language.language_name("ru") == "Russian"
    # AUTO still means "the service default".
    assert language.language_name(language.AUTO) == "Russian"
    # An admin-set custom name still wins over the ISO table.
    monkeypatch.setattr(settings, "_cache",
                        {"language": {"default": "ru",
                                      "supported": ["ru", "de"],
                                      "names": {"de": "Deutsch"}}})
    assert language.language_name("de") == "Deutsch"
