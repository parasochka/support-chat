"""Two-key OpenAI client with failover, backoff, per-key concurrency, cost log.

Failover model (from the Greekly pattern):
  - Try the primary key first.
  - If it stays silent for the purpose's key-switch timeout, launch the fallback
    in PARALLEL and take whichever responds first (cancel the loser).
  - If the primary errors hard (auth / quota / invalid key), switch to fallback
    immediately.
  - Log a `key_failover` admin event whenever the fallback is engaged.

Call purposes: every caller declares WHICH block of the stack it is (see
CALL_PURPOSES), and the timeouts are resolved per purpose from the `model`
settings group. The race above exists to protect a player who is waiting for an
answer; a background pass has nobody waiting, so it ships with the race off (a
switch timeout of 0) — otherwise the second, speculative call bills the same
work twice and its usage cannot even be accounted (it rides in a response that
is never received). Background purposes also get a longer request timeout: a
whole transcript or a multi-MB image does not fit the interactive 30s.

Backoff: 429 / Retry-After / timeouts / transient errors are retried with
exponential backoff up to OPENAI_MAX_ATTEMPTS.

Cost accounting: per-call cost computed from token usage via _PRICING and written
to ai_interaction_logs + chat_messages.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
import weakref
from dataclasses import dataclass
from typing import Any, Optional

from app.core import config
from app.core import settings

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Call purposes — the blocks of the stack that talk to the model. Each one
# resolves its own timeout profile (`settings.model_profile`), so a slow
# background pass can never be measured against the interactive chat's clock:
#   chat   — support widget turns + Telegram bot replies (a player is waiting)
#   agent  — the proactive retention agent: event decisions + ping writing
#   review — the quality-review judge (a whole conversation per call)
#   media  — photo/video cataloguing (vision, multi-MB data URLs)
# The purpose is also stamped on every log line: a background call carries no
# session_id, so without it a `switch_timeout` line named no culprit.
# ---------------------------------------------------------------------------
CALL_PURPOSES = ("chat", "agent", "review", "media")
DEFAULT_PURPOSE = "chat"

try:  # native dep may be stubbed in tests
    from openai import AsyncOpenAI
    import openai as _openai_mod
except Exception:  # noqa: BLE001
    AsyncOpenAI = None  # type: ignore
    _openai_mod = None  # type: ignore


# ---------------------------------------------------------------------------
# Circuit breaker — fail fast during a sustained OpenAI outage
#
# Without this, every request under an outage pays the full
# timeout×attempts×failover cost (~2.5 min worst case) and piles up unbounded
# coroutines behind the per-key semaphore. After N consecutive fully-failed
# completions the breaker OPENS and further completions raise immediately (the
# caller returns the localized nudge in ms) until a cooldown elapses, then one
# HALF-OPEN trial probes recovery. Keyed by key_source ('env' | 'product:<id>')
# so one product's bad key can't trip the breaker for everyone.
# ---------------------------------------------------------------------------
class CircuitOpenError(Exception):
    """Raised by complete() when the breaker is open — no OpenAI call is made."""


class _Breaker:
    __slots__ = ("fails", "opened_at")

    def __init__(self) -> None:
        self.fails = 0
        self.opened_at = 0.0  # monotonic time the breaker last opened; 0 = closed


_breakers: dict[str, _Breaker] = {}


def _breaker_for(source: str) -> _Breaker:
    b = _breakers.get(source)
    if b is None:
        b = _Breaker()
        _breakers[source] = b
    return b


# ---------------------------------------------------------------------------
# Pricing — USD per 1,000,000 tokens: (input, cached_input, output)
# gpt-5-mini list prices verified 2026-06-23: input $0.25, cached input
# $0.025, output $2.00 per 1M tokens. GPT-5.4 mini: input $0.75, cached input
# $0.075, output $4.50 per 1M tokens. Re-verify against current OpenAI pricing
# if the model or OpenAI's published rates change. An unlisted model costs 0 (a
# silent under-count), so add every model the `model` settings group can select.
# ---------------------------------------------------------------------------
_PRICING: dict[str, tuple[float, float, float]] = {
    # model: (input, cached_input, output)  -- USD per 1M tokens
    # For each model both the alias and dated snapshot ids map to the same
    # prices so cost accounting works whichever is configured.
    "gpt-5.5": (5.00, 0.50, 30.00),
    "gpt-5.4": (2.50, 0.25, 15.00),
    "gpt-5.4-mini": (0.75, 0.075, 4.50),
    "gpt-5.4-mini-2026-03-17": (0.75, 0.075, 4.50),
    # gpt-5-mini (the live default).
    "gpt-5-mini": (0.25, 0.025, 2.00),
    "gpt-5-mini-2025-08-07": (0.25, 0.025, 2.00),
}


def _pricing_for_model(model: str) -> Optional[tuple[float, float, float]]:
    pricing = _PRICING.get(model)
    if pricing:
        return pricing
    # OpenAI can return dated snapshot ids (for example
    # `gpt-5.4-mini-2026-03-17`) while admins often configure the stable alias.
    # Strip a trailing -YYYY-MM-DD and price it as the alias when known, so a new
    # snapshot does not silently flatten dashboard costs to $0.
    parts = model.rsplit("-", 3)
    if len(parts) == 4 and all(p.isdigit() for p in parts[1:]):
        return _PRICING.get(parts[0])
    return None


def pricing_for_model(model: str) -> Optional[dict[str, float]]:
    """Public pricing lookup for the admin UI (USD per 1M tokens), or None.

    Powers the admin's token-cost counters (/admin/meta `model_pricing`). Same
    caveat as _PRICING: verify before trusting - prices may be stale.
    """
    p = _pricing_for_model(model)
    if not p:
        return None
    return {"input_per_1m": p[0], "cached_input_per_1m": p[1],
            "output_per_1m": p[2]}


# A reasoning model (the GPT-5 family) can spend the WHOLE output budget on hidden
# reasoning and return an empty visible answer (finish_reason 'length'). When that
# happens we retry the call once with a larger budget — at least
# _MIN_RETRY_OUTPUT_TOKENS, capped at _MAX_RETRY_OUTPUT_TOKENS — so the model has
# room to finish reasoning AND emit the visible answer + control sentinels.
_MIN_RETRY_OUTPUT_TOKENS = 2000
_MAX_RETRY_OUTPUT_TOKENS = 8000


def _is_truncated_empty(resp: Any) -> bool:
    """True when the model returned NO visible text because it hit the token cap.

    Catches the reasoning-model failure where hidden reasoning consumes the entire
    `max_completion_tokens` budget: `finish_reason == 'length'` with empty content.
    Defensive against partial/stub response shapes (returns False on anything it
    cannot read), so it is a no-op for non-reasoning models and test doubles.
    """
    try:
        choice = resp.choices[0]
    except (AttributeError, IndexError, TypeError):
        return False
    content = getattr(getattr(choice, "message", None), "content", None) or ""
    if content.strip():
        return False
    return getattr(choice, "finish_reason", None) == "length"


@dataclass
class ChatResult:
    text: str
    tokens_in: int
    tokens_out: int
    cached_in: int
    model: str
    key_used: str  # 'primary' | 'fallback'
    latency_ms: int


class _KeyClient:
    """Wraps one API key with its own AsyncOpenAI client + concurrency semaphore."""

    def __init__(self, name: str, api_key: str):
        self.name = name
        self.api_key = api_key
        # Token usage of DISCARDED truncated first attempts, keyed by id() of the
        # retry response they were replaced with (plus a weakref so a recycled
        # id() — CPython reuses addresses after GC — can never attribute a stale
        # entry to an unrelated response). _result() pops the entry and folds it
        # into the returned counts, so the tokens the first call burned (the
        # whole budget, billed by OpenAI) are not silently dropped from cost
        # accounting. Entries whose response never reaches _result (a cancelled
        # race loser) are cleared by the size guard below.
        self._pending_extra_usage: dict[int, tuple[Any, tuple[int, int, int]]] = {}
        # Concurrency + the SDK client's DEFAULT timeout are bound at
        # construction; a change to them is picked up via openai_client.reset()
        # (called on settings write). The default is the interactive one — every
        # call overrides it with its purpose's timeout (see `call`).
        m = settings.model()
        self._sem = asyncio.Semaphore(int(m["max_concurrent_per_key"]))
        if AsyncOpenAI is not None:
            self.client = AsyncOpenAI(
                api_key=api_key, timeout=m["request_timeout_sec"]
            )
        else:  # pragma: no cover - only when openai missing & not under test stub
            self.client = None

    async def call(self, messages: list[dict[str, str]],
                   purpose: str = DEFAULT_PURPOSE) -> Any:
        # model / reasoning effort / verbosity / max tokens / per-call timeout
        # are read live so tuning from the admin panel takes effect without a
        # redeploy. The GPT-5 reasoning family takes `max_completion_tokens`
        # (not `max_tokens`) and does NOT accept `temperature`; reasoning_effort
        # and verbosity are sent only when set (empty ⇒ use the model default),
        # so the owner can disable either from the admin panel if a future model
        # rejects it. The timeout is the PURPOSE's (a background call reading a
        # whole transcript needs more than the interactive 30s); passing it
        # per-request overrides the SDK client's construction-time default.
        m = settings.model()
        budget = int(m["max_output_tokens"])
        kwargs: dict[str, Any] = {
            "model": m["model"],
            "messages": messages,
            "max_completion_tokens": budget,
            "timeout": settings.model_profile(purpose, m)["request_timeout_sec"],
            "store": False,
        }
        effort = m.get("reasoning_effort")
        if effort:
            kwargs["reasoning_effort"] = effort
        verbosity = m.get("verbosity")
        if verbosity:
            kwargs["verbosity"] = verbosity
        log.info(
            "openai_request_start key=%s purpose=%s model=%s max_completion_tokens=%s effort=%s verbosity=%s timeout=%s messages=%s",
            self.name, purpose, kwargs["model"], kwargs["max_completion_tokens"],
            kwargs.get("reasoning_effort"), kwargs.get("verbosity"),
            kwargs["timeout"], len(messages),
        )
        async with self._sem:
            resp = await self.client.chat.completions.create(**kwargs)
        # Self-heal a reasoning model that burned the entire budget on hidden
        # reasoning and returned an empty visible answer (finish_reason 'length').
        # Without this the player gets a blank turn (the chat "hangs") and NO
        # control sentinels are emitted — so cross-topic routing ([[TOPIC:slug]])
        # silently dies. Retry ONCE with a larger budget so the answer + tags fit.
        if _is_truncated_empty(resp):
            bumped = min(
                max(budget * 3, _MIN_RETRY_OUTPUT_TOKENS), _MAX_RETRY_OUTPUT_TOKENS
            )
            if bumped > budget:
                log.warning(
                    "openai_empty_truncated_retry key=%s budget=%s bumped=%s "
                    "(reasoning consumed the whole budget; raise the model group's "
                    "max_output_tokens to avoid this retry)",
                    self.name, budget, bumped,
                )
                # The discarded first attempt still billed its tokens (the whole
                # output budget went to hidden reasoning) — remember its usage so
                # _result() can fold it into the returned counts.
                _, first_in, first_out, first_cached = _extract(resp)
                kwargs["max_completion_tokens"] = bumped
                async with self._sem:
                    resp = await self.client.chat.completions.create(**kwargs)
                while len(self._pending_extra_usage) > 32:
                    # Orphaned entries (responses that never reached _result,
                    # e.g. a cancelled race loser) must not accumulate forever.
                    # Evict oldest-first so a concurrent turn's still-in-flight
                    # entry isn't wiped wholesale.
                    self._pending_extra_usage.pop(
                        next(iter(self._pending_extra_usage)), None
                    )
                try:
                    ref = weakref.ref(resp)
                except TypeError:  # object not weakref-able: keep it alive
                    ref = (lambda obj: (lambda: obj))(resp)
                self._pending_extra_usage[id(resp)] = (
                    ref, (first_in, first_out, first_cached)
                )
        return resp


def _is_hard_error(exc: Exception) -> bool:
    """Auth / quota / invalid-key errors: no point retrying the same key."""
    if _openai_mod is None:
        return False
    hard_types = []
    for attr in ("AuthenticationError", "PermissionDeniedError", "NotFoundError"):
        t = getattr(_openai_mod, attr, None)
        if t is not None:
            hard_types.append(t)
    return isinstance(exc, tuple(hard_types)) if hard_types else False


def _is_transient(exc: Exception) -> bool:
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return True
    if _openai_mod is None:
        return True  # be lenient if SDK introspection unavailable
    transient = []
    # Deliberately NOT the SDK base class APIError: in openai-python v1.x it is
    # the parent of essentially every error, including deterministic 4xx
    # (BadRequestError/UnprocessableEntityError/context_length_exceeded). Listing
    # it here made a permanent 400 (e.g. a model rejecting a `reasoning_effort`/
    # `verbosity` value, or a context overflow) look transient, so it was retried
    # max_attempts times AND failed over to the 2nd key — ~2*max_attempts wasted
    # calls plus a spurious key_failover event per turn. Only the genuinely
    # retryable classes belong here.
    for attr in ("RateLimitError", "APITimeoutError", "APIConnectionError",
                 "InternalServerError"):
        t = getattr(_openai_mod, attr, None)
        if t is not None:
            transient.append(t)
    return isinstance(exc, tuple(transient)) if transient else False


def _retry_after_seconds(exc: Exception) -> Optional[float]:
    resp = getattr(exc, "response", None)
    if resp is not None:
        headers = getattr(resp, "headers", None)
        if headers:
            ra = headers.get("retry-after") or headers.get("Retry-After")
            if ra:
                try:
                    return float(ra)
                except ValueError:
                    return None
    return None


async def _call_with_backoff(kc: _KeyClient, messages: list[dict[str, str]],
                             purpose: str = DEFAULT_PURPOSE) -> Any:
    """One key, with exponential backoff on transient errors."""
    last_exc: Optional[Exception] = None
    max_attempts = int(settings.model()["max_attempts"])
    for attempt in range(max_attempts):
        try:
            resp = await kc.call(messages, purpose)
            log.info("openai_request_success key=%s purpose=%s attempt=%s",
                     kc.name, purpose, attempt + 1)
            return resp
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            log.warning(
                "openai_request_error key=%s purpose=%s attempt=%s/%s error_type=%s error=%s",
                kc.name, purpose, attempt + 1, max_attempts,
                exc.__class__.__name__, exc,
            )
            if _is_hard_error(exc):
                log.warning("openai_request_hard_error key=%s", kc.name)
                raise  # caller will fail over to the other key immediately
            if not _is_transient(exc) or attempt == max_attempts - 1:
                log.warning("openai_request_exhausted key=%s attempts=%s", kc.name, attempt + 1)
                raise
            delay = _retry_after_seconds(exc)
            if delay is None:
                delay = (2 ** attempt) + random.uniform(0, 0.5)
            log.info("openai_request_retrying key=%s delay_sec=%.2f", kc.name, delay)
            await asyncio.sleep(delay)
    if last_exc:
        raise last_exc
    raise RuntimeError("backoff exhausted without result")  # pragma: no cover


def compute_cost(model: str, tokens_in: int, tokens_out: int, cached_in: int) -> float:
    """Cost in USD from token usage. Returns 0.0 for unknown models."""
    pricing = _pricing_for_model(model)
    if not pricing:
        return 0.0
    in_price, cached_price, out_price = pricing
    fresh_in = max(tokens_in - cached_in, 0)
    cost = (
        fresh_in * in_price
        + cached_in * cached_price
        + tokens_out * out_price
    ) / 1_000_000
    return round(cost, 6)


def _extract(resp: Any) -> tuple[str, int, int, int]:
    """Pull text + token usage out of a chat.completions response object."""
    text = resp.choices[0].message.content or ""
    usage = getattr(resp, "usage", None)
    tokens_in = getattr(usage, "prompt_tokens", 0) or 0
    tokens_out = getattr(usage, "completion_tokens", 0) or 0
    cached_in = 0
    details = getattr(usage, "prompt_tokens_details", None)
    if details is not None:
        cached_in = getattr(details, "cached_tokens", 0) or 0
    return text, tokens_in, tokens_out, cached_in


class OpenAIClient:
    # Class-level default so directly-constructed test doubles (built via
    # __new__ without __init__) still carry a label for the log lines.
    key_source = "env"

    def __init__(self, primary_key: Optional[str] = None,
                 fallback_key: Optional[str] = None,
                 key_source: str = "env") -> None:
        """Two-key client. With no explicit keys it binds the deploy env keys
        (the pre-tenancy behaviour); `client_for_product` passes a product's own
        decrypted keys instead. `key_source` only labels log lines."""
        self.key_source = key_source
        primary = primary_key or config.OPENAI_API_KEY
        fallback = fallback_key if primary_key else config.OPENAI_API_KEY_FALLBACK
        self.primary = _KeyClient("primary", primary)
        self.fallback: Optional[_KeyClient] = None
        if fallback:
            self.fallback = _KeyClient("fallback", fallback)

    async def complete(
        self,
        messages: list[dict[str, str]],
        session_id: Optional[str] = None,
        on_failover: Optional[Any] = None,
        purpose: str = DEFAULT_PURPOSE,
    ) -> ChatResult:
        """Two-key completion, guarded by the circuit breaker (fail fast on outage).

        `purpose` names the calling block (CALL_PURPOSES) and selects the timeout
        profile — interactive by default, so an unmarked caller keeps the old
        behaviour.

        When the breaker for this client's key_source is open, raise
        CircuitOpenError immediately instead of paying the full failover cost;
        chat_service treats any completion exception as a transient failure and
        returns the localized nudge. A success closes the breaker; a failure that
        reaches the threshold opens it.
        """
        threshold = int(config.OPENAI_BREAKER_FAIL_THRESHOLD)
        cooldown = float(config.OPENAI_BREAKER_COOLDOWN_SEC)
        b = _breaker_for(self.key_source)

        if threshold > 0 and b.opened_at:
            remaining = cooldown - (time.monotonic() - b.opened_at)
            if remaining > 0:
                log.warning(
                    "openai_circuit_open key_source=%s purpose=%s session_id=%s "
                    "cooldown_remaining_sec=%.1f",
                    self.key_source, purpose, session_id, remaining,
                )
                raise CircuitOpenError(
                    f"OpenAI circuit open for {self.key_source}; retry shortly"
                )
            log.info("openai_circuit_half_open key_source=%s purpose=%s session_id=%s",
                     self.key_source, purpose, session_id)

        try:
            result = await self._complete_inner(messages, session_id, on_failover,
                                                purpose)
        except Exception:
            if threshold > 0:
                b.fails += 1
                if b.opened_at:
                    # A half-open trial failed — re-arm the cooldown window.
                    b.opened_at = time.monotonic()
                elif b.fails >= threshold:
                    b.opened_at = time.monotonic()
                    log.error(
                        "openai_circuit_opened key_source=%s consecutive_fails=%s "
                        "cooldown_sec=%s",
                        self.key_source, b.fails, cooldown,
                    )
            raise
        if b.fails or b.opened_at:
            log.info("openai_circuit_reset key_source=%s", self.key_source)
        b.fails = 0
        b.opened_at = 0.0
        return result

    async def _complete_inner(
        self,
        messages: list[dict[str, str]],
        session_id: Optional[str] = None,
        on_failover: Optional[Any] = None,
        purpose: str = DEFAULT_PURPOSE,
    ) -> ChatResult:
        """Run a chat completion with two-key failover + race.

        `on_failover` (optional async callable) is awaited when the fallback is
        engaged, so the caller can log a `key_failover` admin event.
        """
        started = time.monotonic()
        log.info(
            "openai_complete_started session_id=%s purpose=%s "
            "fallback_configured=%s key_source=%s",
            session_id, purpose, self.fallback is not None, self.key_source,
        )

        # No fallback configured -> just the primary with backoff.
        if self.fallback is None:
            resp = await _call_with_backoff(self.primary, messages, purpose)
            log.info("openai_complete_primary_only_finished session_id=%s purpose=%s",
                     session_id, purpose)
            return self._result(resp, self.primary, started)

        switch_timeout = settings.model_profile(purpose)["key_switch_timeout_sec"]

        # Race disabled for this purpose (switch timeout 0): nobody is waiting on
        # a background pass, so a speculative second call would only bill the
        # same work twice. The fallback is still engaged — just on a real error.
        if switch_timeout <= 0:
            try:
                resp = await _call_with_backoff(self.primary, messages, purpose)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "openai_complete_primary_error_failover session_id=%s "
                    "purpose=%s error_type=%s error=%s (race disabled)",
                    session_id, purpose, exc.__class__.__name__, exc,
                )
                await self._note_failover(on_failover, session_id,
                                          reason="primary_error")
                resp = await _call_with_backoff(self.fallback, messages, purpose)
                return self._result(resp, self.fallback, started)
            return self._result(resp, self.primary, started)

        primary_task = asyncio.ensure_future(
            _call_with_backoff(self.primary, messages, purpose)
        )

        try:
            done, _ = await asyncio.wait({primary_task}, timeout=switch_timeout)
        except Exception:  # noqa: BLE001
            done = set()

        if primary_task in done:
            exc = primary_task.exception()
            if exc is None:
                return self._result(primary_task.result(), self.primary, started)
            # Primary failed (hard or exhausted). Fail over immediately.
            log.warning(
                "openai_complete_primary_error_failover session_id=%s purpose=%s "
                "error_type=%s error=%s",
                session_id, purpose, exc.__class__.__name__, exc,
            )
            await self._note_failover(on_failover, session_id, reason="primary_error")
            resp = await _call_with_backoff(self.fallback, messages, purpose)
            return self._result(resp, self.fallback, started)

        # Primary still silent after switch timeout -> race the fallback.
        log.warning(
            "openai_complete_switch_timeout session_id=%s purpose=%s timeout_sec=%s",
            session_id, purpose, switch_timeout,
        )
        await self._note_failover(on_failover, session_id, reason="switch_timeout")
        fallback_task = asyncio.ensure_future(
            _call_with_backoff(self.fallback, messages, purpose)
        )
        return await self._race(primary_task, fallback_task, started)

    async def _race(self, primary_task, fallback_task, started) -> ChatResult:
        pending = {primary_task, fallback_task}
        while pending:
            done, pending = await asyncio.wait(
                pending, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                if task.exception() is None:
                    winner = self.primary if task is primary_task else self.fallback
                    loser = fallback_task if task is primary_task else primary_task
                    loser.cancel()
                    # If the loser had ALREADY finished with an exception (both
                    # in this same wait() wakeup), cancel() is a no-op and the
                    # exception is otherwise never retrieved -> asyncio logs a
                    # spurious "Task exception was never retrieved" at GC, which
                    # pollutes the mirrored logs. Consume it harmlessly.
                    loser.add_done_callback(
                        lambda t: t.cancelled() or t.exception())
                    # NB: if the losing request had already been processed
                    # server-side, its tokens are billed by OpenAI but cannot be
                    # accounted here — the usage rides in a response we never
                    # receive. Rare (only after a switch-timeout race) and
                    # bounded to one request; flagged for transparency.
                    log.info(
                        "openai_complete_race_won key=%s "
                        "(loser cancelled; its usage, if any, is unaccounted)",
                        winner.name if winner else None,
                    )
                    return self._result(task.result(), winner, started)
        # Both failed: re-raise the primary's error for clarity.
        log.error("openai_complete_race_failed")
        raise primary_task.exception() or fallback_task.exception()

    @staticmethod
    async def _note_failover(on_failover, session_id, reason: str) -> None:
        if on_failover is not None:
            try:
                await on_failover(session_id, reason)
            except Exception:  # noqa: BLE001 - logging must never break the call
                pass

    def _result(self, resp: Any, kc: _KeyClient, started: float) -> ChatResult:
        text, tokens_in, tokens_out, cached_in = _extract(resp)
        # Fold in the usage of a discarded truncated first attempt (see
        # _KeyClient.call) so cost accounting covers BOTH billed calls.
        extra = getattr(kc, "_pending_extra_usage", None)
        if extra:
            entry = extra.pop(id(resp), None)
            # Only trust the entry when the weakref still points at THIS
            # response object — a recycled id() must not inflate another turn.
            if entry and entry[0]() is resp:
                first = entry[1]
                tokens_in += first[0]
                tokens_out += first[1]
                cached_in += first[2]
        latency_ms = int((time.monotonic() - started) * 1000)
        return ChatResult(
            text=text,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cached_in=cached_in,
            model=settings.model()["model"],
            key_used=kc.name,
            latency_ms=latency_ms,
        )


# Lazily-instantiated clients. The env-keyed singleton keeps the pre-tenancy
# entry point working; the registry caches one client per product whose OWN
# keys are configured (keyed by product id + a key fingerprint, so a rotated
# key rebuilds the client instead of serving the stale one).
_client: Optional[OpenAIClient] = None
_product_clients: dict[tuple[int, str], OpenAIClient] = {}
# Short-TTL cache of the DECRYPTED per-product keys: without it every single
# turn pays a DB round-trip + secretbox decrypt before the model call, even on
# a client-cache hit. reset() (called on every product-secrets write) clears
# it, so a rotation applies within the TTL at worst on another instance and
# immediately on the instance that took the write.
_product_keys_cache: dict[int, tuple[float, Optional[dict]]] = {}
_PRODUCT_KEYS_TTL_SEC = 60.0


def get_client() -> OpenAIClient:
    global _client
    if _client is None:
        _client = OpenAIClient()
    return _client


def _fingerprint(*keys: Optional[str]) -> str:
    import hashlib
    h = hashlib.sha256()
    for k in keys:
        h.update((k or "").encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:16]


async def client_for_product(product_id: Optional[int]) -> OpenAIClient:
    """The client for a product: its own (decrypted) keys, else the env keys.

    Per the commercial model each casino brings its own OpenAI key(s), entered
    in the admin Structure tab and stored encrypted; a product without keys
    falls back to the deploy env keys (dev/transition mode). The two-key
    failover machinery is identical either way.
    """
    if product_id is not None:
        from app.core import db  # lazy: keep module importable without the app wired up
        now = time.monotonic()
        cached = _product_keys_cache.get(product_id)
        if cached is not None and now - cached[0] < _PRODUCT_KEYS_TTL_SEC:
            keys = cached[1]
        else:
            keys = await db.get_product_openai_keys(product_id)
            if len(_product_keys_cache) > 1024:
                _product_keys_cache.clear()
            _product_keys_cache[product_id] = (now, keys)
        if keys and keys.get("primary"):
            cache_key = (product_id, _fingerprint(keys["primary"], keys.get("fallback")))
            client = _product_clients.get(cache_key)
            if client is None:
                # Drop stale entries for the same product (rotated keys).
                for k in [k for k in _product_clients if k[0] == product_id]:
                    _product_clients.pop(k, None)
                client = OpenAIClient(primary_key=keys["primary"],
                                      fallback_key=keys.get("fallback"),
                                      key_source=f"product:{product_id}")
                _product_clients[cache_key] = client
            return client
    return get_client()


def reset() -> None:
    """Drop the cached clients so the next call rebuilds them with fresh settings.

    The model name, sampling, switch timeout and attempt count are read live on
    every call, but the per-key concurrency semaphore and the SDK client's
    base timeout are bound at construction — so when those change in the admin
    panel the clients must be rebuilt. The admin settings handler calls this on
    a `model` write and on a product-keys write. (No effect on the OpenAI-side
    prefix cache.)
    """
    global _client
    _client = None
    _product_clients.clear()
    _product_keys_cache.clear()
    # Clear breaker state too: an operator changing model/keys is an explicit
    # intervention, and the new config deserves a clean probe rather than an
    # inherited open breaker from the prior configuration.
    _breakers.clear()
