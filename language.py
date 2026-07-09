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

# Human-readable names used in the Layer-3 answer-language directive. These are
# the BUILT-IN defaults; the owner can add any ISO 639-1 language from the admin
# Language tab (persisted in the `language` settings group's `names` map), which
# merges over this via `all_language_names()`.
LANG_NAMES = {
    "en": "English",
    "es": "Spanish",
    "ru": "Russian",
    "tr": "Turkish",
    "pt": "Portuguese",
}

# Full ISO 639-1 catalogue (code -> English name). The admin "add language" picker
# is driven by this, so a new language can only be added with a valid ISO code and
# the right name — "everything is tied to ISO" per the brief. Keep alphabetical by
# code for easy scanning.
ISO_639_1 = {
    "ab": "Abkhazian", "aa": "Afar", "af": "Afrikaans", "ak": "Akan",
    "sq": "Albanian", "am": "Amharic", "ar": "Arabic", "an": "Aragonese",
    "hy": "Armenian", "as": "Assamese", "av": "Avaric", "ae": "Avestan",
    "ay": "Aymara", "az": "Azerbaijani", "bm": "Bambara", "ba": "Bashkir",
    "eu": "Basque", "be": "Belarusian", "bn": "Bengali", "bi": "Bislama",
    "bs": "Bosnian", "br": "Breton", "bg": "Bulgarian", "my": "Burmese",
    "ca": "Catalan", "ch": "Chamorro", "ce": "Chechen", "ny": "Chichewa",
    "zh": "Chinese", "cu": "Church Slavonic", "cv": "Chuvash", "kw": "Cornish",
    "co": "Corsican", "cr": "Cree", "hr": "Croatian", "cs": "Czech",
    "da": "Danish", "dv": "Divehi", "nl": "Dutch", "dz": "Dzongkha",
    "en": "English", "eo": "Esperanto", "et": "Estonian", "ee": "Ewe",
    "fo": "Faroese", "fj": "Fijian", "fi": "Finnish", "fr": "French",
    "fy": "Western Frisian", "ff": "Fulah", "gd": "Gaelic", "gl": "Galician",
    "lg": "Ganda", "ka": "Georgian", "de": "German", "el": "Greek",
    "kl": "Kalaallisut", "gn": "Guarani", "gu": "Gujarati", "ht": "Haitian",
    "ha": "Hausa", "he": "Hebrew", "hz": "Herero", "hi": "Hindi",
    "ho": "Hiri Motu", "hu": "Hungarian", "is": "Icelandic", "io": "Ido",
    "ig": "Igbo", "id": "Indonesian", "ia": "Interlingua", "ie": "Interlingue",
    "iu": "Inuktitut", "ik": "Inupiaq", "ga": "Irish", "it": "Italian",
    "ja": "Japanese", "jv": "Javanese", "kn": "Kannada", "kr": "Kanuri",
    "ks": "Kashmiri", "kk": "Kazakh", "km": "Khmer", "ki": "Kikuyu",
    "rw": "Kinyarwanda", "ky": "Kyrgyz", "kv": "Komi", "kg": "Kongo",
    "ko": "Korean", "kj": "Kuanyama", "ku": "Kurdish", "lo": "Lao",
    "la": "Latin", "lv": "Latvian", "li": "Limburgish", "ln": "Lingala",
    "lt": "Lithuanian", "lu": "Luba-Katanga", "lb": "Luxembourgish",
    "mk": "Macedonian", "mg": "Malagasy", "ms": "Malay", "ml": "Malayalam",
    "mt": "Maltese", "gv": "Manx", "mi": "Maori", "mr": "Marathi",
    "mh": "Marshallese", "mn": "Mongolian", "na": "Nauru", "nv": "Navajo",
    "nd": "North Ndebele", "nr": "South Ndebele", "ng": "Ndonga",
    "ne": "Nepali", "no": "Norwegian", "nb": "Norwegian Bokmål",
    "nn": "Norwegian Nynorsk", "oc": "Occitan", "oj": "Ojibwa", "or": "Oriya",
    "om": "Oromo", "os": "Ossetian", "pi": "Pali", "ps": "Pashto",
    "fa": "Persian", "pl": "Polish", "pt": "Portuguese", "pa": "Punjabi",
    "qu": "Quechua", "ro": "Romanian", "rm": "Romansh", "rn": "Rundi",
    "ru": "Russian", "se": "Northern Sami", "sm": "Samoan", "sg": "Sango",
    "sa": "Sanskrit", "sc": "Sardinian", "sr": "Serbian", "sn": "Shona",
    "sd": "Sindhi", "si": "Sinhala", "sk": "Slovak", "sl": "Slovenian",
    "so": "Somali", "st": "Southern Sotho", "es": "Spanish", "su": "Sundanese",
    "sw": "Swahili", "ss": "Swati", "sv": "Swedish", "tl": "Tagalog",
    "ty": "Tahitian", "tg": "Tajik", "ta": "Tamil", "tt": "Tatar",
    "te": "Telugu", "th": "Thai", "bo": "Tibetan", "ti": "Tigrinya",
    "to": "Tonga", "ts": "Tsonga", "tn": "Tswana", "tr": "Turkish",
    "tk": "Turkmen", "tw": "Twi", "ug": "Uyghur", "uk": "Ukrainian",
    "ur": "Urdu", "uz": "Uzbek", "ve": "Venda", "vi": "Vietnamese",
    "vo": "Volapük", "wa": "Walloon", "cy": "Welsh", "wo": "Wolof",
    "xh": "Xhosa", "yi": "Yiddish", "yo": "Yoruba", "za": "Zhuang",
    "zu": "Zulu",
}


def all_language_names() -> dict[str, str]:
    """Built-in names merged with the admin-added custom names (`language.names`).

    Used wherever a code needs a human name (the Layer-3 directive, the admin
    pickers) so a language the owner added from the panel is named correctly.
    """
    import settings  # lazy
    merged = dict(LANG_NAMES)
    names = settings.language().get("names") or {}
    if isinstance(names, dict):
        for code, name in names.items():
            if isinstance(code, str) and isinstance(name, str) and name.strip():
                merged[code.strip().lower()] = name.strip()
    return merged


def selectable_languages() -> list[dict[str, str]]:
    """The languages the admin can choose to support: built-ins, any custom names,
    and whatever is currently in the supported set — each with a resolved name.
    """
    names = all_language_names()
    codes = list(dict.fromkeys(
        list(names.keys()) + [c for c in supported_codes() if c]
    ))
    return [{"code": c, "name": names.get(c, ISO_639_1.get(c, c.upper()))}
            for c in sorted(codes)]


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

    normalized = _supported(session_lang)
    if normalized:
        return normalized

    return AUTO


def language_name(resolved: str) -> str:
    """Human name of the language to answer in. AUTO -> the service default."""
    default = default_code()
    code = default if resolved == AUTO else resolved
    names = all_language_names()
    return names.get(code, names.get(default, ISO_639_1.get(code, "English")))
