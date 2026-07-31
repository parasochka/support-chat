"""Scenario + Template Library (DOC-6b): the content that fills the journey
engine.

Templates are structured BRIEFS by default (`persona_brief`): marketing-ops
controls the intent/occasion, the persona writes the final text through the
normal ping stack — the persona contour is never bypassed. `verbatim` mode
(exact copy, e.g. legal wording) exists behind an explicit per-template
choice.

The starter packs (recovery by dormancy cohort, loss recovery, FTD D+1,
weekly rhythm, cashier abandonment) seed as DRAFT + dry-run — nothing
touches a player until the operator reviews, replaces the starter texts and
activates each journey deliberately. Seeding is idempotent: an edited
non-starter row is never overwritten.

Ladder coordination (owner decision, A7): a recovery journey and the idle
ladder overlap on the same quiet days — when the operator activates a
recovery journey, the matching ladder rungs should be disabled from the
admin (the activation endpoint returns a warning listing the overlaps).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from app.core import db

log = logging.getLogger(__name__)


async def step_intent(product_id: int, step: dict[str, Any]) -> str:
    """The persona brief for a journey step: the referenced template's intent
    (persona_brief) or its English body (verbatim wrapped as an exact-text
    instruction); falls back to the step's inline `intent`."""
    key = str(step.get("template_key") or "")
    if key:
        try:
            tpl = await db.get_template(product_id, key)
        except Exception:  # noqa: BLE001
            tpl = None
        if tpl is not None:
            if str(tpl.get("mode")) == "verbatim":
                loc = (tpl.get("localizations") or {}).get("en") or {}
                body = str(loc.get("body") or "").strip()
                if body:
                    return ("Send EXACTLY this text, translated to the "
                            f"player's language if needed: {body}")
            intent = str(tpl.get("intent") or "").strip()
            if intent:
                return intent
    return str(step.get("intent") or "reach out warmly with a short, "
                                     "personal note")


# ---------------------------------------------------------------------------
# Starter packs (brand-neutral English briefs; is_starter marks replaceables)
# ---------------------------------------------------------------------------
STARTER_TEMPLATES: dict[str, list[dict[str, Any]]] = {
    "recovery": [
        {"template_key": "d7_soft", "intent":
            "The player has been quiet about a week. Check in softly and "
            "personally - no bonus, no pressure, just warmth and an easy "
            "question that invites a reply."},
        {"template_key": "d10_fs", "intent":
            "Ten quiet days. Welcome them back with a small gift feeling: "
            "mention the free spins credited to their account (the gift is "
            "real - it was credited before this message) and keep it light."},
        {"template_key": "d14_reload", "intent":
            "Two quiet weeks. Warm, personal win-back: mention the reload "
            "bonus waiting on their account and that you would love to see "
            "them around again. No guilt."},
        {"template_key": "d21_teaser", "intent":
            "Three quiet weeks. Tease what happens on the site this week "
            "(tournaments, the Sunday final) and mention the small gift "
            "credited for their return."},
        {"template_key": "d30_final", "intent":
            "A month of silence - the last warm reach of this cycle. "
            "Heartfelt, zero pressure: the door is open, a comeback gift "
            "is on their account for a few days."},
    ],
    "loss": [
        {"template_key": "loss_mid", "intent":
            "The player had a rough session earlier. Be caring first "
            "(comfort tone, no amounts, no play-push); mention the cashback "
            "credited as a small softener only after the empathy."},
        {"template_key": "loss_high", "intent":
            "The player had a genuinely bad day. Pure empathy and care - "
            "no invitations, no amounts; gently mention the cashback that "
            "was credited, framed as being on their side."},
    ],
    "ftd": [
        {"template_key": "ftd_d1", "intent":
            "The player made their FIRST deposit yesterday. Congratulate "
            "them warmly on getting started, point them to the daily bonus "
            "card as an easy next step, and invite them to come say hi."},
        {"template_key": "ftd_d1_reload", "intent":
            "The player made their first deposit but has not returned. A "
            "small reload bonus was credited as a next-step nudge - mention "
            "it lightly, no pressure."},
    ],
    "weekly": [
        {"template_key": "tournament_start", "intent":
            "The weekly tournament just opened. A short, energetic heads-up "
            "that the week's race has started - only if it genuinely exists "
            "in the knowledge base."},
        {"template_key": "sunday_final", "intent":
            "Sunday final teaser: a warm nudge that the week wraps up "
            "today - only facts from the knowledge base."},
    ],
    "abandonment": [
        {"template_key": "cashier_back", "intent":
            "The player started a deposit and did not finish it. Ask softly "
            "if everything went okay and offer help - NEVER pressure them "
            "to pay, this is a care message, not a sales push."},
    ],
}

STARTER_JOURNEYS: dict[str, list[dict[str, Any]]] = {
    "recovery": [
        {"journey_key": "recovery_d7_soft", "name": "Recovery D7 - soft",
         "trigger": {"type": "scheduled",
                     "match": {"dormancy_cohort": "dormant_d7"}},
         "steps": [{"step_id": 1, "type": "send_message",
                    "channel": "telegram", "template_key": "d7_soft",
                    "priority": 3}]},
        {"journey_key": "recovery_d10_fs", "name": "Recovery D10 - free spins",
         "trigger": {"type": "scheduled",
                     "match": {"dormancy_cohort": "dormant_d10"}},
         "steps": [{"step_id": 1, "type": "grant_offer",
                    "channel": "telegram", "offer_key": "no_deposit_fs",
                    "template_key": "d10_fs", "priority": 3}]},
        {"journey_key": "recovery_d14_reload", "name": "Recovery D14 - reload",
         "trigger": {"type": "scheduled",
                     "match": {"dormancy_cohort": "dormant_d14"}},
         "steps": [{"step_id": 1, "type": "grant_offer",
                    "channel": "telegram", "offer_key": "reload_bonus",
                    "template_key": "d14_reload", "priority": 3}]},
        {"journey_key": "recovery_d21_weekly", "name": "Recovery D21 - weekly hook",
         "trigger": {"type": "scheduled",
                     "match": {"dormancy_cohort": "dormant_d21"}},
         "steps": [{"step_id": 1, "type": "grant_offer",
                    "channel": "telegram", "offer_key": "fs_small",
                    "template_key": "d21_teaser", "priority": 4}]},
        {"journey_key": "recovery_d30_final", "name": "Recovery D30 - final",
         "trigger": {"type": "scheduled",
                     "match": {"dormancy_cohort": "dormant_d30"}},
         "steps": [{"step_id": 1, "type": "grant_offer",
                    "channel": "telegram", "offer_key": "final_reload",
                    "template_key": "d30_final", "priority": 4}]},
    ],
    "ftd": [
        {"journey_key": "ftd_d1", "name": "FTD D+1 spine",
         "trigger": {"type": "event", "event_name": "deposit_confirmed"},
         "entry_conditions": [{"field": "lifecycle_stage", "operator": "in",
                               "value": ["d0_onboarding", "d1_7_activation"]}],
         "exit_conditions": [],
         "steps": [
             {"step_id": 1, "type": "send_message", "channel": "telegram",
              "template_key": "ftd_d1", "delay": "24h", "priority": 3},
             {"step_id": 2, "type": "grant_offer", "channel": "telegram",
              "offer_key": "reload_small", "template_key": "ftd_d1_reload",
              "delay": "48h", "priority": 4,
              "conditions": [{"field": "idle_days", "operator": "gte",
                              "value": 2}], "on_skip": "exit"}],
         "metadata": {"exit_on_return": False}},
    ],
    "weekly": [
        {"journey_key": "weekly_tournament_announce",
         "name": "Weekly tournament announce",
         "trigger": {"type": "scheduled", "match": {"day_of_week": "wed"}},
         "steps": [{"step_id": 1, "type": "send_message",
                    "channel": "telegram",
                    "template_key": "tournament_start", "priority": 5}],
         "metadata": {"exit_on_return": False}},
        {"journey_key": "weekly_sunday_final", "name": "Sunday final teaser",
         "trigger": {"type": "scheduled", "match": {"day_of_week": "sun"}},
         "steps": [{"step_id": 1, "type": "send_message",
                    "channel": "telegram", "template_key": "sunday_final",
                    "priority": 5}],
         "metadata": {"exit_on_return": False}},
    ],
    "abandonment": [
        {"journey_key": "cashier_abandonment", "name": "Cashier abandonment",
         "trigger": {"type": "scheduled",
                     "match": {"deposit_initiated_older_than_h": 2}},
         "steps": [{"step_id": 1, "type": "send_message",
                    "channel": "telegram", "template_key": "cashier_back",
                    "priority": 2}],
         "metadata": {"exit_on_return": False}},
    ],
}

# The idle-ladder rungs a recovery journey overlaps with (A7: activating the
# journey should disable the matching rungs — surfaced as an activation
# warning, the operator flips them off).
JOURNEY_LADDER_OVERLAP: dict[str, tuple[int, ...]] = {
    "recovery_d7_soft": (7,),
    "recovery_d10_fs": (10,),
    "recovery_d14_reload": (14,),
    "recovery_d21_weekly": (21,),
    "recovery_d30_final": (30,),
}


async def seed_library(product_id: int, packs: Optional[list[str]] = None, *,
                       updated_by: Optional[str] = None) -> dict[str, int]:
    """Seed the starter journeys + templates (draft / dry-run / is_starter).
    Idempotent: rows the operator edited (is_starter=False after their save)
    are left alone; starter rows are refreshed in place."""
    packs = packs or list(STARTER_JOURNEYS.keys() | STARTER_TEMPLATES.keys())
    seeded_j = seeded_t = 0
    for pack in packs:
        for tpl in STARTER_TEMPLATES.get(pack, []):
            existing = await db.get_template(product_id, tpl["template_key"])
            if existing is not None and not existing.get("is_starter"):
                continue
            await db.upsert_template(
                product_id, template_key=tpl["template_key"],
                type_="telegram", mode="persona_brief",
                intent=tpl["intent"], localizations={}, variables={},
                status="draft", is_starter=True, updated_by=updated_by)
            seeded_t += 1
        for j in STARTER_JOURNEYS.get(pack, []):
            existing = await db.get_journey(product_id, j["journey_key"])
            if existing is not None and not existing.get("is_starter"):
                continue
            await db.upsert_journey(
                product_id,
                {**j, "status": "draft", "dry_run": True},
                updated_by=updated_by, is_starter=True)
            seeded_j += 1
    await db.log_admin_event(None, "scenario_library_seeded",
                             {"packs": packs, "journeys": seeded_j,
                              "templates": seeded_t},
                             product_id=product_id)
    return {"seeded_journeys": seeded_j, "seeded_templates": seeded_t}


def ladder_overlap_warning(journey_key: str,
                           rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The enabled idle-ladder rungs that overlap an activated recovery
    journey (same quiet-days threshold) — the operator should disable them."""
    days = JOURNEY_LADDER_OVERLAP.get(journey_key)
    if not days:
        return []
    return [{"rule_id": r["id"], "name": r.get("name"),
             "inactivity_days": r.get("inactivity_days")}
            for r in rules
            if r.get("enabled") and int(r.get("inactivity_days") or 0) in days]
