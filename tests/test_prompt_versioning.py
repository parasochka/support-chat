"""Prompt versioning + byte-stable core within a version + A/B attribution loader."""
from __future__ import annotations

import prompt_store
import prompts


def test_system_core_constant_unchanged():
    # The Phase 1 byte-stable core constant is still the fallback.
    assert prompts.get_system_core() == prompts.SYSTEM_CORE


def test_build_system_message_uses_supplied_core_byte_stable():
    core = "ТЕСТОВОЕ ЯДРО"
    a = prompts.build_system_message(None, core=core)
    b = prompts.build_system_message(None, core=core)
    assert a == b == core  # identical across calls within a version


def test_build_system_message_appends_kb_after_stable_separator():
    core = "CORE"
    msg = prompts.build_system_message("KB BLOCK", core=core)
    assert msg.startswith("CORE")
    assert "=== БАЗА ЗНАНИЙ (выбранная тема) ===" in msg
    assert msg.endswith("KB BLOCK")


async def test_core_for_version_caches(monkeypatch):
    prompt_store.invalidate()
    calls = []

    async def _get(vid):
        calls.append(vid)
        return {"id": vid, "body": f"BODY-{vid}"}

    monkeypatch.setattr(prompt_store.db, "get_prompt_version", _get)
    first = await prompt_store.core_for_version(7)
    second = await prompt_store.core_for_version(7)
    assert first == second == "BODY-7"
    assert calls == [7]  # second read served from cache


async def test_core_for_version_falls_back_to_constant(monkeypatch):
    prompt_store.invalidate()

    async def _none(vid):
        return None

    monkeypatch.setattr(prompt_store.db, "get_prompt_version", _none)
    assert await prompt_store.core_for_version(None) == prompts.SYSTEM_CORE
    assert await prompt_store.core_for_version(123) == prompts.SYSTEM_CORE
