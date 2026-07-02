"""Translations registry: one place for all user-facing copy, per language.

Resolution chain: admin override[lang] > built-in default[lang] > the default
language > English. The server-side consumers (escalation card, closing bubble,
low-content nudge, model-error nudge) read through it, and the widget-scope
strings are served via GET /api/chat/i18n.
"""
from __future__ import annotations

import json

import pytest

import antispam
import chat_service
import escalation
import settings
import translations


@pytest.fixture(autouse=True)
def _clean():
    settings.invalidate()
    yield
    settings.invalidate()


def test_defaults_resolve_per_language_with_english_fallback():
    assert translations.text("closing_suggestion", "ru") == "Проблема решена."
    assert translations.text("closing_suggestion", "en") == "Issue solved."
    # Unknown language -> default language (ru in tests) -> its copy exists.
    assert translations.text("closing_suggestion", "zz") in (
        "Проблема решена.", "Issue solved.")
    # A key every language has: greeting.
    assert translations.text("greeting", "tr").startswith("Merhaba")


def test_override_wins_and_empty_falls_back():
    settings._cache["translations"] = {
        "ru": {"greeting": "Здорово, я Ника!", "send": ""}}
    assert translations.text("greeting", "ru") == "Здорово, я Ника!"
    assert translations.text("send", "ru") == "Отправить"   # empty -> default


def test_added_language_inherits_english_until_translated():
    # A language with no built-in copy starts on English…
    assert translations.text("support", "de") in ("Support", "Поддержка")
    # …and becomes translatable via overrides.
    settings._cache["translations"] = {"de": {"support": "Hilfe"}}
    assert translations.text("support", "de") == "Hilfe"


def test_server_consumers_read_the_registry():
    settings._cache["translations"] = {
        "en": {
            "escalation_message": "Our humans will help.",
            "escalation_button": "Ping support",
            "closing_suggestion": "All good.",
            "low_content_reply": "Say more please.",
            "model_error_reply": "Blip, retry please.",
        }
    }
    payload = escalation.build_payload("en")
    assert payload["message"] == "Our humans will help."
    assert payload["button"]["label"] == "Ping support"
    assert chat_service.closing_suggestion_for("en") == "All good."
    assert antispam.low_content_reply("en") == "Say more please."
    assert chat_service._model_error_reply("en") == "Blip, retry please."


def test_contact_url_is_per_language_with_general_fallback(monkeypatch):
    import config
    # No per-language URL configured -> the deploy-level default (legacy
    # general.contact_form_url override, then env CONTACT_FORM_URL).
    monkeypatch.setattr(config, "CONTACT_FORM_URL", "https://x/default")
    assert escalation.build_payload("en")["button"]["url"] == "https://x/default"
    # Per-language URLs from the Translations registry win.
    settings._cache["translations"] = {
        "es": {"contact_url": "https://x/es"},
        "en": {"contact_url": "https://x/en"},
    }
    assert escalation.build_payload("es")["button"]["url"] == "https://x/es"
    assert escalation.build_payload("en")["button"]["url"] == "https://x/en"
    # A language without its own URL falls through the default-language chain
    # (default language in tests is ru -> no override -> en override).
    assert escalation.build_payload("tr")["button"]["url"] in (
        "https://x/en", "https://x/default")


def test_contact_url_validation():
    settings.validate_translations({"en": {"contact_url": "https://x/support"}})
    with pytest.raises(ValueError):
        settings.validate_translations({"en": {"contact_url": "not-a-url"}})


def test_widget_strings_scope_only():
    strings = translations.widget_strings(["en", "ru"])
    assert strings["en"]["greeting"]
    assert strings["ru"]["support"] == "Поддержка"
    # Server-scope copy is not shipped to the client.
    assert "escalation_message" not in strings["en"]


async def test_public_i18n_endpoint(monkeypatch):
    from api import chat as chat_api
    monkeypatch.setattr(settings, "_cache",
                        {"language": {"default": "en", "supported": ["en", "ru"]},
                         "translations": {"ru": {"support": "Хелп"}}})
    resp = await chat_api.widget_i18n()
    data = json.loads(resp.body)
    assert data["languages"] == ["en", "ru"]
    assert data["strings"]["ru"]["support"] == "Хелп"
    assert data["strings"]["en"]["support"] == "Support"
    assert resp.headers["Cache-Control"].startswith("public")


def test_validate_translations():
    v = settings.validate_translations(
        {"RU": {"greeting": "Привет!", "send": ""}, "de": {}})
    assert v == {"ru": {"greeting": "Привет!"}}  # normalized, empties dropped
    with pytest.raises(ValueError):
        settings.validate_translations({"xx": {"greeting": "hi"}})  # bad ISO code
    with pytest.raises(ValueError):
        settings.validate_translations({"en": {"nope": "hi"}})      # unknown key
    with pytest.raises(ValueError):
        settings.validate_translations({"en": {"greeting": 5}})     # not a string
    with pytest.raises(ValueError):
        settings.validate_translations([1, 2])
