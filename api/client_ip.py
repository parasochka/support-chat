"""Trusted client-IP resolution for public rate limits."""
from __future__ import annotations

import ipaddress
from typing import Any

import config


def _peer_host(request: Any) -> str:
    return request.client.host if getattr(request, "client", None) else "unknown"


def _trusted_proxy_nets() -> list[ipaddress._BaseNetwork]:
    nets: list[ipaddress._BaseNetwork] = []
    for raw in getattr(config, "TRUSTED_PROXY_IPS", []):
        try:
            nets.append(ipaddress.ip_network(raw, strict=False))
        except ValueError:
            # Config parsing is intentionally fail-soft here so a bad optional
            # proxy entry cannot make the app trust XFF from everyone.
            continue
    return nets


def _is_trusted_proxy(host: str) -> bool:
    try:
        peer = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(peer in net for net in _trusted_proxy_nets())


def client_ip(request: Any) -> str:
    """Return a rate-limit key that cannot be spoofed with client-supplied XFF.

    Forwarded headers are honored only when the immediate socket peer is one of
    the configured trusted proxies. With no TRUSTED_PROXY_IPS configured, the
    safe default is to use request.client.host.
    """
    peer = _peer_host(request)
    if not _is_trusted_proxy(peer):
        return peer

    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        parts = [p.strip() for p in fwd.split(",") if p.strip()]
        if parts:
            idx = min(max(config.TRUSTED_PROXY_COUNT, 1), len(parts))
            return parts[-idx]
    return peer
