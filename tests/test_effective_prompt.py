"""Read-only effective-prompt endpoint: the admin can SEE the whole prompt the
model receives, assembled from prompts.py (the single source of truth). The
prompt is NOT editable from the admin — there is no PUT, no versioning."""
from __future__ import annotations

import json

import db
import prompts
import settings
from api import admin

# A caller with GLOBAL admin reach (the shape require_admin builds).
_ADMIN = {
    "role": "admin", "email": "boss@nowplix.com",
    "memberships": [{"id": 1, "email": "boss@nowplix.com", "scope_type": "global",
                     "partner_id": None, "product_id": None, "role": "admin"}],
}

_PRODUCT = {"id": 1, "partner_id": 1, "slug": "default",
            "name": "Default product", "active": True}


def _stub_product(monkeypatch):
    async def get_default_product():
        return dict(_PRODUCT)

    async def get_product(product_id):
        return dict(_PRODUCT) if product_id == 1 else None

    monkeypatch.setattr(db, "get_default_product", get_default_product)
    monkeypatch.setattr(db, "get_product", get_product)


async def test_effective_prompt_renders_all_layers(monkeypatch):
    async def _list_topics(product_id):
        return [
            {"id": 1, "slug": "other", "title": {"ru": "Другое", "en": "Other"}},
            {"id": 2, "slug": "deposits", "title": {"ru": "Депозиты", "en": "Deposits"}},
            {"id": 3, "slug": "bonuses", "title": {"ru": "Бонусы", "en": "Bonuses"}},
        ]

    async def _kb_content(topic_id, lang="ru"):
        return "Q: Как пополнить счёт?\nA: Через кассу." if topic_id == 2 else None

    async def _kb_variables_map(product_id=None):
        return {}

    _stub_product(monkeypatch)
    monkeypatch.setattr(db, "list_topics", _list_topics)
    monkeypatch.setattr(db, "get_kb_content", _kb_content)
    monkeypatch.setattr(db, "get_kb_variables_map", _kb_variables_map)

    resp = await admin.get_effective_prompt(admin=_ADMIN)
    pv = json.loads(resp.body)["effective_preview"]

    # Layer 1 (the byte-stable core + every STATIC directive) + Layer 2 (the chosen
    # topic's KB) are in the system message; 'other' is skipped for a specialized one.
    assert pv["system"].startswith(prompts.get_system_core())
    assert "KNOWLEDGE BASE" in pv["system"]
    assert "Как пополнить счёт" in pv["system"]  # KB content (data) is whatever lang it's in
    assert pv["example"]["topic"] == "Deposits"  # localized to the default lang
    # Static behavioural directives ride in the cached Layer-1 system block.
    assert "FORMATTING:" in pv["system"]                      # formatting directive
    assert "KNOWLEDGE-BASE GROUNDING:" in pv["system"]  # KB-grounding directive
    assert "Escalation is a last resort" in pv["system"]       # escalation restraint
    assert "SUGGESTED QUESTIONS:" in pv["system"]              # suggested questions

    # Per-request (dynamic) directives + recency guardrails live in the user message.
    user = pv["user"]
    assert "TOPIC ROUTING" in user               # topic routing
    assert "FORBIDDEN TOPICS" in user            # forbidden topics (from the file)
    # The preview player comes from the Test-sandbox profile (the single source of
    # the test player), not a separate hard-coded preview user. With an empty
    # settings cache that resolves to the default profile ("Test Player").
    assert "Test" in user                        # test-sandbox player personalization


async def test_effective_prompt_resilient_when_topics_unavailable(monkeypatch):
    # If topic/KB loading fails the preview must still render Layer 1 + Layer 3.
    async def _boom(*a, **k):
        raise RuntimeError("db down")

    _stub_product(monkeypatch)
    monkeypatch.setattr(db, "list_topics", _boom)

    resp = await admin.get_effective_prompt(admin=_ADMIN)
    pv = json.loads(resp.body)["effective_preview"]
    assert pv["system"].startswith(prompts.get_system_core())
    assert pv["example"]["topic"] is None
    # The escalation-restraint directive is static -> always in the Layer-1 system
    # block, even when topics/KB fail to load.
    assert "Escalation is a last resort" in pv["system"]


async def test_effective_prompt_uses_test_sandbox_player(monkeypatch):
    # The preview player is the admin Test-sandbox profile — the SAME player the
    # chat would use — not a separate hard-coded preview user.
    async def _list_topics(product_id):
        return [{"id": 2, "slug": "deposits", "title": {"en": "Deposits"}}]

    async def _kb_content(topic_id, lang="ru"):
        return None

    async def _kb_variables_map(product_id=None):
        return {}

    _stub_product(monkeypatch)
    monkeypatch.setattr(db, "list_topics", _list_topics)
    monkeypatch.setattr(db, "get_kb_content", _kb_content)
    monkeypatch.setattr(db, "get_kb_variables_map", _kb_variables_map)
    monkeypatch.setattr(settings, "_cache", {"test_profile": {
        "enabled": True, "full_name": "Sandbox Sam", "country": "Portugal"}})

    resp = await admin.get_effective_prompt(admin=_ADMIN)
    user = json.loads(resp.body)["effective_preview"]["user"]
    assert "Sandbox" in user      # the sandbox player's name, personalized
    assert "Portugal" in user     # a sandbox field reaches Layer 3
    # The old hard-coded preview user is gone for good.
    assert "John Smith" not in user
    settings.invalidate()


async def test_effective_prompt_anonymous_when_sandbox_disabled(monkeypatch):
    # Sandbox off -> no invented player data anywhere (anonymous session).
    async def _list_topics(product_id):
        return [{"id": 2, "slug": "deposits", "title": {"en": "Deposits"}}]

    async def _kb_content(topic_id, lang="ru"):
        return None

    async def _kb_variables_map(product_id=None):
        return {}

    _stub_product(monkeypatch)
    monkeypatch.setattr(db, "list_topics", _list_topics)
    monkeypatch.setattr(db, "get_kb_content", _kb_content)
    monkeypatch.setattr(db, "get_kb_variables_map", _kb_variables_map)
    monkeypatch.setattr(settings, "_cache", {"test_profile": {
        "enabled": False, "full_name": "Sandbox Sam"}})

    resp = await admin.get_effective_prompt(admin=_ADMIN)
    user = json.loads(resp.body)["effective_preview"]["user"]
    assert "Sandbox" not in user          # disabled -> name not used
    assert "PERSONALIZATION" not in user  # no personalization for an anon session
    settings.invalidate()


def test_no_prompt_editing_surface_on_admin():
    # The prompt is sourced solely from the file: there must be no edit endpoints.
    for gone in ("put_system_prompt", "get_system_prompt", "put_layer3_prompt",
                 "get_layer3_prompt", "list_prompts", "create_prompt",
                 "publish_prompt", "set_ab", "ab_results"):
        assert not hasattr(admin, gone), f"admin still exposes {gone}"
