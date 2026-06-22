"""Language resolution — deterministic priority, never asks the user.

This resolves the *default* answer language. The model is always told to
mirror the language the player actually writes in; the resolved code below is
only the fallback used when the player's language can't be determined (e.g. a
one-word message). So a Russian-browser visitor who types Russian gets Russian,
and the same visitor typing Spanish gets Spanish.

Priority (for the fallback default):
  1. explicit `lang` param (if supported) — e.g. the player's manual header
     switch, which is the deliberate top override
  2. `locale` param mapped to a base code (e.g. es-MX -> es), if supported —
     this is where the browser language (navigator.language) lands
  3. `profile_lang` — the account/profile language carried in the front-end
     handshake (`user_context`), mapped the same way as `locale`
  4. persisted `session_lang` from a prior turn
  5. AUTO -> fall back to DEFAULT_LANGUAGE

The source prompt + KB stay Russian; only the answer language varies.
"""
from __future__ import annotations

from typing import Optional

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


def resolve(lang: Optional[str] = None, locale: Optional[str] = None,
            profile_lang: Optional[str] = None,
            session_lang: Optional[str] = None) -> str:
    """Resolve the answer language code, or AUTO if it must be auto-detected.

    Priority: manual `lang` -> browser `locale` -> `profile_lang` (account
    language from the handshake) -> persisted `session_lang` -> AUTO. Both
    `locale` and `profile_lang` accept either a base code ('ru') or a locale
    ('ru-RU'). This function keeps the pure priority chain so it is easy to
    unit-test.
    """
    chosen = _supported(lang)
    if chosen:
        return chosen

    chosen = locale_to_lang(locale)
    if chosen:
        return chosen

    chosen = locale_to_lang(profile_lang)
    if chosen:
        return chosen

    if _supported(session_lang):
        return session_lang  # type: ignore[return-value]

    # Nothing explicit and no persisted session language -> auto-detect.
    return AUTO


def profile_lang_from_context(user_context: Optional[dict]) -> Optional[str]:
    """Pull the account/profile language out of the front-end `user_context`.

    Accepts a base code ('ru') or a locale ('ru-RU') under either `language` or
    `lang`. Returns the raw string (resolve() maps it to a supported code) or
    None. This field is only used to seed the default answer language; it is
    not surfaced to the model.
    """
    ctx = user_context or {}
    val = ctx.get("language") or ctx.get("lang")
    return val if isinstance(val, str) else None


import re as _re

# Distinctive-character signals per language (mirrors the widget's detector).
# Conservative on purpose: only a clear signal returns a code, else None, so a
# short/neutral message never drifts the session to the wrong language.
_LATIN_SIGNALS = {
    "es": _re.compile(r"[ñ¿¡]"),
    "pt": _re.compile(r"[ãõ]"),
    "tr": _re.compile(r"[şğıİ]"),
}
_CYRILLIC = _re.compile(r"[а-яё]")


def detect(text: Optional[str]) -> Optional[str]:
    """Best-effort detection of the language the player is *writing* in.

    Returns a supported base code only on a confident signal, else None. Used to
    make the session language sticky (§12) without locking it to the default —
    we persist only a *detected* code, never the bare service default.
    """
    if not text:
        return None
    s = text.lower()
    if _supported("ru") and _CYRILLIC.search(s):
        return "ru"
    best = None
    best_score = 0
    for code, pat in _LATIN_SIGNALS.items():
        if not _supported(code):
            continue
        score = len(pat.findall(s))
        if score > best_score:
            best_score = score
            best = code
    return best


def fallback_language_name(resolved: str) -> str:
    """Human name of the language to answer in *only when the player's own

    language can't be determined* (e.g. a one-word or symbol-only message).
    The model is always told to mirror the player's language first; this is the
    safety net. For AUTO it is the service default.
    """
    default = default_code()
    code = default if resolved == AUTO else resolved
    return LANG_NAMES.get(code, LANG_NAMES.get(default, "English"))
