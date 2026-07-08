"""Starter KB seed (starter_kb.py): the brand-neutral baseline a new product gets.

A freshly created product must start with a WORKING, generic knowledge base and
prompt-variable set — and none of it may leak the original tenant's data: no
brand names, no invented URLs, no per-brand values. These tests pin the content
contract; the DB seeding itself is a thin insert exercised in create_product.
"""
from __future__ import annotations

import re

import db
import settings
import starter_kb
from prompts import PROMPT_VARIABLES

_LANGS = ("en", "ru", "es", "tr", "pt")


def test_starter_topics_structure():
    slugs = [slug for slug, _titles, _content in starter_kb.STARTER_TOPICS]
    # Seven topics mirroring the live picker (same slugs as the widget's
    # TOPIC_EMOJI map); `other` is a normal (never hidden) topic and comes last.
    assert slugs == ["deposits", "withdrawals", "account_kyc", "bonuses",
                     "betting_games", "technical", "other"]
    for slug, titles, content in starter_kb.STARTER_TOPICS:
        assert re.fullmatch(r"[a-z0-9_-]+", slug)
        for lang in _LANGS:
            assert titles.get(lang, "").strip(), f"{slug}: missing {lang} title"
        assert len(content.strip()) > 200, f"{slug}: KB text too thin to be useful"


def test_starter_kb_carries_no_brand_data():
    """No tenant data may leak into the generic baseline (brand, URLs, handles)."""
    for slug, titles, content in starter_kb.STARTER_TOPICS:
        blob = (slug + " " + " ".join(titles.values()) + " " + content).lower()
        assert "nika" not in blob, f"{slug}: brand leak"
        # Links policy: the baseline invents no URLs — the KB points players at
        # site UI locations instead.
        assert "http://" not in blob and "https://" not in blob, f"{slug}: URL leak"
        assert "t.me/" not in blob, f"{slug}: social-handle leak"


def test_starter_kb_has_no_variable_placeholders():
    """Starter texts are self-contained: an accidental {key} would either render
    another product's registry value or leak a literal brace to the player."""
    for slug, _titles, content in starter_kb.STARTER_TOPICS:
        assert not re.search(r"\{[a-z0-9_]+\}", content), f"{slug}: stray placeholder"


def test_default_kb_variables_carry_no_brand_data():
    """The variables registry seeds EVERY product — it must stay brand-neutral."""
    for key, desc, value in db._DEFAULT_KB_VARIABLES:
        blob = f"{key} {desc} {value}".lower()
        assert "nika" not in blob, f"{key}: brand leak"
        assert "nikabet.com" not in blob and "t.me/" not in blob, f"{key}: link leak"


def test_starter_prompt_variables_full_registry_with_brand():
    values = starter_kb.starter_prompt_variables("Lucky Casino")
    assert set(values) == {key for key, _d, _v in PROMPT_VARIABLES}
    assert values["brand_name"] == "Lucky Casino"
    # Every other key carries the template default, so the product layer fully
    # shadows any global (original-tenant) overrides.
    for key, _desc, default in PROMPT_VARIABLES:
        if key != "brand_name":
            assert values[key] == default
    # The seed must be valid by the same rules as an admin write.
    assert settings.validate_prompt_variables(values) == values


def test_starter_prompt_variables_blank_name_falls_back():
    values = starter_kb.starter_prompt_variables("   ")
    defaults = {key: default for key, _d, default in PROMPT_VARIABLES}
    assert values["brand_name"] == defaults["brand_name"]


def test_starter_retention_prompt_variables_full_registry_with_brand():
    """The Telegram persona is a SEPARATE prompt with its own registry, so a
    new product needs its own retention seed — without it the bot resolves to
    the registry defaults (or the original tenant's global overrides) and
    introduces itself under another brand."""
    from prompts import RETENTION_PROMPT_VARIABLES

    values = starter_kb.starter_retention_prompt_variables("Lucky Casino")
    assert set(values) == {key for key, _d, _v, _r in RETENTION_PROMPT_VARIABLES}
    assert values["retention_brand_name"] == "Lucky Casino"
    for key, _desc, default, _renders in RETENTION_PROMPT_VARIABLES:
        if key != "retention_brand_name":
            assert values[key] == default
    # The seed must be valid by the same rules as an admin write.
    assert settings.validate_retention_prompt_variables(values) == values


def test_starter_retention_prompt_variables_blank_name_falls_back():
    from prompts import RETENTION_PROMPT_VARIABLES

    values = starter_kb.starter_retention_prompt_variables("")
    defaults = {key: default for key, _d, default, _r in RETENTION_PROMPT_VARIABLES}
    assert values["retention_brand_name"] == defaults["retention_brand_name"]


# ---------------------------------------------------------------------------
# Starter RETENTION KB — the single document a new product's bot starts with.
# Same contract as the support starter: brand-neutral, English, self-contained.
# ---------------------------------------------------------------------------
def test_starter_retention_kb_carries_no_brand_data():
    blob = starter_kb.STARTER_RETENTION_KB.lower()
    assert "nika" not in blob, "brand leak"
    assert "http://" not in blob and "https://" not in blob, "URL leak"
    assert "t.me/" not in blob, "social-handle leak"


def test_starter_retention_kb_has_no_variable_placeholders():
    assert not re.search(r"\{[a-z0-9_]+\}", starter_kb.STARTER_RETENTION_KB)


def test_starter_retention_kb_is_substantive():
    text = starter_kb.STARTER_RETENTION_KB
    assert len(text.strip()) > 500, "retention starter too thin to be useful"
    # The two behaviours the retention bot must never get wrong ship covered:
    # the route-out list and the responsible-gaming stance.
    lowered = text.lower()
    assert "route out" in lowered or "hand" in lowered
    assert "responsible gaming" in lowered


# ---------------------------------------------------------------------------
# Starter PING-MATRIX rules — the re-engagement ladder every product gets.
# Same brand-neutral contract; plus the ordering invariant the ping worker
# relies on (longest idle window must carry the highest priority).
# ---------------------------------------------------------------------------
_RULES = starter_kb.STARTER_RETENTION_RULES


def test_starter_ping_rules_shape_and_bounds():
    assert len(_RULES) >= 3
    names = [r["name"] for r in _RULES]
    assert len(names) == len(set(names)), "duplicate rule name"
    for r in _RULES:
        assert r["name"].strip()
        # Only bot_inactivity is seeded: the casino triggers stay silent until
        # the partner feeds activity timestamps, so seeding them would create
        # rules that can never fire.
        assert r["trigger_kind"] == "bot_inactivity", f"{r['name']}: casino trigger seeded"
        assert r["action"] in ("message", "photo"), f"{r['name']}: bad action"
        # Same numeric bounds the admin RuleWrite validator enforces.
        assert 1 <= int(r["inactivity_days"]) <= 365, f"{r['name']}: idle out of range"
        assert 0 <= int(r["cooldown_days"]) <= 365, f"{r['name']}: cooldown out of range"
        assert -1000 <= int(r["priority"]) <= 1000, f"{r['name']}: priority out of range"
        assert r["intent"].strip(), f"{r['name']}: empty intent"


def test_starter_ping_rules_priority_climbs_with_idle_window():
    """The worker evaluates rules priority-DESC and takes the FIRST that fires,
    so for a long-idle player the longest-window rule must win — i.e. priority
    must be strictly monotonic with inactivity_days."""
    ordered = sorted(_RULES, key=lambda r: int(r["inactivity_days"]))
    idles = [int(r["inactivity_days"]) for r in ordered]
    prios = [int(r["priority"]) for r in ordered]
    assert idles == sorted(set(idles)), "inactivity windows must be distinct + sorted"
    assert prios == sorted(set(prios)), "priority must climb with the idle window"


def test_starter_ping_rules_carry_no_brand_data():
    """The `intent` hints are shown to the model verbatim — keep them generic."""
    for r in _RULES:
        blob = (r["name"] + " " + r["intent"]).lower()
        assert "nika" not in blob, f"{r['name']}: brand leak"
        assert "http://" not in blob and "https://" not in blob, f"{r['name']}: URL leak"
        assert "t.me/" not in blob, f"{r['name']}: social-handle leak"
        # English-only intents (the model localizes to the player).
        assert not re.search(r"[а-яё]", blob), f"{r['name']}: non-English intent"


def test_starter_ping_rules_keep_the_recommended_starter_set():
    """The three setup-guide rules ship as-is (same names + priorities), so an
    existing product's hand-made copies are matched by name and never doubled."""
    by_name = {r["name"]: r for r in _RULES}
    for name, prio, action in (
        ("Quiet 3 days - check in", 10, "message"),
        ("Quiet 7 days - photo", 20, "photo"),
        ("Quiet 14 days - win back", 30, "message"),
    ):
        assert name in by_name, f"missing recommended rule {name!r}"
        assert int(by_name[name]["priority"]) == prio
        assert by_name[name]["action"] == action
