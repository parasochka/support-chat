"""Layer-1 core as editable sections: composing defaults stays byte-stable, and
overrides compose + resolve correctly."""
from __future__ import annotations

import json

import db
import prompts
import settings
from api import admin


def test_default_sections_compose_to_byte_stable_core():
    # The whole point: editing through sections must not silently shift the
    # cached prefix. Composing the shipped defaults reproduces SYSTEM_CORE.
    assert prompts.compose_core(prompts.default_sections()) == prompts.SYSTEM_CORE


def test_section_meta_matches_keys_and_order():
    meta = prompts.section_meta()
    assert [m["key"] for m in meta] == list(prompts.SECTION_KEYS)
    assert all(m["label"] for m in meta)


def test_compose_applies_override_in_place():
    sections = prompts.default_sections()
    sections["intro"] = "Совершенно новый тон общения."
    core = prompts.compose_core(sections)
    assert core.startswith("Совершенно новый тон общения.")
    # Other (untouched) sections still present + in order.
    assert "АБСОЛЮТНЫЕ ПРАВИЛА:" in core
    assert core.index("Совершенно новый") < core.index("АБСОЛЮТНЫЕ ПРАВИЛА:")


def test_compose_blank_or_missing_section_falls_back_to_default():
    sections = {"intro": "   "}  # blank + the rest missing entirely
    assert prompts.compose_core(sections) == prompts.SYSTEM_CORE


def test_compose_ignores_unknown_keys():
    sections = prompts.default_sections()
    sections["bogus"] = "should be ignored"
    assert prompts.compose_core(sections) == prompts.SYSTEM_CORE


def test_settings_system_prompt_merges_override_over_defaults():
    settings.invalidate()
    try:
        settings._cache["system_prompt"] = {"sections": {"style": "Будь лаконичен."}}
        resolved = settings.system_prompt()
        assert resolved["style"] == "Будь лаконичен."
        # all other keys retained from defaults
        assert set(resolved) == set(prompts.SECTION_KEYS)
        assert resolved["intro"] == prompts.default_sections()["intro"]
    finally:
        settings.invalidate()


def test_settings_system_prompt_ignores_blank_and_unknown():
    settings.invalidate()
    try:
        settings._cache["system_prompt"] = {
            "sections": {"style": "  ", "bogus": "x"}}
        resolved = settings.system_prompt()
        assert resolved["style"] == prompts.default_sections()["style"]
        assert "bogus" not in resolved
    finally:
        settings.invalidate()


async def test_put_system_prompt_publishes_composed_core(monkeypatch):
    stored: dict = {}
    created: dict = {}

    async def _set_setting(key, value, updated_by=None):
        stored[key] = value

    async def _reload():
        settings._cache.clear()
        settings._cache.update(stored)

    async def _create(name, body, status="draft", is_default=False):
        created.update(name=name, body=body, status=status)
        return 77

    async def _publish(vid):
        created["published"] = vid
        return {"id": vid}

    async def _log(sid, type_, payload=None):
        created.setdefault("events", []).append((type_, payload))

    monkeypatch.setattr(db, "set_setting", _set_setting)
    monkeypatch.setattr(settings, "reload", _reload)
    monkeypatch.setattr(db, "create_prompt_version", _create)
    monkeypatch.setattr(db, "publish_prompt_version", _publish)
    monkeypatch.setattr(db, "log_admin_event", _log)
    monkeypatch.setattr(admin.prompt_store, "invalidate", lambda: None)

    settings.invalidate()
    try:
        resp = await admin.put_system_prompt(
            admin.SystemPromptWrite(sections={"intro": "Новый тон."}),
            admin={"role": "owner"})
        data = json.loads(resp.body)
        assert data["version_id"] == 77
        assert created["published"] == 77            # created draft was published
        assert created["status"] == "draft"
        assert created["body"].startswith("Новый тон.")   # override applied
        assert "АБСОЛЮТНЫЕ ПРАВИЛА:" in created["body"]    # defaults retained
        assert data["sections"]["intro"] == "Новый тон."
        assert ("system_prompt_updated", {"version_id": 77,
                "sections": ["intro"]}) in created["events"]
    finally:
        settings.invalidate()


async def test_put_system_prompt_rejects_unknown_section(monkeypatch):
    from fastapi import HTTPException
    import pytest

    with pytest.raises(HTTPException) as exc:
        await admin.put_system_prompt(
            admin.SystemPromptWrite(sections={"nope": "x"}),
            admin={"role": "owner"})
    assert exc.value.status_code == 400
