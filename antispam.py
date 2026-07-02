"""Layered anti-spam: reCaptcha, rate-limit, cooldown, caps, injection scan.

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
# 1. reCaptcha v3 (verified at session create)
# ---------------------------------------------------------------------------
async def verify_recaptcha(token: Optional[str], remote_ip: Optional[str] = None
                           ) -> dict[str, object]:
    """Return {'ok': bool, 'skipped': bool, 'score': float|None, 'reason': str}.

    Skips gracefully (ok=True, skipped=True) when RECAPTCHA_SECRET is unset (dev),
    so the caller can log that verification was skipped.
    """
    if not config.RECAPTCHA_SECRET:
        return {"ok": True, "skipped": True, "score": None, "reason": "no_secret_dev_mode"}

    if not token:
        return {"ok": False, "skipped": False, "score": None, "reason": "missing_token"}

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://www.google.com/recaptcha/api/siteverify",
                data={
                    "secret": config.RECAPTCHA_SECRET,
                    "response": token,
                    **({"remoteip": remote_ip} if remote_ip else {}),
                },
            )
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        # Fail-open on verifier outage would be unsafe; fail-closed but explain.
        return {"ok": False, "skipped": False, "score": None,
                "reason": f"verify_error:{exc.__class__.__name__}"}

    score = data.get("score")
    success = bool(data.get("success"))
    if not success:
        return {"ok": False, "skipped": False, "score": score, "reason": "recaptcha_failed"}
    min_score = settings.antispam()["recaptcha_min_score"]
    if score is not None and score < min_score:
        return {"ok": False, "skipped": False, "score": score, "reason": "low_score"}
    return {"ok": True, "skipped": False, "score": score, "reason": "ok"}


# ---------------------------------------------------------------------------
# 3. IP sliding-window rate-limit
# ---------------------------------------------------------------------------
def _prune_ip_hits(now: float, window: float) -> None:
    """Drop IP buckets whose entire window has expired (bound memory growth)."""
    stale = [ip for ip, hits in _ip_hits.items()
             if not hits or now - hits[-1] > window]
    for ip in stale:
        _ip_hits.pop(ip, None)


def check_rate_limit(ip: str) -> None:
    """Raise AntiSpamError(429) if the IP exceeded the window allowance."""
    now = time.monotonic()
    cfg = settings.antispam()
    window = cfg["window_sec"]
    if len(_ip_hits) > _IP_PRUNE_THRESHOLD:
        _prune_ip_hits(now, window)
    hits = _ip_hits[ip]
    while hits and now - hits[0] > window:
        hits.popleft()
    if len(hits) >= cfg["rate_limit_max_per_ip"]:
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
    """Raise AntiSpamError(429) if messages arrive faster than the cooldown."""
    now = time.monotonic()
    cooldown = settings.antispam()["cooldown_sec"]
    if len(_last_message_at) > _COOLDOWN_PRUNE_THRESHOLD:
        _prune_cooldowns(now, cooldown)
    last = _last_message_at.get(session_id)
    if last is not None and now - last < cooldown:
        raise AntiSpamError(429, "cooldown",
                            "You're sending messages too quickly; please wait a moment.")
    _last_message_at[session_id] = now


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


def scan_injection(text: str) -> bool:
    """Return True if the user message looks like an injection/jailbreak attempt.

    SYSTEM_CORE plus the Layer-3 guardrails are the real defence; this scan feeds
    the `injection_blocked` audit log (and an optional hard block, see config).
    Input is normalized first so spacing/zero-width obfuscation can't slip a known
    trigger past the substring match.
    """
    if not text:
        return False
    # Check each pattern against three views so neither spacing nor Unicode
    # obfuscation can slip a known trigger past the substring match:
    #   - lowered:     the raw text, lower-cased (multi-word patterns, fast path)
    #   - norm_spaced: NFKC-normalized, zero-width stripped, single-spaced (catches
    #                  full-width / confusable characters in multi-word patterns)
    #   - norm_nospace: the same, fully de-spaced (catches "i g n o r e" / zero-width
    #                  splits inside a single word)
    lowered = text.lower()
    norm = _normalize_for_scan(text)
    norm_spaced = " ".join(norm.split())
    norm_nospace = re.sub(r"\s+", "", norm)
    return any(
        p in lowered or p in norm_spaced or p.replace(" ", "") in norm_nospace
        for p in _INJECTION_PATTERNS
    )


def reset_state() -> None:
    """Test helper: clear in-memory windows."""
    _ip_hits.clear()
    _last_message_at.clear()
