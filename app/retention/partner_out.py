"""Outbound calls TO the casino platform (orchestrator -> partner).

Direction decision (Б2): the offer-grant and the delegated-delivery contracts
are OUR calls to endpoints the PARTNER implements ("partner" = the operator
running a casino on the platform). Each product carries the endpoint URLs
(`offer_grant_url`, `delivery_endpoint_url`, plain config edited in the
admin) and ONE outbound Bearer secret (`partner_out_key`, encrypted at rest
like every product secret).

Every call is SSRF-guarded and DNS-pinned exactly like the Player-API pull
(player_sync.resolve_pinned_outbound): admin-configured URLs must never reach
internal/cloud-metadata addresses, and the vetted resolution is the one the
socket actually connects to.
"""
from __future__ import annotations

import logging
from typing import Any

from app.core import db
from app.retention import player_sync

log = logging.getLogger(__name__)


class PartnerCallError(RuntimeError):
    """The partner endpoint could not be called (config/network/HTTP error).
    `permanent` marks errors a retry cannot fix (bad config, 4xx)."""

    def __init__(self, detail: str, *, permanent: bool = False) -> None:
        super().__init__(detail)
        self.permanent = permanent


async def post_json(product: dict[str, Any], url: str,
                    payload: dict[str, Any], *,
                    timeout_sec: float = 10.0) -> dict[str, Any]:
    """POST a JSON payload to a partner endpoint with the product's outbound
    key as Bearer. Returns the parsed JSON body (a dict). Raises
    PartnerCallError on any failure."""
    import httpx
    url = (url or "").strip()
    if not url:
        raise PartnerCallError("no partner endpoint configured",
                               permanent=True)
    pinned = await player_sync.resolve_pinned_outbound(url)
    if pinned is None:
        raise PartnerCallError("partner endpoint URL is unsafe/unresolvable",
                               permanent=True)
    key = await db.get_product_partner_out_key(int(product["id"]))
    headers = {"Host": pinned["host"]}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    extensions = ({"sni_hostname": pinned["sni"]}
                  if pinned["scheme"] == "https" else {})
    try:
        async with httpx.AsyncClient(timeout=timeout_sec) as client:
            resp = await client.post(pinned["url"], json=payload,
                                     headers=headers, extensions=extensions)
    except Exception as exc:  # noqa: BLE001 - network errors are transient
        raise PartnerCallError(f"partner call failed: "
                               f"{exc.__class__.__name__}")
    if resp.status_code >= 500:
        raise PartnerCallError(f"partner HTTP {resp.status_code}")
    if resp.status_code >= 400:
        raise PartnerCallError(f"partner HTTP {resp.status_code}",
                               permanent=resp.status_code != 429)
    try:
        body = resp.json()
    except ValueError:
        raise PartnerCallError("partner returned non-JSON", permanent=True)
    if not isinstance(body, dict):
        raise PartnerCallError("partner returned a non-object body",
                               permanent=True)
    return body
