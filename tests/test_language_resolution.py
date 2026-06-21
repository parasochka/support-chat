"""Language resolution precedence: param > locale > profile > session/auto > default."""
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


def test_locale_beats_profile():
    # Browser locale outranks the account/profile language.
    assert language.resolve(locale="es-MX", profile_lang="ru") == "es"


def test_profile_used_when_no_locale():
    # No manual lang, no (supported) browser locale -> fall to profile language.
    assert language.resolve(lang=None, locale=None, profile_lang="ru") == "ru"
    assert language.resolve(lang=None, locale="de-DE", profile_lang="tr") == "tr"


def test_profile_accepts_locale_form():
    assert language.resolve(profile_lang="pt-BR") == "pt"


def test_profile_beats_session():
    assert language.resolve(profile_lang="es", session_lang="ru") == "es"


def test_unsupported_profile_falls_through_to_session():
    assert language.resolve(profile_lang="de", session_lang="ru") == "ru"


def test_profile_lang_from_context():
    assert language.profile_lang_from_context({"language": "ru-RU"}) == "ru-RU"
    assert language.profile_lang_from_context({"lang": "es"}) == "es"
    assert language.profile_lang_from_context({"id": "x"}) is None
    assert language.profile_lang_from_context(None) is None
    assert language.profile_lang_from_context({"language": 7}) is None


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
