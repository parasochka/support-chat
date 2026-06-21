"""Client-IP resolution must not trust the spoofable left side of XFF."""
from __future__ import annotations

import types

import config
from api import chat as chat_api


def _req(xff=None, peer="10.0.0.1"):
    headers = {}
    if xff is not None:
        headers["x-forwarded-for"] = xff
    client = types.SimpleNamespace(host=peer) if peer else None
    return types.SimpleNamespace(headers=headers, client=client)


def test_takes_rightmost_with_single_trusted_proxy(monkeypatch):
    monkeypatch.setattr(config, "TRUSTED_PROXY_COUNT", 1)
    # Attacker spoofs the left entry; the edge appends the real client on the right.
    ip = chat_api._client_ip(_req("1.1.1.1, 203.0.113.9"))
    assert ip == "203.0.113.9"


def test_spoofed_xff_cannot_rotate_identity(monkeypatch):
    monkeypatch.setattr(config, "TRUSTED_PROXY_COUNT", 1)
    a = chat_api._client_ip(_req("9.9.9.9, 203.0.113.9"))
    b = chat_api._client_ip(_req("8.8.8.8, 203.0.113.9"))
    assert a == b == "203.0.113.9"  # same real client despite different spoofs


def test_honours_multiple_trusted_proxies(monkeypatch):
    monkeypatch.setattr(config, "TRUSTED_PROXY_COUNT", 2)
    ip = chat_api._client_ip(_req("client, 198.51.100.7, 203.0.113.9"))
    assert ip == "198.51.100.7"


def test_falls_back_to_peer_without_xff(monkeypatch):
    monkeypatch.setattr(config, "TRUSTED_PROXY_COUNT", 1)
    assert chat_api._client_ip(_req(xff=None, peer="10.0.0.5")) == "10.0.0.5"
