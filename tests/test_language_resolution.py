"""Language resolution precedence: browser locale > session lang > AUTO/default."""
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


def test_unsupported_locale_falls_to_session_then_auto():
    # Unsupported locale, no session lang -> AUTO (service default downstream).
    assert language.resolve(locale="de-DE") == language.AUTO
    # …but a persisted session language is used before AUTO.
    assert language.resolve(locale="de-DE", session_lang="ru") == "ru"


def test_session_lang_used_when_no_locale():
    assert language.resolve(session_lang="ru") == "ru"


def test_locale_beats_session():
    # The browser locale is the single source of truth, so it outranks a stale
    # persisted session language if the two ever disagree.
    assert language.resolve(locale="es-MX", session_lang="ru") == "es"


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
