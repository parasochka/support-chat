"""Language resolution — one source of truth: the browser language.

The widget and the AI answers both use a single language, derived from the
browser locale (`navigator.language`) the front-end sends, mapped to a
supported base code. There is no per-message mirroring, no account-language
input, and no manual switcher — the chrome and the answers always speak the
browser's language for the whole session.

Priority (for the answer language):
  1. `locale` mapped to a base code (e.g. es-MX -> es), if supported — this is
     where the browser language (navigator.language) lands
  2. persisted `session_lang` from session create (the resolved locale)
  3. AUTO -> fall back to DEFAULT_LANGUAGE

The source prompt + KB stay Russian; only the answer language varies.
"""
from __future__ import annotations

from typing import Optional

# Sentinel meaning "no supported language resolved -> use the service default".
AUTO = "auto"

# Human-readable names used in the Layer-3 answer-language directive.
LANG_NAMES = {
    "en": "English",
    "es": "Spanish",
    "ru": "Russian",
    "tr": "Turkish",
    "pt": "Portuguese",
}


def default_code() -> str:
    """The resolved default answer language (admin `language` group > env > default)."""
    import settings  # lazy: avoid an import cycle at module load
    return settings.language()["default"]


def supported_codes() -> list[str]:
    """The resolved supported-language set (admin `language` group > env > default)."""
    import settings  # lazy
    return settings.language()["supported"]


def _supported(code: Optional[str]) -> Optional[str]:
    if not code:
        return None
    c = code.strip().lower()
    return c if c in supported_codes() else None


def locale_to_lang(locale: Optional[str]) -> Optional[str]:
    """Map a locale like 'es-MX' or 'pt_BR' to a supported base code."""
    if not locale:
        return None
    base = locale.replace("_", "-").split("-", 1)[0].strip().lower()
    return _supported(base)


def resolve(locale: Optional[str] = None,
            session_lang: Optional[str] = None) -> str:
    """Resolve the answer language from the browser locale, else AUTO.

    Priority: browser `locale` -> persisted `session_lang` (the locale resolved
    at session create) -> AUTO. `locale` accepts either a base code ('ru') or a
    locale ('ru-RU'). Pure priority chain so it is easy to unit-test.
    """
    chosen = locale_to_lang(locale)
    if chosen:
        return chosen

    if _supported(session_lang):
        return session_lang  # type: ignore[return-value]

    return AUTO


def language_name(resolved: str) -> str:
    """Human name of the language to answer in. AUTO -> the service default."""
    default = default_code()
    code = default if resolved == AUTO else resolved
    return LANG_NAMES.get(code, LANG_NAMES.get(default, "English"))
