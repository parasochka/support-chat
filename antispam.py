"""Layered anti-spam: reCaptcha, rate-limit, cooldown, caps, injection scan.

All thresholds come from config (§3 env). The IP sliding-window and per-session
cooldown use in-memory dicts — fine for Phase 1, but they do NOT span multiple
instances (documented limitation).
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Optional

import httpx

import config

# --- in-memory state --------------------------------------------------------
_ip_hits: dict[str, deque[float]] = defaultdict(deque)
_last_message_at: dict[str, float] = {}


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
    if score is not None and score < config.RECAPTCHA_MIN_SCORE:
        return {"ok": False, "skipped": False, "score": score, "reason": "low_score"}
    return {"ok": True, "skipped": False, "score": score, "reason": "ok"}


# ---------------------------------------------------------------------------
# 3. IP sliding-window rate-limit
# ---------------------------------------------------------------------------
def check_rate_limit(ip: str) -> None:
    """Raise AntiSpamError(429) if the IP exceeded the window allowance."""
    now = time.monotonic()
    window = config.RATE_LIMIT_WINDOW_SEC
    hits = _ip_hits[ip]
    while hits and now - hits[0] > window:
        hits.popleft()
    if len(hits) >= config.RATE_LIMIT_MAX_PER_IP:
        raise AntiSpamError(429, "rate_limited",
                            "Too many requests from this IP; slow down.")
    hits.append(now)


# ---------------------------------------------------------------------------
# 5. Per-session cooldown
# ---------------------------------------------------------------------------
# Above this many tracked sessions we prune stale cooldown entries so the
# in-memory dict cannot grow without bound under churn (one session per visitor).
_COOLDOWN_PRUNE_THRESHOLD = 10_000


def _prune_cooldowns(now: float) -> None:
    cutoff = config.MESSAGE_COOLDOWN_SEC
    stale = [sid for sid, ts in _last_message_at.items() if now - ts > cutoff]
    for sid in stale:
        _last_message_at.pop(sid, None)


def check_cooldown(session_id: str) -> None:
    """Raise AntiSpamError(429) if messages arrive faster than the cooldown."""
    now = time.monotonic()
    if len(_last_message_at) > _COOLDOWN_PRUNE_THRESHOLD:
        _prune_cooldowns(now)
    last = _last_message_at.get(session_id)
    if last is not None and now - last < config.MESSAGE_COOLDOWN_SEC:
        raise AntiSpamError(429, "cooldown",
                            "You're sending messages too quickly; please wait a moment.")
    _last_message_at[session_id] = now


# ---------------------------------------------------------------------------
# 7. Input length cap
# ---------------------------------------------------------------------------
def check_input_length(text: str) -> None:
    if text is None:
        raise AntiSpamError(400, "empty", "Message text is required.")
    if len(text) > config.MAX_INPUT_CHARS:
        raise AntiSpamError(400, "too_long",
                            f"Message exceeds {config.MAX_INPUT_CHARS} characters.")
    if text.strip() == "":
        raise AntiSpamError(400, "empty", "Message text is required.")


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


def scan_injection(text: str) -> bool:
    """Return True if the user message looks like an injection/jailbreak attempt.

    SYSTEM_CORE already hardens against this; we log it for audit but do not
    reject the message outright.
    """
    if not text:
        return False
    lowered = text.lower()
    return any(p in lowered for p in _INJECTION_PATTERNS)


def reset_state() -> None:
    """Test helper: clear in-memory windows."""
    _ip_hits.clear()
    _last_message_at.clear()
