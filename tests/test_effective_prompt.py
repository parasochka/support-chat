"""Read-only effective-prompt endpoint: the admin can SEE the whole prompt the
model receives, assembled from prompts.py (the single source of truth). The
prompt is NOT editable from the admin — there is no PUT, no versioning."""
from __future__ import annotations

import json

import db
import prompts
from api import admin


async def test_effective_prompt_renders_all_layers(monkeypatch):
    async def _list_topics(include_hidden=False):
        return [
            {"id": 1, "slug": "other", "title": {"ru": "Другое", "en": "Other"}},
            {"id": 2, "slug": "deposits", "title": {"ru": "Депозиты", "en": "Deposits"}},
            {"id": 3, "slug": "bonuses", "title": {"ru": "Бонусы", "en": "Bonuses"}},
        ]

    async def _kb_content(topic_id, lang="ru"):
        return "Q: Как пополнить счёт?\nA: Через кассу." if topic_id == 2 else None

    async def _kb_variables_map():
        return {}

    monkeypatch.setattr(db, "list_topics", _list_topics)
    monkeypatch.setattr(db, "get_kb_content", _kb_content)
    monkeypatch.setattr(db, "get_kb_variables_map", _kb_variables_map)

    resp = await admin.get_effective_prompt()
    pv = json.loads(resp.body)["effective_preview"]

    # Layer 1 (the byte-stable core + every STATIC directive) + Layer 2 (the chosen
    # topic's KB) are in the system message; 'other' is skipped for a specialized one.
    assert prompts.SYSTEM_CORE in pv["system"]
    assert "KNOWLEDGE BASE" in pv["system"]
    assert "Как пополнить счёт" in pv["system"]  # KB content (data) is whatever lang it's in
    assert pv["example"]["topic"] == "Deposits"  # localized to the default lang
    # Static behavioural directives ride in the cached Layer-1 system block.
    assert "Formatting:" in pv["system"]                      # formatting directive
    assert "Grounding in the knowledge base:" in pv["system"]  # KB-grounding directive
    assert "Escalation is a last resort" in pv["system"]       # escalation restraint
    assert "Suggested questions:" in pv["system"]              # suggested questions

    # Per-request (dynamic) directives + recency guardrails live in the user message.
    user = pv["user"]
    assert "TOPIC ROUTING" in user               # topic routing
    assert "Forbidden topics" in user            # forbidden topics (from the file)
    assert "John" in user                        # sample player personalization


async def test_effective_prompt_resilient_when_topics_unavailable(monkeypatch):
    # If topic/KB loading fails the preview must still render Layer 1 + Layer 3.
    async def _boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(db, "list_topics", _boom)

    resp = await admin.get_effective_prompt()
    pv = json.loads(resp.body)["effective_preview"]
    assert prompts.SYSTEM_CORE in pv["system"]
    assert pv["example"]["topic"] is None
    # The escalation-restraint directive is static -> always in the Layer-1 system
    # block, even when topics/KB fail to load.
    assert "Escalation is a last resort" in pv["system"]


def test_no_prompt_editing_surface_on_admin():
    # The prompt is sourced solely from the file: there must be no edit endpoints.
    for gone in ("put_system_prompt", "get_system_prompt", "put_layer3_prompt",
                 "get_layer3_prompt", "list_prompts", "create_prompt",
                 "publish_prompt", "set_ab", "ab_results"):
        assert not hasattr(admin, gone), f"admin still exposes {gone}"
