"""Scenario + template library (DOC-6b): starter packs shape, idempotent
seeding, persona_brief vs verbatim, the ladder-overlap warning (A7)."""
from __future__ import annotations

from app.core import db
from app.retention import scenario_library


def test_starter_packs_shape():
    # Recovery covers the five dormancy cohorts; every step names a channel.
    keys = {j["journey_key"] for j in
            scenario_library.STARTER_JOURNEYS["recovery"]}
    assert keys == {"recovery_d7_soft", "recovery_d10_fs",
                    "recovery_d14_reload", "recovery_d21_weekly",
                    "recovery_d30_final"}
    for pack in scenario_library.STARTER_JOURNEYS.values():
        for j in pack:
            assert j["trigger"].get("type") in ("event", "scheduled")
            for step in j["steps"]:
                assert step.get("channel"), j["journey_key"]
    # Starter briefs are English (model-facing prompt material).
    for pack in scenario_library.STARTER_TEMPLATES.values():
        for tpl in pack:
            assert tpl["intent"].isascii()


async def test_seed_idempotent_never_overwrites_edited(monkeypatch):
    journeys_store: dict = {}
    templates_store: dict = {}

    async def _get_tpl(pid, key):
        return templates_store.get(key)

    async def _upsert_tpl(pid, **kw):
        templates_store[kw["template_key"]] = {**kw,
                                               "is_starter": kw["is_starter"]}
        return templates_store[kw["template_key"]]

    async def _get_j(pid, key, version=None):
        return journeys_store.get(key)

    async def _upsert_j(pid, j, updated_by=None, is_starter=False):
        journeys_store[j["journey_key"]] = {**j, "is_starter": is_starter}
        return journeys_store[j["journey_key"]]

    async def _log(*a, **kw):
        pass

    monkeypatch.setattr(db, "get_template", _get_tpl)
    monkeypatch.setattr(db, "upsert_template", _upsert_tpl)
    monkeypatch.setattr(db, "get_journey", _get_j)
    monkeypatch.setattr(db, "upsert_journey", _upsert_j)
    monkeypatch.setattr(db, "log_admin_event", _log)

    res = await scenario_library.seed_library(1, ["recovery"])
    assert res["seeded_journeys"] == 5 and res["seeded_templates"] == 5
    # Everything seeds as draft + dry-run.
    assert all(j["status"] == "draft" and j["dry_run"] is True
               for j in journeys_store.values())
    # The operator edits one journey (is_starter drops) — reseed leaves it.
    journeys_store["recovery_d7_soft"]["is_starter"] = False
    journeys_store["recovery_d7_soft"]["name"] = "MY EDIT"
    await scenario_library.seed_library(1, ["recovery"])
    assert journeys_store["recovery_d7_soft"]["name"] == "MY EDIT"


async def test_step_intent_modes(monkeypatch):
    async def _get_tpl_brief(pid, key):
        return {"mode": "persona_brief", "intent": "the brief",
                "localizations": {}}

    monkeypatch.setattr(db, "get_template", _get_tpl_brief)
    assert await scenario_library.step_intent(
        1, {"template_key": "x"}) == "the brief"

    async def _get_tpl_verbatim(pid, key):
        return {"mode": "verbatim", "intent": "",
                "localizations": {"en": {"body": "Exact legal text."}}}

    monkeypatch.setattr(db, "get_template", _get_tpl_verbatim)
    out = await scenario_library.step_intent(1, {"template_key": "x"})
    assert "EXACTLY" in out and "Exact legal text." in out

    async def _get_tpl_none(pid, key):
        return None

    monkeypatch.setattr(db, "get_template", _get_tpl_none)
    assert await scenario_library.step_intent(
        1, {"template_key": "x", "intent": "inline"}) == "inline"


def test_ladder_overlap_warning():
    rules = [
        {"id": 1, "name": "Quiet 10 days", "inactivity_days": 10,
         "enabled": True},
        {"id": 2, "name": "Quiet 14 days", "inactivity_days": 14,
         "enabled": True},
        {"id": 3, "name": "Quiet 10 days (off)", "inactivity_days": 10,
         "enabled": False},
    ]
    overlap = scenario_library.ladder_overlap_warning("recovery_d10_fs",
                                                      rules)
    assert [o["rule_id"] for o in overlap] == [1]
    assert scenario_library.ladder_overlap_warning("ftd_d1", rules) == []
