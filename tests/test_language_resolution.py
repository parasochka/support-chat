"""Language resolution precedence: param > locale > session/auto > default."""
from __future__ import annotations

import pytest

import config
import language


@pytest.fixture(autouse=True)
def _langs(monkeypatch):
    monkeypatch.setattr(config, "SUPPORTED_LANGUAGES", ["en", "es", "ru", "tr", "pt"])
    monkeypatch.setattr(config, "DEFAULT_LANGUAGE", "en")


def test_explicit_param_wins():
    assert language.resolve(lang="es", locale="ru-RU") == "es"


def test_unsupported_param_falls_through_to_locale():
    assert language.resolve(lang="de", locale="pt-BR") == "pt"


def test_locale_mapping():
    assert language.resolve(locale="es-MX") == "es"
    assert language.resolve(locale="pt_BR") == "pt"
    assert language.resolve(locale="tr-TR") == "tr"


def test_unsupported_locale_falls_to_auto():
    # no session lang, unsupported everything -> AUTO (model auto-detect)
    assert language.resolve(lang=None, locale="de-DE") == language.AUTO


def test_session_lang_used_before_auto():
    assert language.resolve(lang=None, locale=None, session_lang="ru") == "ru"


def test_nothing_resolves_to_auto():
    assert language.resolve() == language.AUTO


def test_fallback_name_for_concrete_and_auto():
    # The fallback language is only used when the player's own language is
    # unclear; the model otherwise mirrors the player's message.
    assert language.fallback_language_name("es") == "Spanish"
    # AUTO -> service default (DEFAULT_LANGUAGE = en in this fixture).
    assert language.fallback_language_name(language.AUTO) == "English"


def test_locale_to_lang_helper():
    assert language.locale_to_lang("en-GB") == "en"
    assert language.locale_to_lang("zz-ZZ") is None
    assert language.locale_to_lang(None) is None
