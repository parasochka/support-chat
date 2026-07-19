"""Layered anti-spam: Turnstile, rate-limit, cooldown, caps, injection scan.

All thresholds come from config (§3 env). The IP sliding-window and per-session
cooldown use in-memory dicts — fine for Phase 1, but they do NOT span multiple
instances (documented limitation).
"""
from __future__ import annotations

import re
import time
import unicodedata
from collections import defaultdict, deque
from typing import Optional

import httpx

import config
import settings

# --- in-memory state --------------------------------------------------------
_ip_hits: dict[str, deque[float]] = defaultdict(deque)
_last_message_at: dict[str, float] = {}

# Above this many tracked IPs we drop buckets whose whole window has expired so
# the dict cannot grow without bound as unique IPs (and forged X-Forwarded-For
# values) churn through. Without this, every IP ever seen leaks an empty deque.
_IP_PRUNE_THRESHOLD = 10_000


class AntiSpamError(Exception):
    """Base for anti-spam rejections; carries an HTTP status + machine code."""

    def __init__(self, status: int, code: str, detail: str):
        super().__init__(detail)
        self.status = status
        self.code = code
        self.detail = detail


# ---------------------------------------------------------------------------
# 1. Cloudflare Turnstile (verified at session create; ADVISORY, fail-open)
# ---------------------------------------------------------------------------
async def verify_turnstile(token: Optional[str], remote_ip: Optional[str] = None,
                           secret: Optional[str] = None) -> dict[str, object]:
    """Return {'ok': bool, 'skipped': bool, 'reason': str}.

    `secret` is the PRODUCT's own Turnstile secret when it has one (each client
    domain runs its own Turnstile widget); the deploy env TURNSTILE_SECRET is
    only the fallback. Skips gracefully (ok=True, skipped=True) when neither is
    set (dev), so the caller can log that verification was skipped.

    Turnstile is deliberately ADVISORY (fail-open): the challenges.cloudflare.com
    script can be blocked in some regions/networks, and a player must never lose
    the chat over that. A MISSING token (the client-side widget didn't load or
    timed out) and a verifier outage both SKIP the check — the rate limit,
    cooldown, low-content and injection layers still gate every request. Only an
    explicit "invalid token" verdict from Cloudflare blocks (a definitive bot
    signal, not a loading problem).
    """
    effective_secret = secret or config.TURNSTILE_SECRET
    if not effective_secret:
        return {"ok": True, "skipped": True, "reason": "no_secret_dev_mode"}

    if not token:
        # The client couldn't obtain a token (Turnstile blocked / slow / failed).
        # Fail-open by design — see the docstring.
        return {"ok": True, "skipped": True, "reason": "no_token_client_side"}

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://challenges.cloudflare.com/turnstile/v0/siteverify",
                data={
                    "secret": effective_secret,
                    "response": token,
                    **({"remoteip": remote_ip} if remote_ip else {}),
                },
            )
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        # Verifier outage/unreachable: fail-open (advisory check), but explain.
        return {"ok": True, "skipped": True,
                "reason": f"verify_error:{exc.__class__.__name__}"}

    if not bool(data.get("success")):
        codes = [str(c) for c in (data.get("error-codes") or [])]
        # ADVISORY fail-open (docstring): ONLY a definitive bad-TOKEN verdict
        # blocks — a real bot/replay signal. Everything else that yields
        # success:false — a verifier `internal-error`, a mistyped/absent product
        # secret (`invalid-input-secret` / `missing-input-secret`), a malformed
        # request, or an unknown/empty code — is a Cloudflare-side config/outage
        # problem, NOT a bot, and must SKIP so a player never loses the chat over
        # it (a typo'd secret would otherwise 403 every visitor = product outage).
        blocking_codes = {"invalid-input-response", "timeout-or-duplicate"}
        if blocking_codes.intersection(codes):
            return {"ok": False, "skipped": False,
                    "reason": "turnstile_failed:" + ",".join(codes)}
        return {"ok": True, "skipped": True,
                "reason": "turnstile_nonblocking:" + ",".join(codes or ["unknown"])}
    return {"ok": True, "skipped": False, "reason": "ok"}


# ---------------------------------------------------------------------------
# 3. IP sliding-window rate-limit
# ---------------------------------------------------------------------------
def _prune_ip_hits(now: float, window: float) -> None:
    """Drop IP buckets whose entire window has expired (bound memory growth)."""
    stale = [ip for ip, hits in _ip_hits.items()
             if not hits or now - hits[-1] > window]
    for ip in stale:
        _ip_hits.pop(ip, None)


def check_rate_limit(ip: str, max_hits: Optional[int] = None) -> None:
    """Raise AntiSpamError(429) if the key exceeded the window allowance.

    `max_hits` overrides the per-IP allowance for callers with their own budget
    (the Telegram retention chat uses the higher `tg_rate_limit_max_per_user` —
    a lively human dialogue easily outpaces the widget's per-IP limit). The
    window is shared (`window_sec`). A blocked attempt is NOT recorded, so the
    window drains while the sender is over the limit instead of re-arming.
    """
    now = time.monotonic()
    cfg = settings.antispam()
    window = cfg["window_sec"]
    if len(_ip_hits) > _IP_PRUNE_THRESHOLD:
        _prune_ip_hits(now, window)
    hits = _ip_hits[ip]
    while hits and now - hits[0] > window:
        hits.popleft()
    if len(hits) >= (max_hits if max_hits is not None
                     else cfg["rate_limit_max_per_ip"]):
        # Don't leave a fresh empty bucket behind for a one-shot/forged IP.
        if not hits:
            _ip_hits.pop(ip, None)
        raise AntiSpamError(429, "rate_limited",
                            "Too many requests from this IP; slow down.")
    hits.append(now)


# ---------------------------------------------------------------------------
# 5. Per-session cooldown
# ---------------------------------------------------------------------------
# Above this many tracked sessions we prune stale cooldown entries so the
# in-memory dict cannot grow without bound under churn (one session per visitor).
_COOLDOWN_PRUNE_THRESHOLD = 10_000


def _prune_cooldowns(now: float, cutoff: float) -> None:
    stale = [sid for sid, ts in _last_message_at.items() if now - ts > cutoff]
    for sid in stale:
        _last_message_at.pop(sid, None)


def check_cooldown(session_id: str) -> None:
    """Raise AntiSpamError(429) if messages arrive faster than the cooldown.

    Check only — the stamp is armed separately (arm_cooldown) AFTER the other
    pre-model gates pass. Stamping here punished a rejected message (too long /
    low-content / injection-blocked) by 429ing the player's immediate corrected
    resend.
    """
    now = time.monotonic()
    cooldown = settings.antispam()["cooldown_sec"]
    if len(_last_message_at) > _COOLDOWN_PRUNE_THRESHOLD:
        _prune_cooldowns(now, cooldown)
    last = _last_message_at.get(session_id)
    if last is not None and now - last < cooldown:
        raise AntiSpamError(429, "cooldown",
                            "You're sending messages too quickly; please wait a moment.")


def arm_cooldown(session_id: str) -> None:
    """Stamp the cooldown clock for a message that passed every pre-model gate."""
    _last_message_at[session_id] = time.monotonic()


def clear_cooldown(session_id: str) -> None:
    """Drop the cooldown mark for a session so its very next message isn't throttled.

    Used after a cross-topic ROUTING-ONLY turn: that turn recorded a cooldown
    stamp here (it ran the full gate before chat_service decided it was a routing
    turn), but the widget immediately re-asks the SAME question against the newly
    switched topic's KB — an automatic follow-up, not player spam. Without this
    the ~1s re-ask lands inside the 2s cooldown window and 429s, so the player's
    question is never answered against the right KB and the raw throttle error
    renders as the assistant's reply. Server-decided (not a client-supplied
    bypass), and the routing turn still cost a model call + counts toward the cap.
    """
    _last_message_at.pop(session_id, None)


# ---------------------------------------------------------------------------
# 7. Input length cap
# ---------------------------------------------------------------------------
def check_input_length(text: str) -> None:
    if text is None:
        raise AntiSpamError(400, "empty", "Message text is required.")
    max_chars = settings.antispam()["max_input_chars"]
    if len(text) > max_chars:
        raise AntiSpamError(400, "too_long",
                            f"Message exceeds {max_chars} characters.")
    if text.strip() == "":
        raise AntiSpamError(400, "empty", "Message text is required.")


# ---------------------------------------------------------------------------
# 7b. Low-content / junk guard (lone chars, symbol spam, repeated mashing)
# ---------------------------------------------------------------------------
# A real product-support question carries at least a couple of distinct
# letters/digits. A lone character ("a"), a message of pure punctuation or emoji
# ("???", "🙂🙂"), or one character mashed over and over ("aaaaaa", "1111") can be
# fired in a loop by a bot or an idle user and would each cost an OpenAI
# round-trip for an answer that cannot exist. We stop them before the model call
# so they never burn tokens. Tunable via the `antispam` settings group:
# `low_content_block` (master switch) and `min_meaningful_chars` (how many
# letters/digits a message must carry to be worth answering).
def check_low_content(text: str) -> None:
    """Raise AntiSpamError(400, 'low_content') for a message with no answerable
    content so it never reaches the model. Assumes check_input_length already
    rejected empty/oversized input."""
    cfg = settings.antispam()
    if not cfg["low_content_block"]:
        return
    min_chars = cfg["min_meaningful_chars"]
    norm = unicodedata.normalize("NFKC", text or "")
    alnum = [c for c in norm if c.isalnum()]
    # Too few letters/digits to form a question — covers lone characters and
    # messages that are only punctuation, separators, or emoji.
    if len(alnum) < min_chars:
        raise AntiSpamError(400, "low_content",
                            "Please describe your question in a few words.")
    # A single distinct character mashed/repeated ("aaaa", "11", "ё ё ё"): real
    # content, however short, uses more than one distinct symbol.
    if len(alnum) >= 2 and len({c.lower() for c in alnum}) < 2:
        raise AntiSpamError(400, "low_content",
                            "Please describe your question in a few words.")


# Localized, model-free nudge shown when check_low_content rejects a turn, so the
# player gets a gentle "ask a real question" reply instead of a hard error. The
# copy lives in the translations registry (admin Translations tab > defaults).
def low_content_reply(lang: str) -> str:
    """Return the localized low-content nudge, falling back to English."""
    import translations  # lazy: avoid a settings import cycle at module load
    return translations.text("low_content_reply", lang)


# ---------------------------------------------------------------------------
# 8. Injection / jailbreak scan on the user message (log, don't necessarily block)
# ---------------------------------------------------------------------------
_INJECTION_PATTERNS = (
    "ignore previous",
    "ignore all previous",
    "ignore the above",
    "ignore your instructions",
    "игнорируй предыдущие",
    "игнорируй все",
    "игнорируй инструкции",
    "забудь инструкции",
    "забудь все предыдущие",
    "you are now",
    "ты теперь",
    "act as",
    "pretend to be",
    "представь, что ты",
    "system prompt",
    "системный промпт",
    "system:",
    "reveal your",
    "покажи свой промпт",
    "раскрой систем",
    "purgetool",
    "developer mode",
    "режим разработчика",
    "jailbreak",
    "dan mode",
    "disregard your instructions",
    "override your",
    "new instructions",
    "новые инструкции",
)


# Zero-width / invisible characters commonly used to break up trigger words so a
# naive substring scan misses them ("ig​nore previous").
_ZERO_WIDTH_RE = re.compile(r"[​‌‍⁠﻿]")
# Runs of separators (spaces, dots, dashes, etc.) inserted between letters to
# evade matching ("i g n o r e", "i.g.n.o.r.e"). Collapsed to nothing only when
# they sit between two single letters so ordinary phrases are left intact.
_SPACED_LETTERS_RE = re.compile(r"(?<=\b\w)[\s._\-*]+(?=\w\b)")


def _normalize_for_scan(text: str) -> str:
    """Fold a message to a canonical form so trivial obfuscation can't hide a
    known injection trigger: Unicode-normalize, lower-case, strip zero-width
    characters, and collapse separators sprinkled between single letters."""
    norm = unicodedata.normalize("NFKC", text)
    norm = _ZERO_WIDTH_RE.sub("", norm)
    norm = norm.lower()
    # Run the de-spacing pass twice so "i . g . n" collapses fully.
    for _ in range(2):
        norm = _SPACED_LETTERS_RE.sub("", norm)
    return norm


def _compile_injection_res(patterns: tuple) -> list[re.Pattern]:
    """Build a WORD-BOUNDARY-aware regex per trigger phrase.

    A plain substring scan produced false positives: "act as" fired inside
    "contact as" / "react as" / "impact assessment" (and the fully-de-spaced view
    made it worse — "reactas" contains "actas"), so an ordinary player message
    like "contact a support agent" was hard-blocked with a 400. Each phrase is
    matched instead as WHOLE words: a `\\b` is anchored on any alphanumeric edge,
    and internal spaces become `\\s*` so the phrase still matches when an attacker
    removes / zero-widths the separators ("ignore​previous" -> "ignoreprevious").
    Matching runs over `_normalize_for_scan` output (NFKC + zero-width strip +
    letter-run de-spacing collapses "i g n o r e" -> "ignore"), so obfuscation is
    still caught while innocent substrings are not.
    """
    compiled: list[re.Pattern] = []
    for raw in patterns:
        p = (raw or "").strip().lower()
        if not p:
            continue
        core = r"\s*".join(re.escape(tok) for tok in p.split())
        prefix = r"\b" if p[:1].isalnum() else ""
        suffix = r"\b" if p[-1:].isalnum() else ""
        compiled.append(re.compile(prefix + core + suffix))
    return compiled


_INJECTION_RES = _compile_injection_res(_INJECTION_PATTERNS)


def scan_injection(text: str) -> bool:
    """Return True if the user message looks like an injection/jailbreak attempt.

    SYSTEM_CORE plus the Layer-3 guardrails are the real defence; this scan feeds
    the `injection_blocked` audit log (and an optional hard block, see config).
    Input is normalized first so spacing/zero-width obfuscation can't slip a known
    trigger past the (word-boundary-aware) match, while an innocent substring of a
    longer word no longer trips it.
    """
    if not text:
        return False
    # Match each boundary-aware pattern against two views: the normalized
    # (de-obfuscated) form catches spacing / zero-width / full-width tricks, and
    # the raw lower-cased text is a cheap belt-and-suspenders for anything NFKC
    # might alter.
    norm = _normalize_for_scan(text)
    lowered = text.lower()
    return any(rx.search(norm) or rx.search(lowered) for rx in _INJECTION_RES)


def reset_state() -> None:
    """Test helper: clear in-memory windows."""
    _ip_hits.clear()
    _last_message_at.clear()
