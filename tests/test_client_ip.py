"""Client-IP resolution must not trust spoofable XFF from untrusted peers."""
from __future__ import annotations

import types

import config
from api.client_ip import client_ip


def _req(xff=None, peer="10.0.0.1"):
    headers = {}
    if xff is not None:
        headers["x-forwarded-for"] = xff
    client = types.SimpleNamespace(host=peer) if peer else None
    return types.SimpleNamespace(headers=headers, client=client)


def test_ignores_xff_from_untrusted_peer(monkeypatch):
    monkeypatch.setattr(config, "TRUSTED_PROXY_IPS", [])
    monkeypatch.setattr(config, "TRUSTED_PROXY_COUNT", 1)

    ip = client_ip(_req("1.1.1.1, 203.0.113.9", peer="198.51.100.10"))

    assert ip == "198.51.100.10"


def test_spoofed_xff_cannot_rotate_identity_without_trusted_proxy(monkeypatch):
    monkeypatch.setattr(config, "TRUSTED_PROXY_IPS", [])
    monkeypatch.setattr(config, "TRUSTED_PROXY_COUNT", 1)

    a = client_ip(_req("9.9.9.9", peer="198.51.100.10"))
    b = client_ip(_req("8.8.8.8", peer="198.51.100.10"))

    assert a == b == "198.51.100.10"


def test_takes_rightmost_with_configured_trusted_proxy(monkeypatch):
    monkeypatch.setattr(config, "TRUSTED_PROXY_IPS", ["10.0.0.1"])
    monkeypatch.setattr(config, "TRUSTED_PROXY_COUNT", 1)

    ip = client_ip(_req("1.1.1.1, 203.0.113.9", peer="10.0.0.1"))

    assert ip == "203.0.113.9"


def test_honours_multiple_trusted_proxies(monkeypatch):
    monkeypatch.setattr(config, "TRUSTED_PROXY_IPS", ["10.0.0.0/24"])
    monkeypatch.setattr(config, "TRUSTED_PROXY_COUNT", 2)

    ip = client_ip(_req("client, 198.51.100.7, 203.0.113.9", peer="10.0.0.1"))

    assert ip == "198.51.100.7"


def test_falls_back_to_peer_without_xff(monkeypatch):
    monkeypatch.setattr(config, "TRUSTED_PROXY_IPS", ["10.0.0.0/24"])
    monkeypatch.setattr(config, "TRUSTED_PROXY_COUNT", 1)

    assert client_ip(_req(xff=None, peer="10.0.0.5")) == "10.0.0.5"
