"""SSRF guard for the admin-configured Player API URL (player_sync).

player_api_url is set by a product-scoped admin (semi-trusted), so the guard must
reject any host that resolves to a non-global address (cloud metadata, RFC1918,
loopback, link-local, multicast) — including a rebind that mixes public + private
records — and the pinned-resolution helper must connect to the literal vetted IP
while preserving the original host for Host/SNI. No test covered this before; a
refactor could silently reopen the hole.
"""
from __future__ import annotations

import socket

import player_sync
import pytest


def _fake_getaddrinfo(*ips):
    """Return a getaddrinfo replacement yielding one A record per given IP."""
    def _gai(host, port, *a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port or 443))
                for ip in ips]
    return _gai


def _raise_gai(*a, **k):
    raise socket.gaierror("name resolution failed")


# --- is_safe_outbound_url --------------------------------------------------
async def test_rejects_non_http_scheme(monkeypatch):
    monkeypatch.setattr(player_sync.socket, "getaddrinfo", _fake_getaddrinfo("8.8.8.8"))
    assert await player_sync.is_safe_outbound_url("ftp://example.com/x") is False
    assert await player_sync.is_safe_outbound_url("file:///etc/passwd") is False


async def test_rejects_missing_host(monkeypatch):
    monkeypatch.setattr(player_sync.socket, "getaddrinfo", _fake_getaddrinfo("8.8.8.8"))
    assert await player_sync.is_safe_outbound_url("http:///nohost") is False


@pytest.mark.parametrize("ip", [
    "127.0.0.1",        # loopback
    "169.254.169.254",  # link-local / cloud metadata
    "10.0.0.5",         # RFC1918
    "192.168.1.10",     # RFC1918
    "172.16.5.5",       # RFC1918
    "224.0.0.1",        # multicast
    "0.0.0.0",          # unspecified / non-global
])
async def test_rejects_non_global_resolution(monkeypatch, ip):
    monkeypatch.setattr(player_sync.socket, "getaddrinfo", _fake_getaddrinfo(ip))
    assert await player_sync.is_safe_outbound_url(f"https://evil.example/{ip}") is False


async def test_rejects_mixed_public_and_private(monkeypatch):
    # DNS-rebind trick: answer with one public and one private record.
    monkeypatch.setattr(player_sync.socket, "getaddrinfo",
                        _fake_getaddrinfo("8.8.8.8", "169.254.169.254"))
    assert await player_sync.is_safe_outbound_url("https://rebind.example/") is False


async def test_allows_public_resolution(monkeypatch):
    monkeypatch.setattr(player_sync.socket, "getaddrinfo", _fake_getaddrinfo("8.8.8.8"))
    assert await player_sync.is_safe_outbound_url("https://api.partner.example/v1") is True


async def test_resolution_failure_is_unsafe(monkeypatch):
    monkeypatch.setattr(player_sync.socket, "getaddrinfo", _raise_gai)
    assert await player_sync.is_safe_outbound_url("https://nope.example/") is False


# --- resolve_pinned_outbound ----------------------------------------------
async def test_pin_uses_literal_ip_preserves_host(monkeypatch):
    monkeypatch.setattr(player_sync.socket, "getaddrinfo", _fake_getaddrinfo("93.184.216.34"))
    pinned = await player_sync.resolve_pinned_outbound("https://api.partner.example/v1/profile")
    assert pinned is not None
    assert pinned["url"] == "https://93.184.216.34/v1/profile"  # connect to the vetted IP
    assert pinned["host"] == "api.partner.example"              # Host header preserved
    assert pinned["sni"] == "api.partner.example"               # TLS SNI/cert preserved
    assert pinned["scheme"] == "https"


async def test_pin_rejects_private_resolution(monkeypatch):
    monkeypatch.setattr(player_sync.socket, "getaddrinfo", _fake_getaddrinfo("10.1.2.3"))
    assert await player_sync.resolve_pinned_outbound("https://internal.example/") is None


async def test_pin_rejects_mixed_resolution(monkeypatch):
    monkeypatch.setattr(player_sync.socket, "getaddrinfo",
                        _fake_getaddrinfo("93.184.216.34", "127.0.0.1"))
    assert await player_sync.resolve_pinned_outbound("https://rebind.example/") is None
