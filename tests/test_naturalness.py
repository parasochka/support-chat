"""Naturalness mechanics: multi-message reply bursts, fallback-caption
variance, the CTA link in prompt history, global-only settings resolution and
the idle re-engagement rule matching."""
from __future__ import annotations

import datetime as _dt

import prompts
import retention
import retention_idle
import settings


# --- multi-message reply bursts (blank-line split at the send site) ---------
def test_split_reply_parts_shapes():
    assert retention._split_reply_parts("Привет!") == ["Привет!"]
    assert retention._split_reply_parts("Привет!\n\nКак дела?") == [
        "Привет!", "Как дела?"]
    # Single newlines stay inside one message (only a BLANK line splits).
    assert retention._split_reply_parts("line one\nline two") == [
        "line one\nline two"]
    # Bounded: a runaway split collapses the tail into the last part so
    # nothing is dropped and the burst can't spam.
    parts = retention._split_reply_parts("a\n\nb\n\nc\n\nd\n\ne")
    assert len(parts) == retention._MAX_REPLY_PARTS == 3
    assert parts[0] == "a" and "d" in parts[-1] and "e" in parts[-1]
    # Blank-ish input never crashes.
    assert retention._split_reply_parts("") == []


def test_typing_pause_is_bounded():
    for text in ("hi", "a" * 500):
        pause = retention._typing_pause_sec(text)
        assert 0.5 < pause < 5.0


# --- fallback photo caption variance -----------------------------------------
def test_fallback_photo_caption_variants():
    seen = {retention.fallback_photo_caption("en") for _ in range(60)}
    # All three registry variants surface over enough draws.
    assert len(seen) == 3
    assert any("just for you" in s for s in seen)


# --- the CTA link is visible in the retention prompt history ------------------
def test_history_content_shows_attached_link():
    m = {"role": "assistant", "content": "Come spin something fun",
         "link_url": "https://casino.example/slots"}
    rendered = prompts._retention_history_content(m)
    assert "Come spin something fun" in rendered
    assert "https://casino.example/slots" in rendered
    assert "attached a site page button" in rendered
    # No link — the content is untouched.
    assert prompts._retention_history_content(
        {"role": "assistant", "content": "hi"}) == "hi"
    # A player message never gets the note even if a stray field appears.
    assert prompts._retention_history_content(
        {"role": "user", "content": "hi", "link_url": "https://x.example"}
    ) == "hi"


# --- global-only settings never resolve from the product layer ---------------
def test_global_only_fields_ignore_product_layer(monkeypatch):
    monkeypatch.setattr(settings, "_cache",
                        {"retention": {"worker_interval_sec": 120}})
    monkeypatch.setattr(settings, "_product_cache",
                        {7: {"retention": {"worker_interval_sec": 360,
                                           "daily_photo_cap": 3}}})
    grp = settings._group("retention", product_id=7)
    # The per-product knob shadows normally…
    assert grp["daily_photo_cap"] == 3
    # …but the global-only worker cadence resolves from the GLOBAL layer (a
    # stored product override is dead weight the runtime never reads).
    assert grp["worker_interval_sec"] == 120


def test_worker_interval_reads_global_even_in_product_scope(monkeypatch):
    import retention_v2
    import tenancy
    monkeypatch.setattr(settings, "_cache",
                        {"retention": {"worker_interval_sec": 77}})
    monkeypatch.setattr(settings, "_product_cache",
                        {7: {"retention": {"worker_interval_sec": 360}}})
    token = tenancy.set_current_product(7)
    try:
        assert retention_v2.worker_interval_sec() == 77
        # The helper must not clobber the caller's scope (it is also called
        # from admin request handlers).
        assert tenancy.current_product_id() == 7
    finally:
        tenancy.reset_current_product(token)


# --- idle re-engagement rule matching -----------------------------------------
def _iso_days_ago(days: float) -> str:
    return (_dt.datetime.now(_dt.timezone.utc)
            - _dt.timedelta(days=days)).isoformat()


def test_idle_days_for_triggers():
    now = _dt.datetime.now(_dt.timezone.utc)
    ru = {"last_active_at": _iso_days_ago(8),
          "last_login_at": _iso_days_ago(3),
          "last_played_at": _iso_days_ago(5),
          "last_deposit_at": None}
    assert round(retention_idle._idle_days_for(ru, "bot_inactivity", now)) == 8
    # casino_inactivity keys on the FRESHEST casino signal.
    assert round(
        retention_idle._idle_days_for(ru, "casino_inactivity", now)) == 3
    # A rule on casino data never fires when the signal is absent.
    assert retention_idle._idle_days_for(ru, "no_deposit", now) is None
    assert retention_idle._idle_days_for(ru, "unknown", now) is None


async def test_match_rule_priority_tiers_and_cooldown(monkeypatch):
    import db as _db
    fired: list[tuple[int, int]] = []

    async def _recently(rid, rule_id, cooldown_days):
        return (rid, rule_id) in fired
    monkeypatch.setattr(_db, "ping_rule_recently_fired", _recently)

    ru = {"id": 1, "vip_level": "Gold",
          "last_active_at": _iso_days_ago(15)}
    rules = [  # already priority DESC, like db.list_retention_rules returns
        {"id": 2, "trigger_kind": "bot_inactivity", "inactivity_days": 14,
         "vip_tiers": ["platinum"], "cooldown_days": 14, "priority": 20},
        {"id": 3, "trigger_kind": "bot_inactivity", "inactivity_days": 7,
         "vip_tiers": [], "cooldown_days": 14, "priority": 10},
    ]
    # Tier-restricted rule skipped (player is gold), the general one fires.
    matched = await retention_idle._match_rule(ru, rules)
    assert matched is not None and matched[0]["id"] == 3
    assert matched[1] == 15
    # A rule inside its per-player cooldown is skipped.
    fired.append((1, 3))
    assert await retention_idle._match_rule(ru, rules) is None


def test_starter_idle_rules_are_brand_neutral_english():
    assert len(retention_idle.STARTER_IDLE_RULES) == 3
    days = [r["inactivity_days"] for r in retention_idle.STARTER_IDLE_RULES]
    assert days == sorted(days) == [7, 14, 30]
    for rule in retention_idle.STARTER_IDLE_RULES:
        settings.ensure_english(rule["intent"], "intent")  # raises on non-Latin
