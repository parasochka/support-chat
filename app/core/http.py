"""ONE outbound HTTP client per process, instead of one per request.

Every outbound call in this service used to open its own `httpx.AsyncClient`
for a single request and throw it away:

    async with httpx.AsyncClient(timeout=...) as client:
        resp = await client.post(url, json=payload)

That costs a full TCP + TLS handshake on every call (~100-300ms to
api.telegram.org from Railway) and — the part that does not show up in a
latency graph — a fresh `ssl.SSLContext` per client, which parses the whole
certifi CA bundle. On the Bot API path, which is the single choke point for
EVERY message, typing action, callback answer and photo upload the retention
bot sends, that handshake was paid per message.

A shared client keeps the connections warm and builds the SSL context once.

WHICH CALLS GET IT — the split is by SAFETY, not by caller:

  client()  — pooled, keep-alive. For calls to FIXED, first-party API hosts:
              the Telegram Bot API, Cloudflare Turnstile, Customer.io. One
              hostname per endpoint, one TLS identity, no cookies in the
              protocol.

  The IP-PINNED calls (`player_sync.maybe_pull_profile`,
  `partner_out.post_json`) deliberately DO NOT use it and keep building a
  client per call. They are the SSRF/DNS-rebinding-guarded paths: they connect
  to a literal vetted IP with the real hostname in a `Host:` header and in an
  `extensions={"sni_hostname": ...}` per-request extension. httpcore keys its
  connection pool on the request origin — `(scheme, host, port)`, which for
  those calls is the pinned IP — and applies `sni_hostname` only when a
  connection is ESTABLISHED, never when one is REUSED. So two partner
  hostnames behind one front-end IP (Cloudflare: the common case) would share
  a connection whose certificate was verified for the OTHER partner's
  hostname, and one tenant's bearer token would ride a TLS session
  authenticated to another tenant's host. Those two sites are low-volume (a
  profile pull is TTL-gated per player, a partner webhook fires per offer
  grant), so pooling buys little and would cost a cross-tenant TLS guarantee.
  Do not "finish the migration" by moving them here.

NO base_url, NO default headers, NO cookie reliance, NO follow_redirects: the
Customer.io host is per-product (us/eu), the Telegram path embeds the
per-product bot token, and every `Authorization: Bearer` is a per-product
decrypted secret that must stay on the REQUEST. Redirects stay off (httpx's
default) because following one would leave the vetted host.

TIMEOUTS ARE PER REQUEST. The three sites carry different deadlines — Turnstile
10s, Customer.io 15s, and Telegram's is a per-`TelegramClient` value (the seam a
future per-product knob would land on) — so the shared client holds only a
fallback and every caller passes its own `timeout=`. httpx applies a per-request
timeout over the client default; a construction-time one would silently flatten
all three.

KEEP-ALIVE TRADEOFF, stated plainly: a pooled connection can be closed by the
server between httpcore's readability check and our write, which surfaces as a
transport error the caller already handles (a Telegram send returns None, a
Turnstile check fails open, a Customer.io send is retried by the delivery
queue). The window is microseconds and every HTTP client in the world accepts
it, but the escape hatch is real: HTTP_KEEPALIVE_EXPIRY_SEC=0 turns reuse off
without a code change, leaving only the shared SSL context.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

import httpx

from app.core import config

log = logging.getLogger(__name__)

# (loop, client) — the loop the client's connection pool is bound to, so a
# client built on a dead loop is rebuilt instead of raising "Event loop is
# closed". Production has one loop per process and never hits it; the test
# suite gives every async test its own loop and does.
_client: Optional[tuple[Any, "httpx.AsyncClient"]] = None


def _build() -> "httpx.AsyncClient":
    """Construct the pooled client. Resolves `httpx.AsyncClient` at CALL time.

    Deliberately not an import-time constant: the tests install their transport
    fakes over `httpx.AsyncClient`, and a client built at import time would have
    resolved the real class before any patch ran.
    """
    expiry = max(int(config.HTTP_KEEPALIVE_EXPIRY_SEC), 0)
    limits = httpx.Limits(
        max_connections=config.HTTP_MAX_CONNECTIONS,
        # expiry 0 = the documented escape hatch: no reuse at all, so the only
        # thing still shared is the transport's SSL context.
        max_keepalive_connections=(config.HTTP_MAX_KEEPALIVE if expiry else 0),
        keepalive_expiry=float(expiry),
    )
    return httpx.AsyncClient(limits=limits,
                             timeout=float(config.HTTP_DEFAULT_TIMEOUT_SEC))


def client() -> "httpx.AsyncClient":
    """The process-wide pooled client (built on first use).

    No lock, and none is needed: this function never awaits, so two coroutines
    cannot interleave between the `is None` check and the assignment — unlike
    `db.connect()`, whose bare `if _pool is None` is safe only because it runs
    once at boot.
    """
    global _client
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:  # pragma: no cover - called from sync code
        loop = None
    if _client is not None:
        cached_loop, cached = _client
        if cached_loop is loop and not getattr(cached, "is_closed", False):
            return cached
        # Dead loop or an already-closed client: drop it and rebuild. Not
        # closed here — awaiting a client whose loop is gone is what we are
        # avoiding, and the sockets die with the loop anyway.
        _client = None
    built = _build()
    _client = (loop, built)
    return built


async def aclose() -> None:
    """Close the pooled client (no-op when nothing was ever built).

    Never raises: it is called from the shutdown `finally` of both entry points,
    right next to `db.close()`, and an httpx teardown error must not mask that.
    Mirrors `db.close()` by resetting the global, so a close/re-open cycle works.
    """
    global _client
    current = _client
    _client = None
    if current is None:
        return
    try:
        await current[1].aclose()
    except Exception as exc:  # noqa: BLE001 - shutdown must not fail on teardown
        log.warning("http_client_close_failed error=%s: %s",
                    exc.__class__.__name__, exc)


def reset() -> None:
    """Drop the cached client WITHOUT closing it (test seam).

    The sibling of `settings.invalidate()` / `openai_client.reset()`: production
    code always uses `aclose()`. Tests need a synchronous drop because the
    client they are discarding may be a fake, or may belong to an event loop
    that has already been closed.
    """
    global _client
    _client = None
