"""Public Telegram link handling — the ONE home for the player-facing domain.

The historic ``t.me`` domain had its registrar delegation suspended, so ``t.me``
links error in a normal browser (they resolve only inside the Telegram apps).
Every player-facing / model-facing Telegram link is therefore served on the
official legacy alias ``telegram.me``, which serves the identical ``?start=``
deeplinks and username routes.

Two normalizers live here so a future domain change is a single edit:

* ``normalize_tg_url`` — for a SINGLE stored URL field (the channel link, a
  contact URL). Operator-stored values a code deploy can't reach in the DB are
  rewritten at render time, no migration needed.
* ``normalize_tg_text`` — for FREE TEXT that may embed a link inline (KB texts,
  prompt-variable prose). Rewrites every standalone ``t.me`` host in place so
  the model never sees a suspended domain it could echo back to the player.

Both rewrite ``t.me`` only when it is the WHOLE host — a label that merely
contains it (``not-t.me.evil.com``, ``sub.t.me``) is left untouched.
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlsplit

# The one place the public Telegram link base lives.
TG_LINK_BASE = "https://telegram.me"

# A standalone `t.me` host inside free text: optional scheme + optional www,
# `t.me` as a WHOLE label (the negative lookbehind rejects `not-t.me` / a
# `sub.t.me` subdomain), followed by a path/query/fragment/space or end.
_TME_TEXT_RE = re.compile(
    r"(?<![\w.-])((?:https?://)?(?:www\.)?)t\.me(?=[/?#\s]|$)",
    re.IGNORECASE,
)


def normalize_tg_url(url: Optional[str]) -> str:
    """Rewrite a legacy ``t.me`` host in a SINGLE URL to ``telegram.me``.

    Non-``t.me`` URLs (and blanks) pass through untouched; only ``t.me`` as the
    full host is rewritten (a domain that merely contains ``t.me``, e.g.
    ``not-t.me.evil.com``, is left alone).
    """
    if not url:
        return url or ""
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return url
    if host in ("t.me", "www.t.me"):
        rest = url.split("//", 1)[1] if "//" in url else url
        rest = rest.split("/", 1)[1] if "/" in rest else ""
        return f"{TG_LINK_BASE}/{rest}"
    return url


def normalize_tg_text(text: Optional[str]) -> str:
    """Rewrite every standalone ``t.me`` link EMBEDDED in free text to ``telegram.me``.

    Scheme + ``www.`` are preserved; a label that only contains ``t.me``
    (``not-t.me``, ``sub.t.me``) is not touched. Blanks pass through.
    """
    if not text:
        return text or ""
    return _TME_TEXT_RE.sub(lambda m: f"{m.group(1)}telegram.me", text)
