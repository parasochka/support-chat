"""Language resolution — deterministic priority, never asks the user.

Priority:
  1. explicit `lang` param (if supported)
  2. `locale` param mapped to a base code (e.g. es-MX -> es), if supported
  3. model auto-detect (signalled by returning AUTO; the Layer-3 directive then
     tells the model to answer in the user's language; the session persists the
     detected language after the first turn)
  4. DEFAULT_LANGUAGE

The source prompt + KB stay Russian; only the answer language varies.
"""
from __future__ import annotations

from typing import Optional

import config

# Sentinel meaning "let the model answer in the language of the user's message".
AUTO = "auto"

# Human-readable names used in the Layer-3 directive ("Answer strictly in {LANG}.").
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


def directive_language_name(resolved: str) -> str:
    """The concrete LANG string injected into the Layer-3 directive.

    For AUTO we instruct the model to mirror the user's language.
    """
    if resolved == AUTO:
        return "the same language as the user's message"
    return LANG_NAMES.get(resolved, config.DEFAULT_LANGUAGE)
