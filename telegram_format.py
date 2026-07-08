"""Outgoing-text shaping for the Telegram (retention) channel.

Two independent concerns, both applied to model-generated retention text before
it reaches the player:

1. ``normalize_punctuation`` — a DETERMINISTIC scrub of the typographic
   characters the persona is told to avoid but the model keeps emitting anyway
   (em/en dashes, guillemet/angle quotes, curly quotes). The prompt rule alone
   was not reliably followed, so this guarantees it: plain hyphens and straight
   vertical quotes only. Applied to the persisted text too, so the admin
   transcript matches what the player saw.

2. ``to_html`` — convert the LIGHT Markdown subset the retention persona may now
   use (**bold** / *italic* / `code`) into the matching Telegram HTML
   (``parse_mode=HTML``) tags, safely. The text is HTML-escaped FIRST and only a
   whitelist of balanced tags is re-introduced, and bare URLs + code spans are
   stashed behind private-use sentinels so their punctuation can't be re-chewed
   by the emphasis passes — the same approach the widget's renderMarkdown uses.
   The output is always well-formed, balanced HTML, so Telegram never rejects it
   (the send sites still fall back to plain text on any error, belt-and-braces).
"""
from __future__ import annotations

import html
import re

# --- 1. deterministic punctuation scrub -------------------------------------
# Map every "AI-tell" typographic character to its plain ASCII equivalent.
_PUNCT_MAP = {
    "—": "-",   # — em dash
    "–": "-",   # – en dash
    "―": "-",   # ― horizontal bar
    "«": '"',   # « left guillemet
    "»": '"',   # » right guillemet
    "‹": "'",   # ‹ single left angle quote
    "›": "'",   # › single right angle quote
    "“": '"',   # “ left double curly quote
    "”": '"',   # ” right double curly quote
    "„": '"',   # „ low double quote
    "‟": '"',   # ‟ high reversed double quote
    "‘": "'",   # ‘ left single curly quote
    "’": "'",   # ’ right single curly quote / apostrophe
    "‚": "'",   # ‚ low single quote
}
_PUNCT_RE = re.compile("|".join(map(re.escape, _PUNCT_MAP)))


def normalize_punctuation(text: str) -> str:
    """Replace em/en dashes and guillemet/curly quotes with plain ASCII."""
    if not text:
        return text
    return _PUNCT_RE.sub(lambda m: _PUNCT_MAP[m.group(0)], text)


# --- 2. light Markdown -> Telegram HTML -------------------------------------
_URL_RE = re.compile(r"https?://\S+")
_CODE_RE = re.compile(r"`([^`\n]+)`")
_BOLD_RE = re.compile(r"(?<!\*)\*\*(?!\s)([^\n*]+?)(?<!\s)\*\*(?!\*)")
_BOLD_US_RE = re.compile(r"(?<!_)__(?!\s)([^\n_]+?)(?<!\s)__(?!_)")
_ITALIC_RE = re.compile(r"(?<![\*\w])\*(?!\s)([^\n*]+?)(?<!\s)\*(?![\*\w])")
_ITALIC_US_RE = re.compile(r"(?<![_\w])_(?!\s)([^\n_]+?)(?<!\s)_(?![_\w])")

# Private-use sentinels: characters the model will never emit, used to stash
# already-final spans so later passes leave them alone.
_SENT_OPEN = ""
_SENT_CLOSE = ""


def to_html(text: str) -> str:
    """Render the retention Markdown subset as safe Telegram HTML.

    Handles **bold**/__bold__, *italic*/_italic_ and `code`; everything else is
    HTML-escaped plain text. Bare URLs and code spans are protected so their
    punctuation survives the emphasis passes. Output is balanced HTML.
    """
    if not text:
        return text

    stash: list[str] = []

    def _stash(payload: str) -> str:
        stash.append(payload)
        return f"{_SENT_OPEN}{len(stash) - 1}{_SENT_CLOSE}"

    # Stash code spans (content escaped, wrapped in <code>) before anything else.
    def _code_repl(m: re.Match[str]) -> str:
        return _stash(f"<code>{html.escape(m.group(1))}</code>")

    text = _CODE_RE.sub(_code_repl, text)

    # Stash bare URLs (escaped) so underscores/asterisks in them aren't chewed.
    def _url_repl(m: re.Match[str]) -> str:
        return _stash(html.escape(m.group(0)))

    text = _URL_RE.sub(_url_repl, text)

    # Escape the remaining plain text, THEN re-introduce emphasis tags.
    text = html.escape(text)

    text = _BOLD_RE.sub(r"<b>\1</b>", text)
    text = _BOLD_US_RE.sub(r"<b>\1</b>", text)
    text = _ITALIC_RE.sub(r"<i>\1</i>", text)
    text = _ITALIC_US_RE.sub(r"<i>\1</i>", text)

    # Restore the stashed spans.
    def _unstash(m: re.Match[str]) -> str:
        return stash[int(m.group(1))]

    text = re.sub(f"{_SENT_OPEN}(\\d+){_SENT_CLOSE}", _unstash, text)
    return text
