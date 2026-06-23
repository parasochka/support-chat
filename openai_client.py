"""Two-key OpenAI client with failover, backoff, per-key concurrency, cost log.

Failover model (from the Greekly pattern):
  - Try the primary key first.
  - If it stays silent for OPENAI_KEY_SWITCH_TIMEOUT_SEC, launch the fallback in
    PARALLEL and take whichever responds first (cancel the loser).
  - If the primary errors hard (auth / quota / invalid key), switch to fallback
    immediately.
  - Log a `key_failover` admin event whenever the fallback is engaged.

Backoff: 429 / Retry-After / timeouts / transient errors are retried with
exponential backoff up to OPENAI_MAX_ATTEMPTS.

Cost accounting: per-call cost computed from token usage via _PRICING and written
to ai_interaction_logs + chat_messages.
"""
from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from typing import Any, Optional

import config
import settings

try:  # native dep may be stubbed in tests
    from openai import AsyncOpenAI
    import openai as _openai_mod
except Exception:  # noqa: BLE001
    AsyncOpenAI = None  # type: ignore
    _openai_mod = None  # type: ignore


# ---------------------------------------------------------------------------
# Pricing — USD per 1,000,000 tokens: (input, cached_input, output)
# GPT-5.4 mini list prices verified 2026-06-23: input $0.75, cached input
# $0.075, output $4.50 per 1M tokens. Re-verify against current OpenAI pricing
# if the model or OpenAI's published rates change.
# ---------------------------------------------------------------------------
_PRICING: dict[str, tuple[float, float, float]] = {
    # model: (input, cached_input, output)  -- USD per 1M tokens
    # GPT-5.4 mini (the live default). Both the alias and the dated snapshot id
    # map to the same prices so cost accounting works whichever is configured.
    "gpt-5.4-mini": (0.75, 0.075, 4.50),
    "gpt-5.4-mini-2026-03-17": (0.75, 0.075, 4.50),
}


@dataclass
class ChatResult:
    text: str
    lang: Optional[str]
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
        # Concurrency + client timeout are bound at construction; a change to
        # them is picked up via openai_client.reset() (called on settings write).
        m = settings.model()
        self._sem = asyncio.Semaphore(int(m["max_concurrent_per_key"]))
        if AsyncOpenAI is not None:
            self.client = AsyncOpenAI(
                api_key=api_key, timeout=m["request_timeout_sec"]
            )
        else:  # pragma: no cover - only when openai missing & not under test stub
            self.client = None

    async def call(self, messages: list[dict[str, str]]) -> Any:
        # model / reasoning effort / verbosity / max tokens / per-call timeout
        # are read live so tuning from the admin panel takes effect without a
        # redeploy. The GPT-5 reasoning family takes `max_completion_tokens`
        # (not `max_tokens`) and does NOT accept `temperature`; reasoning_effort
        # and verbosity are sent only when set (empty ⇒ use the model default),
        # so the owner can disable either from the admin panel if a future model
        # rejects it.
        m = settings.model()
        kwargs: dict[str, Any] = {
            "model": m["model"],
            "messages": messages,
            "max_completion_tokens": int(m["max_output_tokens"]),
            "timeout": m["request_timeout_sec"],
        }
        effort = m.get("reasoning_effort")
        if effort:
            kwargs["reasoning_effort"] = effort
        verbosity = m.get("verbosity")
        if verbosity:
            kwargs["verbosity"] = verbosity
        async with self._sem:
            return await self.client.chat.completions.create(**kwargs)


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
    for attr in ("RateLimitError", "APITimeoutError", "APIConnectionError",
                 "InternalServerError", "APIError"):
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


async def _call_with_backoff(kc: _KeyClient, messages: list[dict[str, str]]) -> Any:
    """One key, with exponential backoff on transient errors."""
    last_exc: Optional[Exception] = None
    max_attempts = int(settings.model()["max_attempts"])
    for attempt in range(max_attempts):
        try:
            return await kc.call(messages)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if _is_hard_error(exc):
                raise  # caller will fail over to the other key immediately
            if not _is_transient(exc) or attempt == max_attempts - 1:
                raise
            delay = _retry_after_seconds(exc)
            if delay is None:
                delay = (2 ** attempt) + random.uniform(0, 0.5)
            await asyncio.sleep(delay)
    if last_exc:
        raise last_exc
    raise RuntimeError("backoff exhausted without result")  # pragma: no cover


def compute_cost(model: str, tokens_in: int, tokens_out: int, cached_in: int) -> float:
    """Cost in USD from token usage. Returns 0.0 for unknown models."""
    pricing = _PRICING.get(model)
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
    def __init__(self) -> None:
        self.primary = _KeyClient("primary", config.OPENAI_API_KEY)
        self.fallback: Optional[_KeyClient] = None
        if config.OPENAI_API_KEY_FALLBACK:
            self.fallback = _KeyClient("fallback", config.OPENAI_API_KEY_FALLBACK)

    async def complete(
        self,
        messages: list[dict[str, str]],
        session_id: Optional[str] = None,
        on_failover: Optional[Any] = None,
    ) -> ChatResult:
        """Run a chat completion with two-key failover + race.

        `on_failover` (optional async callable) is awaited when the fallback is
        engaged, so the caller can log a `key_failover` admin event.
        """
        started = time.monotonic()

        # No fallback configured -> just the primary with backoff.
        if self.fallback is None:
            resp = await _call_with_backoff(self.primary, messages)
            return self._result(resp, self.primary, started)

        primary_task = asyncio.ensure_future(
            _call_with_backoff(self.primary, messages)
        )

        switch_timeout = settings.model()["key_switch_timeout_sec"]
        try:
            done, _ = await asyncio.wait({primary_task}, timeout=switch_timeout)
        except Exception:  # noqa: BLE001
            done = set()

        if primary_task in done:
            exc = primary_task.exception()
            if exc is None:
                return self._result(primary_task.result(), self.primary, started)
            # Primary failed (hard or exhausted). Fail over immediately.
            await self._note_failover(on_failover, session_id, reason="primary_error")
            resp = await _call_with_backoff(self.fallback, messages)
            return self._result(resp, self.fallback, started)

        # Primary still silent after switch timeout -> race the fallback.
        await self._note_failover(on_failover, session_id, reason="switch_timeout")
        fallback_task = asyncio.ensure_future(
            _call_with_backoff(self.fallback, messages)
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
                    return self._result(task.result(), winner, started)
        # Both failed: re-raise the primary's error for clarity.
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
        latency_ms = int((time.monotonic() - started) * 1000)
        return ChatResult(
            text=text,
            lang=None,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cached_in=cached_in,
            model=settings.model()["model"],
            key_used=kc.name,
            latency_ms=latency_ms,
        )


# Lazily-instantiated module singleton.
_client: Optional[OpenAIClient] = None


def get_client() -> OpenAIClient:
    global _client
    if _client is None:
        _client = OpenAIClient()
    return _client


def reset() -> None:
    """Drop the singleton so the next call rebuilds it with fresh settings.

    The model name, sampling, switch timeout and attempt count are read live on
    every call, but the per-key concurrency semaphore and the SDK client's
    base timeout are bound at construction — so when those change in the admin
    panel the client must be rebuilt. The admin settings handler calls this on a
    `model` write. (No effect on the OpenAI-side prefix cache.)
    """
    global _client
    _client = None
