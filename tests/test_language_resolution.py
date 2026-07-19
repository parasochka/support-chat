"""Language resolution: browser locale > AUTO (service default downstream)."""
from __future__ import annotations

import pytest

import config
import language


@pytest.fixture(autouse=True)
def _langs(monkeypatch):
    monkeypatch.setattr(config, "SUPPORTED_LANGUAGES", ["en", "es", "ru", "tr", "pt"])
    monkeypatch.setattr(config, "DEFAULT_LANGUAGE", "en")


def test_locale_mapping():
    assert language.resolve(locale="es-MX") == "es"
    assert language.resolve(locale="pt_BR") == "pt"
    assert language.resolve(locale="tr-TR") == "tr"
    assert language.resolve(locale="ru") == "ru"


def test_unsupported_locale_falls_to_auto():
    assert language.resolve(locale="de-DE") == language.AUTO


def test_nothing_resolves_to_auto():
    assert language.resolve() == language.AUTO


def test_language_name_for_concrete_and_auto():
    assert language.language_name("es") == "Spanish"
    # AUTO -> service default (DEFAULT_LANGUAGE = en in this fixture).
    assert language.language_name(language.AUTO) == "English"


def test_locale_to_lang_helper():
    assert language.locale_to_lang("en-GB") == "en"
    assert language.locale_to_lang("zz-ZZ") is None
    assert language.locale_to_lang(None) is None
