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
    # nothing is dropped and the burst can't spam. The cap is the hot
    # `retention.max_reply_parts` knob (default 3).
    parts = retention._split_reply_parts("a\n\nb\n\nc\n\nd\n\ne")
    assert len(parts) == retention._max_reply_parts() == 3
    assert parts[0] == "a" and "d" in parts[-1] and "e" in parts[-1]
    # Blank-ish input never crashes.
    assert retention._split_reply_parts("") == []


def test_split_reply_parts_cap_is_tunable(monkeypatch):
    # max_reply_parts=1 delivers everything as ONE message; 2 collapses the
    # tail into the second part.
    monkeypatch.setattr(settings, "retention", lambda: {"max_reply_parts": 1})
    assert retention._split_reply_parts("a\n\nb\n\nc") == ["a\n\nb\n\nc"]
    monkeypatch.setattr(settings, "retention", lambda: {"max_reply_parts": 2})
    assert retention._split_reply_parts("a\n\nb\n\nc") == ["a", "b\n\nc"]


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

    async def _thresholds(rid, since, trigger_kind=None):
        return {}
    monkeypatch.setattr(_db, "ping_rule_recently_fired", _recently)
    monkeypatch.setattr(_db, "idle_rule_thresholds_fired_since", _thresholds)

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


async def test_match_rule_never_cascades_down_the_ladder(monkeypatch):
    """Anti-cascade: after the highest rung fired for this silence stretch,
    LOWER rungs must not fire on the next sweeps — per-rule cooldowns alone
    let a 60-days-quiet player receive the whole ladder in reverse (45, 30,
    21, … at min-gap pace). The SAME rung stays re-fireable (its own cooldown
    governs), and the memory resets with the player's next activity (the DB
    helper is keyed on last_active_at)."""
    import db as _db

    async def _recently(rid, rule_id, cooldown_days):
        return False  # per-rule cooldowns all clean — the cascade's precondition

    fired_thresholds: dict[str, int] = {"bot_inactivity": 30}

    async def _thresholds(rid, since, trigger_kind=None):
        return fired_thresholds
    monkeypatch.setattr(_db, "ping_rule_recently_fired", _recently)
    monkeypatch.setattr(_db, "idle_rule_thresholds_fired_since", _thresholds)

    ru = {"id": 1, "vip_level": "", "last_active_at": _iso_days_ago(60)}
    rules = [  # priority DESC — the deepest rung first
        {"id": 10, "trigger_kind": "bot_inactivity", "inactivity_days": 30,
         "vip_tiers": [], "cooldown_days": 45, "priority": 50},
        {"id": 11, "trigger_kind": "bot_inactivity", "inactivity_days": 14,
         "vip_tiers": [], "cooldown_days": 30, "priority": 30},
        {"id": 12, "trigger_kind": "bot_inactivity", "inactivity_days": 7,
         "vip_tiers": [], "cooldown_days": 14, "priority": 20},
    ]
    # The 30-day rung already fired this stretch: 14/7 are suppressed, the
    # 30-day rung itself may re-fire (its own cooldown said it's clean).
    matched = await retention_idle._match_rule(ru, rules)
    assert matched is not None and matched[0]["id"] == 10

    # Another trigger kind is judged on its own ladder, not this one's.
    rules_other = [{"id": 13, "trigger_kind": "no_deposit",
                    "inactivity_days": 7, "vip_tiers": [],
                    "cooldown_days": 14, "priority": 10}]
    ru2 = dict(ru, last_deposit_at=_iso_days_ago(20))
    matched = await retention_idle._match_rule(ru2, rules_other)
    assert matched is not None and matched[0]["id"] == 13

    # Player wrote again (helper returns nothing for the new stretch) — the
    # whole ladder is eligible from the bottom again.
    fired_thresholds.clear()
    matched = await retention_idle._match_rule(ru, rules)
    assert matched is not None and matched[0]["id"] == 10


async def test_idle_anti_cascade_anchors_on_the_trigger_clock(monkeypatch):
    """Regression: the fired-rung memory must be queried on the SAME clock the
    rung measures idleness on — the casino timestamps for casino_inactivity/
    no_deposit, NOT last_active_at. A player who replies to pings bumps
    last_active_at but not the casino silence; anchoring the memory on
    last_active_at wiped it and let the ladder reverse-cascade to exactly the
    player who is still engaging."""
    import db as _db
    seen = {}

    async def _recently(rid, rule_id, cooldown_days):
        return False  # per-rule cooldowns clean — the cascade's precondition

    async def _thresholds(rid, since, trigger_kind=None):
        seen[trigger_kind] = since
        # The 30-day no_deposit rung fired within the current deposit-silence
        # stretch (so a query on the DEPOSIT clock still sees it).
        return {"no_deposit": 30} if trigger_kind == "no_deposit" else {}
    monkeypatch.setattr(_db, "ping_rule_recently_fired", _recently)
    monkeypatch.setattr(_db, "idle_rule_thresholds_fired_since", _thresholds)

    ru = {"id": 1, "vip_level": "",
          "last_active_at": _iso_days_ago(1),      # replied yesterday
          "last_deposit_at": _iso_days_ago(60)}    # but no deposit in 60 days
    rules = [  # priority DESC — deepest rung first
        {"id": 20, "trigger_kind": "no_deposit", "inactivity_days": 30,
         "vip_tiers": [], "cooldown_days": 45, "priority": 50},
        {"id": 21, "trigger_kind": "no_deposit", "inactivity_days": 14,
         "vip_tiers": [], "cooldown_days": 30, "priority": 30},
        {"id": 22, "trigger_kind": "no_deposit", "inactivity_days": 7,
         "vip_tiers": [], "cooldown_days": 14, "priority": 20},
    ]
    matched = await retention_idle._match_rule(ru, rules)
    # 30-day rung already fired this deposit-silence stretch → 14/7 suppressed
    # (only the 30-day rung itself may re-fire). The OLD code anchored on the
    # 1-day-old last_active_at, saw no fired pings, and fired the 14-day rung.
    assert matched is not None and matched[0]["id"] == 20
    # The anti-cascade memory was queried on the DEPOSIT clock, never on the
    # (freshly-bumped) bot-activity clock.
    assert seen["no_deposit"] == ru["last_deposit_at"]
    assert seen["no_deposit"] != ru["last_active_at"]


def test_starter_idle_rules_are_brand_neutral_english():
    # The production-tuned ladder: light check-ins early, photos as milestones,
    # heartfelt pressure-free reaches as the silence grows.
    days = [r["inactivity_days"] for r in retention_idle.STARTER_IDLE_RULES]
    assert days == sorted(days) == [3, 5, 7, 10, 14, 21, 30, 45, 60]
    for rule in retention_idle.STARTER_IDLE_RULES:
        settings.ensure_english(rule["intent"], "intent")  # raises on non-Latin
        assert "nika" not in (rule["name"] + rule["intent"]).lower()
        # Cooldown must at least cover the gap to the rule's own re-fire so a
        # player can't get the same rung twice in a row too quickly.
        assert rule["cooldown_days"] >= rule["inactivity_days"]
