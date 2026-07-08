"""Retention KB as ONE text document + the retention effective-prompt preview.

The admin edits the retention KB like a support topic's KB text (one field:
paste, change, save). Storage stays the retention_kb table: the document is a
single row with the sentinel title; legacy structured entries still render and
are folded into the document on the first save. The Prompt preview tab mirrors
the support effective-prompt endpoint and lists only the prompt variables the
retention templates actually use.
"""
from __future__ import annotations

import json

import db
import prompts
from api import retention as api_retention

# A caller with GLOBAL admin reach (the shape require_admin builds).
_ADMIN = {
    "role": "admin", "email": "boss@nowplix.com",
    "memberships": [{"id": 1, "email": "boss@nowplix.com", "scope_type": "global",
                     "partner_id": None, "product_id": None, "role": "admin"}],
}

_DOC_ENTRY = {
    "id": 1, "product_id": 1, "title": db.RETENTION_KB_DOC_TITLE,
    "trigger_when": None, "body": "## Hello\nBe warm.", "links": [],
    "sort_order": 0, "active": True,
}
_LEGACY_ENTRIES = [
    {"id": 1, "product_id": 1, "title": "Welcome back",
     "trigger_when": "player returns", "body": "Greet warmly.",
     "links": ["promotions page"], "sort_order": 0, "active": True},
    {"id": 2, "product_id": 1, "title": "Photos",
     "trigger_when": None, "body": "Only from candidates.", "links": [],
     "sort_order": 1, "active": True},
]


def _patch_entries(monkeypatch, entries):
    async def _list(product_id, *, active_only=False):
        return [dict(e) for e in entries
                if not active_only or e.get("active")]

    monkeypatch.setattr(db, "list_retention_kb", _list)


async def test_kb_text_single_document_roundtrip_shape(monkeypatch):
    _patch_entries(monkeypatch, [_DOC_ENTRY])
    # The document body comes back verbatim (no sentinel title leaking)...
    assert await db.get_retention_kb_text(1) == "## Hello\nBe warm."
    # ...and the prompt block is the body itself, without a "## __…__" header.
    block = await db.retention_kb_block(1)
    assert block == "## Hello\nBe warm."
    assert db.RETENTION_KB_DOC_TITLE not in block


async def test_kb_text_folds_legacy_entries(monkeypatch):
    """Old structured entries render into the same text the prompt received,
    so nothing is lost when the owner first opens the one-field editor."""
    _patch_entries(monkeypatch, _LEGACY_ENTRIES)
    text = await db.get_retention_kb_text(1)
    assert "## Welcome back" in text
    assert "When: player returns" in text
    assert "Links: promotions page" in text
    assert "## Photos" in text
    # The prompt block for legacy entries is unchanged.
    assert await db.retention_kb_block(1) == text


def test_retention_prompt_variable_keys_subset():
    keys = prompts.retention_prompt_variable_keys()
    registered = [k for k, _d, _v, _i in prompts.RETENTION_PROMPT_VARIABLES]
    # Only retention-registry keys, in registry order.
    assert keys == [k for k in registered if k in keys]
    # The retention templates use the persona/brand set (via inheritance from
    # the base placeholders) + their OWN tone...
    for expected in ("retention_persona_name", "retention_brand_name",
                     "retention_products", "retention_tone_of_voice"):
        assert expected in keys
    # ...but never the support-only keys (scope list / support tone).
    assert "support_scope" not in keys
    assert "tone_of_voice" not in keys


async def test_retention_effective_prompt_renders_all_layers(monkeypatch):
    async def _kb_block(product_id):
        return "## Reasons to come back\nNew games appear regularly."

    async def _kb_vars(product_id=None):
        return {}

    async def _get_product(product_id):
        return {"id": 1, "partner_id": 1, "slug": "default",
                "name": "Default product", "active": True}

    monkeypatch.setattr(db, "retention_kb_block", _kb_block)
    monkeypatch.setattr(db, "get_kb_variables_map", _kb_vars)
    monkeypatch.setattr(db, "get_product", _get_product)

    resp = await api_retention.retention_effective_prompt(product_id=1,
                                                          admin=_ADMIN)
    payload = json.loads(resp.body)
    pv = payload["effective_preview"]

    # Layer 1 (retention core) + Layer 2 (the KB document) in the system message.
    assert pv["system"].startswith(prompts.get_retention_system_core())
    assert "RETENTION KNOWLEDGE BASE" in pv["system"]
    assert "Reasons to come back" in pv["system"]
    # Layer 3 carries the photo-candidate block and the recency guardrails.
    assert "PHOTO CANDIDATES" in pv["user"]
    assert "CONSTRAINTS" in pv["user"]
    # Only retention-relevant variables are listed. `value` is the raw override
    # (empty = inherited), `resolved` is what the prompt actually renders with.
    keys = [v["key"] for v in payload["variables"]]
    assert keys == prompts.retention_prompt_variable_keys()
    assert all(v["resolved"] for v in payload["variables"])
    # With no overrides stored, the inherit-keys resolve to the support values.
    by_key = {v["key"]: v for v in payload["variables"]}
    assert by_key["retention_persona_name"]["resolved"] == "Nika"
    assert by_key["retention_persona_name"]["value"] == ""
