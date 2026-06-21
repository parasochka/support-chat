"""Language resolution — deterministic priority, never asks the user.

This resolves the *default* answer language. The model is always told to
mirror the language the player actually writes in; the resolved code below is
only the fallback used when the player's language can't be determined (e.g. a
one-word message). So a Russian-browser visitor who types Russian gets Russian,
and the same visitor typing Spanish gets Spanish.

Priority (for the fallback default):
  1. explicit `lang` param (if supported)
  2. `locale` param mapped to a base code (e.g. es-MX -> es), if supported —
     this is where the browser language (navigator.language) lands
  3. persisted `session_lang` from a prior turn
  4. AUTO -> fall back to DEFAULT_LANGUAGE

The source prompt + KB stay Russian; only the answer language varies.
"""
from __future__ import annotations

from typing import Optional

import config

# Sentinel meaning "let the model answer in the language of the user's message".
AUTO = "auto"

# Human-readable names used in the Layer-3 fallback-language directive.
LANG_NAMES = {
    "en": "English",
    "es": "Spanish",
    "ru": "Russian",
    "tr": "Turkish",
    "pt": "Portuguese",
}


def _supported(code: Optional[str]) -> Optional[str]:
    if not code:
        return None
    c = code.strip().lower()
    return c if c in config.SUPPORTED_LANGUAGES else None


def locale_to_lang(locale: Optional[str]) -> Optional[str]:
    """Map a locale like 'es-MX' or 'pt_BR' to a supported base code."""
    if not locale:
        return None
    base = locale.replace("_", "-").split("-", 1)[0].strip().lower()
    return _supported(base)


def resolve(lang: Optional[str] = None, locale: Optional[str] = None,
            session_lang: Optional[str] = None) -> str:
    """Resolve the answer language code, or AUTO if it must be auto-detected.

    `session_lang` (already persisted from a prior turn) takes effect only via
    being passed as `lang` by the caller; this function keeps the pure priority
    chain so it is easy to unit-test.
    """
    chosen = _supported(lang)
    if chosen:
        return chosen

    chosen = locale_to_lang(locale)
    if chosen:
        return chosen

    if _supported(session_lang):
        return session_lang  # type: ignore[return-value]

    # Nothing explicit and no persisted session language -> auto-detect.
    return AUTO


def fallback_language_name(resolved: str) -> str:
    """Human name of the language to answer in *only when the player's own

    language can't be determined* (e.g. a one-word or symbol-only message).
    The model is always told to mirror the player's language first; this is the
    safety net. For AUTO it is the service default.
    """
    code = config.DEFAULT_LANGUAGE if resolved == AUTO else resolved
    return LANG_NAMES.get(code, LANG_NAMES.get(config.DEFAULT_LANGUAGE, "English"))
