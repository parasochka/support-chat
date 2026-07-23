"""Retention bot orchestration — the brain between Telegram transport and the AI.

Ties together: the one-time deeplink nonce exchange, the channel-subscription
gate, the entry menu, the AI retention chat (chat_service.handle_retention_message),
photo candidate selection + progression gating, and manager round-robin routing.

Transport (Telegram HTTP) lives in telegram_transport; the AI turn lives in
chat_service; this module holds the retention BUSINESS logic and the flow.
Everything resolves per product (multi-tenant): the product is passed in by the
webhook handler, which resolved it from the bot's webhook routing token.
"""
from __future__ import annotations

import asyncio
import contextlib
import html
import logging
import random
import re
import datetime as _dt
import secrets
import time
import unicodedata
import uuid
from collections import OrderedDict
from typing import Any, Optional
from urllib.parse import urlsplit

import antispam
import chat_service
import config
import db
import language
import player_sync
import prompts
import settings
import telegram_format
import tenancy
import translations
from telegram_transport import (ParsedUpdate, TelegramClient, inline_keyboard,
                                parse_update)

log = logging.getLogger(__name__)


# SSRF guard for admin-configured outbound URLs — the implementation moved to
# player_sync (the unified data-sync module) together with the lazy pull; the
# re-export keeps this module's name (tests and callers monkeypatch/call it here).
is_safe_outbound_url = player_sync.is_safe_outbound_url


@contextlib.asynccontextmanager
async def _typing(client: TelegramClient, chat_id: int):
    """Keep the native Telegram "typing…" indicator alive while the body runs.

    Telegram clears a chat action after ~5s (or on the next message), so the
    task re-sends it on a timer — the player sees a live "печатает…" instead
    of dead silence while the model thinks. Purely cosmetic: any failure is
    swallowed and the reply still goes out.
    """
    async def _loop() -> None:
        while True:
            await client.send_chat_action(chat_id)
            await asyncio.sleep(4.5)

    task = asyncio.create_task(_loop())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


# A model reply may arrive as several short chat messages separated by a BLANK
# line (the persona is told: usually one, sometimes two, rarely three) — real
# people in Telegram send bursts, not paragraphs. Bounds keep a misbehaving
# reply from turning into spam. The cap is the hot `retention.max_reply_parts`
# knob (per-product; 1 = never split).
def _max_reply_parts() -> int:
    try:
        return max(1, int(settings.retention().get("max_reply_parts", 3)))
    except Exception:
        return 3


def _split_reply_parts(text: str) -> list[str]:
    """Split a model reply into its blank-line-separated chat messages.

    At most `retention.max_reply_parts` parts; a long tail collapses into the
    last part so nothing is ever dropped. A reply with no blank lines stays
    whole.
    """
    cap = _max_reply_parts()
    chunks = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    if len(chunks) <= 1:
        return chunks
    if len(chunks) > cap:
        chunks = chunks[:cap - 1] + ["\n\n".join(chunks[cap - 1:])]
    return chunks


def _typing_pause_sec(next_part: str) -> float:
    """A human-ish pause before the NEXT burst message, sized to its length."""
    base = 0.8 + min(len(next_part) * 0.035, 2.6)
    return base + random.uniform(0.0, 0.6)


async def _send_ai_text(client: TelegramClient, chat_id: int, text: str, *,
                        reply_markup: Optional[dict[str, Any]] = None,
                        silent: bool = False) -> bool:
    """Send model-generated retention text with the light HTML markup rendered.

    The retention persona may use a touch of **bold**/*italic*; telegram_format
    converts that to balanced Telegram HTML. If Telegram rejects the HTML for any
    reason, fall back to the plain text so a delivery never silently fails.

    A reply carrying blank lines is delivered as SEPARATE consecutive messages
    (the persona's natural chat burst), with a typing indicator + a short pause
    between them; an inline button (`reply_markup`) always rides on the LAST
    part. Returns True when at least one message reached Telegram.
    """
    parts = _split_reply_parts(text) or [text]
    delivered_any = False
    for i, part in enumerate(parts):
        last = i == len(parts) - 1
        if i > 0:
            with contextlib.suppress(Exception):
                await client.send_chat_action(chat_id)
            await asyncio.sleep(_typing_pause_sec(part))
        part_html = telegram_format.to_html(part)
        result = await client.send_message(
            chat_id, part_html, reply_markup=reply_markup if last else None,
            parse_mode="HTML", disable_notification=silent)
        if result is None and part_html != part:
            result = await client.send_message(
                chat_id, part, reply_markup=reply_markup if last else None,
                disable_notification=silent)
        delivered_any = delivered_any or result is not None
        if result is None and not delivered_any:
            # The first part never made it — bail instead of sending a tail
            # without its head.
            return False
    return delivered_any

# Callback-data constants for the inline menu (short + stable).
CB_CHECK_SUB = "rtn:checksub"
CB_MENU_NIKA = "rtn:nika"
CB_MENU_MANAGER = "rtn:manager"

# A player asking to SEE a photo = reactive (bypasses the proactive cooldown,
# still bounded by the daily cap). Multilingual stems; matched on a normalized
# copy of the message.
_PHOTO_REQUEST_STEMS = (
    "фото", "фотк", "картинк", "покажи", "снимок", "селфи", "пришли",
    "photo", "pic", "picture", "selfie", "show", "send me",
    "foto", "muestra", "envía", "fotoğraf", "göster", "resim",
)
# Word-START matching (not raw substring): "pic" matches "pictures" but never
# "epic"/"topic", so an unrelated word can't accidentally bypass the proactive
# photo cooldown (the daily cap holds regardless).
_PHOTO_REQUEST_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(s) for s in _PHOTO_REQUEST_STEMS) + r")")


# ---------------------------------------------------------------------------
# Deeplink / nonce
# ---------------------------------------------------------------------------
async def create_deeplink(product: dict[str, Any], handshake_context: dict[str, Any],
                          escalation: bool,
                          lang: Optional[str] = None) -> dict[str, str]:
    """Mint a nonce for a player's handshake and return the t.me deep link.

    The full profile is stashed server-side keyed by the nonce (the deeplink
    only carries the short nonce). Redeemed once on /start.

    `lang` is the language the player's conversation was ALREADY running in
    (the widget escalation passes the turn's answer language; the site deeplink
    endpoint passes an optional `lang` field). It rides in the nonce payload and
    on redemption becomes the bot's conversation language — so a player who
    chatted in Russian lands in a Russian bot, not the service default.
    """
    # A short, URL-safe, unguessable one-time deeplink token (~20 chars).
    nonce = secrets.token_urlsafe(15)
    ttl = settings.retention()["nonce_ttl_sec"]
    payload = dict(handshake_context or {})
    if lang and lang in language.supported_codes():
        payload["lang"] = lang
    await db.create_retention_nonce(nonce, product["id"], payload,
                                    escalation, ttl)
    # Durable funnel marker: expired nonces are reaped from the table, so the
    # "deeplinks issued" denominator lives in admin_events.
    await db.log_admin_event(
        None, "retention_deeplink_created",
        {"escalation": escalation}, product_id=product["id"])
    username = product.get("telegram_bot_username") or ""
    deep_link = f"https://t.me/{username}?start={nonce}" if username else ""
    return {"nonce": nonce, "deep_link": deep_link}


# ---------------------------------------------------------------------------
# Profile / tier helpers (the whitelist lives in player_sync.PROFILE_FIELDS)
# ---------------------------------------------------------------------------


async def maybe_pull_profile(product: dict[str, Any], ru: dict[str, Any]
                             ) -> dict[str, Any]:
    """Lazy profile refresh (§8 level 2) — the implementation lives in
    player_sync (the unified data-sync module). The thin wrapper passes this
    module's `is_safe_outbound_url` name so a test monkeypatching it here
    still governs the pull."""
    return await player_sync.maybe_pull_profile(
        product, ru, url_guard=is_safe_outbound_url)


def tier_ordinal(vip_level: Optional[str], cfg: dict[str, Any]) -> int:
    """Map a free-text vip_level to its ordinal in the configured tier ladder."""
    tiers = [str(t).strip().lower() for t in cfg.get("vip_tiers", [])]
    v = (vip_level or "").strip().lower()
    if v in tiers:
        return tiers.index(v)
    return 0


def tier_stage_ceiling(vip_level: Optional[str], cfg: dict[str, Any]) -> int:
    """The highest photo stage this player's VIP tier may unlock."""
    v = (vip_level or "").strip().lower()
    by_tier = cfg.get("max_stage_by_tier", {}) or {}
    if v in by_tier:
        return int(by_tier[v])
    return int(cfg.get("max_stage", 4))


def _normalize(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "").lower()
    return re.sub(r"\s+", " ", t)


def is_photo_request(text: str) -> bool:
    return bool(_PHOTO_REQUEST_RE.search(_normalize(text)))


def is_meaningful(text: str) -> bool:
    """A message worth counting toward engagement/progression (>= 2 alnum)."""
    return sum(1 for c in (text or "") if c.isalnum()) >= 2


# ---------------------------------------------------------------------------
# Photo candidate selection (pre-model)
# ---------------------------------------------------------------------------
async def select_photo_candidates(product_id: int, ru: dict[str, Any],
                                  user_text: str, *,
                                  bypass_cooldown: bool = False
                                  ) -> list[dict[str, Any]]:
    """The allowed photo set for this turn (empty = no photo this turn).

    Gates:
      - daily cap: at/over -> empty (hard limit, reactive included);
      - proactive cooldown: unless the player explicitly asked, require
        msgs_since_photo >= cooldown;
      - tier x stage: level_min <= tier ordinal, stage <= unlocked (+1 teaser,
        capped by the tier ceiling);
      - unseen only; capped at candidate_list_size.
    """
    cfg = settings.retention()
    # Daily cap (with midnight reset already handled by record view / DB day).
    sent_today = int(ru.get("photos_sent_today") or 0)
    photos_day = ru.get("photos_day")
    # The stored counter only counts if it belongs to today; a stale day means 0.
    # UTC on purpose: the DB counters roll over on the UTC day, so both sides
    # must read the same clock or the cap stops enforcing around midnight.
    today = _dt.datetime.now(_dt.timezone.utc).date().isoformat()
    if photos_day and str(photos_day)[:10] != today:
        sent_today = 0
    if sent_today >= int(cfg["daily_photo_cap"]):
        return []
    # Proactive cooldown unless the player asked (or the caller — the ping
    # worker on a photo-action rule — explicitly bypasses it; the daily cap
    # above still holds).
    reactive = bypass_cooldown or is_photo_request(user_text)
    if not reactive and int(ru.get("msgs_since_photo") or 0) < int(
            cfg["proactive_photo_cooldown_msgs"]):
        return []
    vip = ru.get("vip_level")
    level_ord = tier_ordinal(vip, cfg)
    ceiling = tier_stage_ceiling(vip, cfg)
    unlocked = int(ru.get("unlocked_stage") or 1)
    # current stages + one teaser step ahead, never above the tier ceiling.
    max_stage = min(unlocked + 1, ceiling)
    return await db.candidate_photos(
        product_id, int(ru["id"]),
        level_ordinal=level_ord, max_stage=max_stage,
        limit=int(cfg["candidate_list_size"]),
    )


async def intro_photo_due(ru: dict[str, Any]) -> bool:
    """Is the introduction-photo rule due for this player on this turn?

    A brand-new player should SEE that chatting with Nika comes with photos —
    so within his first `intro_photo_within_msgs` meaningful messages, if he
    has never received a photo, the turn carries an imperative Layer-3 block
    ordering the model to send one with a "this is me - let's get to know each
    other" caption (the caption itself stays model-written: localized and
    grounded in the chosen photo's description). The candidate selection
    bypasses the proactive cooldown for this turn; the daily cap and the
    tier x stage gate still hold. Admin knobs: `retention.intro_photo_enabled`
    / `intro_photo_within_msgs`.
    """
    cfg = settings.retention()
    if not cfg["intro_photo_enabled"]:
        return False
    if int(ru.get("meaningful_msgs") or 0) > int(cfg["intro_photo_within_msgs"]):
        return False
    return not await db.has_photo_views(int(ru["id"]))


def progression_context(ru: dict[str, Any]) -> dict[str, Any]:
    """The player's REAL progression state for the Layer-3 PROGRESSION block.

    Mirrors the maybe_advance_stage gate maths (same thresholds/ceilings), so
    what Nika tells the player about his progress is exactly what the backend
    will actually enforce. `at_ceiling` = no further stage is reachable by
    chatting alone (tier/max ceiling hit, or no configured threshold).
    """
    cfg = settings.retention()
    unlocked = int(ru.get("unlocked_stage") or 1)
    ceiling = min(tier_stage_ceiling(ru.get("vip_level"), cfg),
                  int(cfg["max_stage"]))
    thresholds = cfg.get("stage_advance_msgs") or []
    next_stage = unlocked + 1
    idx = next_stage - 2
    threshold = thresholds[idx] if 0 <= idx < len(thresholds) else None
    at_ceiling = next_stage > ceiling or threshold is None
    return {
        "stage": unlocked,
        "ceiling": max(ceiling, unlocked),
        "vip_level": (str(ru.get("vip_level") or "").strip() or None),
        "meaningful_msgs": int(ru.get("meaningful_msgs") or 0),
        "next_threshold": None if at_ceiling else int(threshold),
        "at_ceiling": at_ceiling,
    }


async def maybe_advance_stage(ru: dict[str, Any]) -> Optional[int]:
    """Apply the backend stage-advance gate. Returns the new stage or None.

    Progression is FULLY backend-decided: engagement threshold + tier ceiling
    + spacing, evaluated on every meaningful message. The model's [[STAGE_UP]]
    sentinel is stripped defensively (chat_service) but deliberately has NO
    say in this gate. `ru` must be the freshly-bumped row.
    """
    cfg = settings.retention()
    unlocked = int(ru.get("unlocked_stage") or 1)
    next_stage = unlocked + 1
    ceiling = tier_stage_ceiling(ru.get("vip_level"), cfg)
    if next_stage > ceiling or next_stage > int(cfg["max_stage"]):
        return None
    # engagement threshold for the NEXT stage (stage 2 -> index 0, ...)
    thresholds = cfg.get("stage_advance_msgs") or []
    idx = next_stage - 2
    threshold = thresholds[idx] if 0 <= idx < len(thresholds) else None
    # No configured threshold for this stage ⇒ no advance at all: a model
    # [[STAGE_UP]] hint must never unlock explicitness the admin didn't pace.
    if threshold is None:
        return None
    meaningful = int(ru.get("meaningful_msgs") or 0)
    if meaningful < threshold:
        return None
    # spacing: at most one advance per stage_advance_min_hours.
    last = ru.get("last_stage_advance_at")
    if last:
        try:
            last_dt = _dt.datetime.fromisoformat(str(last))
            now = _dt.datetime.now(last_dt.tzinfo)
            if (now - last_dt).total_seconds() < int(
                    cfg["stage_advance_min_hours"]) * 3600:
                return None
        except (ValueError, TypeError):
            pass
    await db.advance_retention_stage(int(ru["id"]), next_stage)
    log.info("retention_stage_advanced user=%s new_stage=%s", ru["id"], next_stage)
    return next_stage


# ---------------------------------------------------------------------------
# Language for a retention user (Telegram has no browser locale — use the
# client language_code, then any drifted conv_lang, then the service default).
# ---------------------------------------------------------------------------
def resolve_user_lang(ru: dict[str, Any], tg_lang_code: Optional[str] = None) -> str:
    conv = ru.get("conv_lang")
    if conv and conv in language.supported_codes():
        return conv
    if tg_lang_code:
        mapped = language.locale_to_lang(tg_lang_code)
        if mapped:
            return mapped
    return language.default_code()


# ---------------------------------------------------------------------------
# Session helper — one telegram chat_session per retention user (lazy)
# ---------------------------------------------------------------------------
def _user_context_from_ru(ru: dict[str, Any]) -> dict[str, Any]:
    """Build the whitelisted Layer-3 player context from a retention_user row.

    On a dev/test deploy the admin **Test player** profile is overlaid on top of
    the snapshot — the same stand-in role it plays for the widget's
    create_session (api/chat.py) and the read-only prompt previews. This lets an
    operator drive the retention bot AND the proactive agent on the current test
    player, so the LIVE bot matches what the preview shows, instead of running on
    the real/empty Telegram snapshot the model would otherwise invent a
    balance/VIP for. The gate is the same one the widget uses: it applies only
    when NO widget handshake secret is configured — a production deploy sets that
    secret (the host site is authoritative), so real players always keep their
    own snapshot.
    """
    ctx = {k: ru.get(k) for k in player_sync.PROFILE_FIELDS if ru.get(k)}
    if ru.get("player_id"):
        ctx["id"] = ru.get("player_id")
    return _overlay_test_profile(ctx)


def _overlay_test_profile(ctx: dict[str, Any]) -> dict[str, Any]:
    """Overlay the enabled admin Test-player profile over a player context.

    No-op in production (a widget handshake secret is configured) or when the
    test profile is disabled — the real snapshot then wins untouched. Only the
    whitelisted `_CONTEXT_FIELDS` are copied, and an empty test field never
    clobbers a real value (so clearing one test field falls back to the snapshot).
    """
    if config.WIDGET_HANDSHAKE_SECRET:
        return ctx
    tp = settings.test_profile()
    if not tp.get("enabled"):
        return ctx
    out = dict(ctx)
    for field in prompts._CONTEXT_FIELDS:
        val = tp.get(field)
        if val:
            out[field] = val
    return out


def session_expired(session: dict[str, Any]) -> bool:
    """True when the Telegram chat has sat idle past `session_idle_minutes`.

    Idleness is measured from the session's `updated_at` (bumped on every
    persisted turn). 0 disables the lifecycle entirely (one endless session —
    the pre-lifecycle behaviour). A session with no messages yet never expires
    (there is nothing to close).
    """
    idle_min = int(settings.retention()["session_idle_minutes"])
    if idle_min <= 0 or not session.get("message_count"):
        return False
    last = session.get("updated_at")
    if not last:
        return False
    try:
        last_dt = (last if isinstance(last, _dt.datetime)
                   else _dt.datetime.fromisoformat(str(last)))
    except (ValueError, TypeError):
        return False
    now_dt = _dt.datetime.now(last_dt.tzinfo)
    return (now_dt - last_dt).total_seconds() >= idle_min * 60


async def _ensure_session(product_id: int, ru: dict[str, Any],
                          lang: str) -> dict[str, Any]:
    """Get or lazily create the telegram chat session backing this player.

    Chat lifecycle: a session is reused only while it is open and not idle past
    `session_idle_minutes`. An abandoned chat is closed (status='resolved') and
    the next message starts a FRESH session that points back at the closed one
    via `prev_session_id` — so the transcripts stay separate chats in the admin,
    while the first prompt of the new chat can carry a short continuity tail
    (see chat_service.handle_retention_message / carry_context_turns).
    """
    sid = ru.get("session_id")
    prev_id: Optional[str] = None
    if sid:
        session = await db.get_session(sid)
        if session:
            if session.get("status") == "open" and not session_expired(session):
                return session
            # Idle or already closed (e.g. by an earlier rollover) — start a
            # fresh chat. Only a chat that actually happened becomes the
            # continuity anchor; an empty open session is simply reused.
            if session.get("message_count"):
                prev_id = session["id"]
                if session.get("status") == "open":
                    await db.close_retention_session(
                        session["id"], product_id=product_id, reason="idle")
                    log.info("retention_session_rollover product_id=%s old=%s",
                             product_id, session["id"])
            elif session.get("status") == "open":
                return session
    user_context = _user_context_from_ru(ru)
    new_id = str(uuid.uuid4())
    await db.create_session(
        consumer="telegram", player_id=ru.get("player_id"), lang=lang,
        user_context=user_context, session_id=new_id, product_id=product_id,
        tg_user_id=ru.get("tg_user_id"), prev_session_id=prev_id,
    )
    await db.set_retention_session(int(ru["id"]), new_id)
    return await db.get_session(new_id)


# ---------------------------------------------------------------------------
# Subscription gate
# ---------------------------------------------------------------------------
# Positive getChatMember results are cached briefly so an active conversation
# doesn't cost one Telegram API round-trip per message. Only YES is cached (an
# unsubscribed player who just subscribed must pass the re-check instantly);
# the explicit "I subscribed" button always re-checks live (use_cache=False).
_SUB_CACHE_TTL_SEC = 600
_SUB_CACHE_PRUNE_THRESHOLD = 50_000
_sub_cache: dict[tuple[int, int], float] = {}  # (product_id, tg_user_id) -> expiry

# Rate-limit courtesy notices already sent: spam_key -> monotonic time of the
# notice. One "slow down" nudge per block streak (cleared when a message passes,
# expired after a window), so a blocked player learns WHY the bot went quiet
# while a hammering bot can't turn the limiter into a Telegram-send amplifier.
_rl_notified: dict[str, float] = {}
_RL_NOTIFIED_PRUNE_THRESHOLD = 10_000


async def check_subscription(client: TelegramClient, product: dict[str, Any],
                             tg_user_id: int, *, use_cache: bool = True
                             ) -> Optional[bool]:
    """Tri-state subscription gate. True = subscribed (or no channel configured),
    False = definitively not a member, None = the check could not be completed
    (Telegram outage / bot-not-admin blip).

    None is NOT False: an outage must never be treated as "left the channel" — the
    message gate fails open on None (see _handle_message) so a transient blip can't
    drop the player's message and silently unsubscribe them from proactive pings.
    Only a real True/False result touches the positive cache."""
    channel = product.get("telegram_channel_id")
    if not channel:
        return True
    key = (int(product["id"]), int(tg_user_id))
    now = time.monotonic()
    if use_cache:
        expiry = _sub_cache.get(key)
        if expiry is not None and expiry > now:
            return True
    state = await client.subscription_state(channel, tg_user_id)
    if state is None:
        # Couldn't determine — leave the cached/stored state untouched, let the
        # caller fail open for this turn.
        return None
    if state:
        # TTL is the hot `retention.subscription_cache_ttl_sec` knob
        # (0 = never cache: re-check live on every message).
        ttl = int(settings.retention().get("subscription_cache_ttl_sec",
                                           _SUB_CACHE_TTL_SEC))
        if ttl > 0:
            if len(_sub_cache) > _SUB_CACHE_PRUNE_THRESHOLD:
                stale = [k for k, exp in _sub_cache.items() if exp <= now]
                for k in stale:
                    _sub_cache.pop(k, None)
            _sub_cache[key] = now + ttl
        else:
            _sub_cache.pop(key, None)
    else:
        _sub_cache.pop(key, None)
    return state


def reset_state() -> None:
    """Test helper: clear the in-memory subscription + rate-notice caches,
    the webhook update dedup and the per-chat locks."""
    _sub_cache.clear()
    _rl_notified.clear()
    _seen_updates.clear()
    _chat_locks.clear()


def _persona_name() -> str:
    """The product's TELEGRAM persona name (product scope is already set).

    Resolves through the retention prompt variables (retention override > the
    retention registry default — support values are never consulted), so the
    bot chrome always matches the persona the retention prompt actually runs.
    """
    return (settings.retention_prompt_variables().get("retention_persona_name")
            or "Nika")


def _rtn_text(key: str, lang: str) -> str:
    """A retention copy string with the {persona} placeholder substituted."""
    return translations.text(key, lang).replace("{persona}", _persona_name())


def fallback_photo_caption(lang: str) -> str:
    """A random localized fallback caption for a photo the model sent bare.

    Three registry variants — the SAME stock line stamped on every captionless
    photo is exactly the repeated-caption bot tell the photo directive bans, so
    the fallback rotates too.
    """
    key = random.choice(("rtn_photo_caption", "rtn_photo_caption_2",
                         "rtn_photo_caption_3"))
    return _rtn_text(key, lang)


def fallback_video_caption(lang: str) -> str:
    """The video twin of fallback_photo_caption (rotating video-worded lines)."""
    key = random.choice(("rtn_video_caption", "rtn_video_caption_2",
                         "rtn_video_caption_3"))
    return _rtn_text(key, lang)


def _subscribe_markup(product: dict[str, Any], lang: str) -> dict[str, Any]:
    rows = []
    url = product.get("telegram_channel_url")
    if url:
        rows.append([{"text": _rtn_text("rtn_btn_open_channel", lang),
                      "url": url}])
    rows.append([{"text": _rtn_text("rtn_btn_check_sub", lang),
                  "callback_data": CB_CHECK_SUB}])
    return inline_keyboard(rows)


def _menu_markup(entry_type: str, lang: str) -> dict[str, Any]:
    rows = []
    # The "to a manager" button is shown only to an escalated entry.
    if entry_type == "escalation":
        rows.append([{"text": _rtn_text("rtn_btn_manager", lang),
                      "callback_data": CB_MENU_MANAGER}])
    rows.append([{"text": _rtn_text("rtn_btn_nika", lang),
                  "callback_data": CB_MENU_NIKA}])
    return inline_keyboard(rows)


def _menu_parts(ru: dict[str, Any], lang: str) -> tuple[str, str]:
    """(greeting, prompt) for the first message after /start: a warm greeting
    FROM the persona (by the player's first name when the profile snapshot has
    one) + the menu prompt."""
    full_name = str(ru.get("full_name") or "").strip()
    first_name = full_name.split()[0] if full_name else ""
    key = "rtn_menu_greeting" if first_name else "rtn_menu_greeting_noname"
    greeting = _rtn_text(key, lang).replace("{name}", first_name)
    return greeting, _rtn_text("rtn_menu_prompt", lang)


def _menu_text(ru: dict[str, Any], lang: str) -> str:
    greeting, prompt = _menu_parts(ru, lang)
    return f"{greeting}\n\n{prompt}"


def _menu_html(ru: dict[str, Any], lang: str) -> str:
    """The menu message with light HTML structure: a bold greeting line above
    the plain menu prompt, both HTML-escaped (the copy is admin-edited text)."""
    greeting, prompt = _menu_parts(ru, lang)
    return f"<b>{html.escape(greeting)}</b>\n\n{html.escape(prompt)}"


async def _send_menu(client: TelegramClient, chat_id: int, ru: dict[str, Any],
                     lang: str) -> None:
    markup = _menu_markup(ru.get("entry_type", "retention"), lang)
    # Structured (bold greeting) first; if Telegram rejects the HTML for any
    # reason, fall back to the plain text so the menu always arrives.
    result = await client.send_message(chat_id, _menu_html(ru, lang),
                                       reply_markup=markup, parse_mode="HTML")
    if result is None:
        await client.send_message(chat_id, _menu_text(ru, lang),
                                  reply_markup=markup)


def _site_support_url(lang: str, product: Optional[dict[str, Any]] = None) -> str:
    """The "support on the site" destination for a Telegram hand-off.

    Priority: the product's explicit `site_url` (its main page, the dedicated
    field for "open the support chat on the site") → the per-language
    `contact_url` (translations registry) → the site's main page derived from
    the first site-map entry (the support widget lives on the site, so the
    origin is a safe landing). "" when none exists. The `site_url` field is
    first on purpose: a hand-off's "support on the site" button should land on
    the site itself, not on a Telegram/contact link an operator may have set as
    the widget's `contact_url`.
    """
    site_url = str((product or {}).get("site_url") or "").strip()
    if site_url.startswith(("http://", "https://")):
        return site_url
    url = (translations.text("contact_url", lang) or "").strip()
    if url.startswith(("http://", "https://")):
        return url
    for page in settings.site_map() or []:
        if not isinstance(page, dict):
            continue
        page_url = str(page.get("url", "")).strip()
        if page_url.startswith(("http://", "https://")):
            parts = urlsplit(page_url)
            if parts.scheme and parts.netloc:
                return f"{parts.scheme}://{parts.netloc}/"
    return ""


async def _send_manager_intro(client: TelegramClient, chat_id: int,
                              lang: str, manager: dict[str, Any]) -> None:
    """The single-manager hand-off message: intro line + one manager button."""
    link = f"https://t.me/{manager['username']}"
    intro = _rtn_text("rtn_manager_intro", lang).replace(
        "{manager}", f"{manager['display_name']} ({link})")
    await client.send_message(chat_id, intro,
                              reply_markup=inline_keyboard([[{
                                  "text": f"👤 {manager['display_name']}",
                                  "url": link}]]))


async def _send_handoff_choice(client: TelegramClient, product: dict[str, Any],
                               ru: dict[str, Any], chat_id: int, lang: str
                               ) -> str:
    """The [[HANDOFF]] hand-off message: a structured CHOICE with up to two
    buttons — the player's personal manager (right here in Telegram) and the
    support chat on the site (main page / contact_url, where the widget lives).

    Degrades gracefully: with only one destination configured it falls back to
    the matching single-option copy (`rtn_manager_intro` / `rtn_handoff_support`),
    and with neither to the plain route-out line — a hand-off never dead-ends.
    Returns the offered target ("manager+site" | "manager" | "site" | "none")
    for the retention_handoff admin event.
    """
    manager: Optional[dict[str, Any]] = None
    try:
        manager = await db.assign_round_robin_manager(product["id"], int(ru["id"]))
    except Exception:  # noqa: BLE001 - a manager-pool failure must not kill the hand-off
        log.exception("retention_handoff_manager_assign_failed product_id=%s",
                      product["id"])
    site_url = _site_support_url(lang, product)

    rows: list[list[dict[str, str]]] = []
    if manager:
        link = f"https://t.me/{manager['username']}"
        rows.append([{"text": f"👤 {manager['display_name']}", "url": link}])
        await db.log_admin_event(
            None, "retention_manager_handoff",
            {"tg_user_id": ru.get("tg_user_id"), "manager_id": manager["id"],
             "from_reason": "handoff"}, product_id=product["id"])
    if site_url:
        rows.append([{"text": _rtn_text("rtn_btn_site_support", lang),
                      "url": site_url}])

    if manager and site_url:
        # Both destinations — the structured two-button choice message.
        title = _rtn_text("rtn_handoff_title", lang)
        body = _rtn_text("rtn_handoff_choice", lang)
        markup = inline_keyboard(rows)
        result = await client.send_message(
            chat_id, f"<b>{html.escape(title)}</b>\n\n{html.escape(body)}",
            reply_markup=markup, parse_mode="HTML")
        if result is None:
            await client.send_message(chat_id, f"{title}\n\n{body}",
                                      reply_markup=markup)
        return "manager+site"
    if manager:
        await _send_manager_intro(client, chat_id, lang, manager)
        return "manager"
    await client.send_message(chat_id, _rtn_text("rtn_handoff_support", lang),
                              reply_markup=inline_keyboard(rows) if rows else None)
    return "site" if site_url else "none"


async def _route_to_manager(client: TelegramClient, product: dict[str, Any],
                            ru: dict[str, Any], chat_id: int, lang: str) -> None:
    manager = await db.assign_round_robin_manager(product["id"], int(ru["id"]))
    if manager is None:
        await client.send_message(chat_id, _rtn_text("rtn_manager_none", lang))
        return
    await _send_manager_intro(client, chat_id, lang, manager)
    await db.log_admin_event(
        None, "retention_manager_handoff",
        {"tg_user_id": ru.get("tg_user_id"), "manager_id": manager["id"],
         "from_reason": "menu"}, product_id=product["id"])


# ---------------------------------------------------------------------------
# The webhook entry point
# ---------------------------------------------------------------------------
# Update dedup + per-chat serialization (in-memory, like the rate-limit and
# subscription caches — single-instance Phase-1 state):
#   - Telegram redelivers an update when it believes the webhook did not
#     handle it (a restart mid-processing, a slow 200); without dedup the
#     player got the whole turn — including the model reply — twice.
#   - Updates run as BackgroundTasks, so two quick messages from one player
#     used to process CONCURRENTLY: the second model turn didn't see the
#     first in history, replies interleaved, and _ensure_session raced.
#     A per-(product, player) lock serializes them in arrival order.
_SEEN_UPDATES_MAX = 512
_seen_updates: dict[int, "OrderedDict[int, None]"] = {}
_CHAT_LOCKS_PRUNE_THRESHOLD = 10_000
_chat_locks: dict[tuple[int, int], asyncio.Lock] = {}


def _is_duplicate_update(product_id: int, update: dict[str, Any]) -> bool:
    uid = update.get("update_id")
    if not isinstance(uid, int):
        return False
    seen = _seen_updates.setdefault(int(product_id), OrderedDict())
    if uid in seen:
        return True
    seen[uid] = None
    while len(seen) > _SEEN_UPDATES_MAX:
        seen.popitem(last=False)
    return False


def _chat_lock(product_id: int, tg_user_id: int) -> asyncio.Lock:
    key = (int(product_id), int(tg_user_id))
    lock = _chat_locks.get(key)
    if lock is None:
        if len(_chat_locks) > _CHAT_LOCKS_PRUNE_THRESHOLD:
            for k in [k for k, lk in _chat_locks.items() if not lk.locked()]:
                _chat_locks.pop(k, None)
        lock = _chat_locks[key] = asyncio.Lock()
    return lock


async def handle_update(product: dict[str, Any], update: dict[str, Any]) -> None:
    """Process one Telegram update for a product. Never raises into the webhook."""
    tenancy.set_current_product(product["id"])
    if _is_duplicate_update(product["id"], update):
        log.info("retention_duplicate_update product_id=%s update_id=%s",
                 product["id"], update.get("update_id"))
        return
    token = await db.get_product_telegram_token(product["id"])
    if not token:
        log.warning("retention_no_bot_token product_id=%s", product["id"])
        return
    client = TelegramClient(token)
    pu = parse_update(update)
    if pu.tg_user_id is None or pu.chat_id is None:
        return
    try:
        async with _chat_lock(product["id"], pu.tg_user_id):
            if pu.kind == "callback":
                await _handle_callback(client, product, pu)
            elif pu.kind == "message":
                await _handle_message(client, product, pu)
    except Exception:  # noqa: BLE001 - a handler error must not 500 the webhook
        log.exception("retention_handle_update_failed product_id=%s", product["id"])


async def _handle_message(client: TelegramClient, product: dict[str, Any],
                          pu: ParsedUpdate) -> None:
    # /start [nonce] — the only allowed entry (org traffic is rejected).
    if pu.start_param is not None:
        await _handle_start(client, product, pu)
        return

    # --- Anti-spam gate, Telegram flavour of the widget's /message gate -----
    # Rate limit FIRST (before any DB/Telegram round-trip), against the
    # Telegram-specific per-user allowance (`tg_rate_limit_max_per_user` — a
    # lively human chat outpaces the widget's per-IP budget). The block is NOT
    # fully silent anymore: a real player whose messages just vanish thinks the
    # bot is broken ("сообщения не доходят?"), so the FIRST blocked message in
    # a streak gets a localized in-persona "give me a second" nudge; further
    # blocked messages in the same window stay silent (a hammering bot cannot
    # make us spam Telegram). Every block logs a WARNING for Railway tracing.
    spam_key = f"tg:{product['id']}:{pu.tg_user_id}"
    cfg = settings.antispam()
    try:
        antispam.check_rate_limit(spam_key, cfg["tg_rate_limit_max_per_user"])
    except antispam.AntiSpamError:
        log.warning("retention_rate_limited product_id=%s tg_user_id=%s",
                    product["id"], pu.tg_user_id)
        await db.log_admin_event_sampled(
            None, "rate_limited",
            {"channel": "telegram", "tg_user_id": pu.tg_user_id},
            product_id=product["id"])
        now = time.monotonic()
        notified = _rl_notified.get(spam_key)
        if notified is None or now - notified > float(cfg["window_sec"]):
            if len(_rl_notified) > _RL_NOTIFIED_PRUNE_THRESHOLD:
                stale = [k for k, ts in _rl_notified.items()
                         if now - ts > float(cfg["window_sec"])]
                for k in stale:
                    _rl_notified.pop(k, None)
            _rl_notified[spam_key] = now
            ru = await db.get_retention_user(product["id"], pu.tg_user_id)
            lang = resolve_user_lang(ru or {}, pu.language_code)
            await client.send_message(pu.chat_id,
                                      _rtn_text("rtn_rate_limited", lang))
        return
    _rl_notified.pop(spam_key, None)

    ru = await db.get_retention_user(product["id"], pu.tg_user_id)
    if ru is None:
        # Never entered via a deeplink — require the site.
        log.info("retention_need_deeplink product_id=%s tg_user_id=%s",
                 product["id"], pu.tg_user_id)
        lang = resolve_user_lang({}, pu.language_code)
        await client.send_message(pu.chat_id,
                                  _rtn_text("rtn_need_deeplink", lang))
        return

    lang = resolve_user_lang(ru, pu.language_code)

    # They wrote to us — whatever blocked state we recorded is stale.
    if ru.get("unreachable"):
        await db.set_retention_unreachable(int(ru["id"]), False)

    # Proactive-ping opt-out/in commands (model-free).
    command = (pu.text or "").strip().lower()
    if command in ("/stop", "stop"):
        await db.set_retention_pings_muted(int(ru["id"]), True)
        await client.send_message(pu.chat_id, _rtn_text("rtn_pings_stopped", lang))
        return
    if command == "/resume":
        await db.set_retention_pings_muted(int(ru["id"]), False)
        await client.send_message(pu.chat_id, _rtn_text("rtn_pings_resumed", lang))
        return

    # Subscription gate applies to every turn (positive results briefly cached).
    # Tri-state: True = subscribed, False = definitively not a member, None = the
    # check could not be completed (Telegram outage / bot-not-admin blip). On None
    # we FAIL OPEN for this turn — never treat an outage as "left the channel",
    # which would drop the player's live message AND flip them to unsubscribed,
    # removing them from every proactive ping until they happen to write again
    # during a healthy window.
    sub = await check_subscription(client, product, pu.tg_user_id)
    if sub is False:
        log.info("retention_subscription_gate product_id=%s tg_user_id=%s",
                 product["id"], pu.tg_user_id)
        await db.set_retention_subscribed(int(ru["id"]), False)
        await client.send_message(pu.chat_id,
                                  _rtn_text("rtn_subscribe_prompt", lang),
                                  reply_markup=_subscribe_markup(product, lang))
        return
    if sub and not ru.get("subscribed"):
        await db.set_retention_subscribed(int(ru["id"]), True)

    # Input gates: overlong text is truncated (not rejected — chats are human);
    # junk/no-content messages and injection attempts get a canned in-persona
    # line and never reach the model. (cfg was resolved at the rate-limit gate.)
    text = pu.text or ""
    max_chars = int(cfg["max_input_chars"])
    if len(text) > max_chars:
        text = text[:max_chars]
    try:
        antispam.check_low_content(text)
    except antispam.AntiSpamError:
        log.info("retention_low_content product_id=%s tg_user_id=%s",
                 product["id"], pu.tg_user_id)
        await db.log_admin_event_sampled(
            None, "low_content_blocked", {"channel": "telegram"},
            product_id=product["id"])
        await client.send_message(pu.chat_id,
                                  _rtn_text("rtn_low_content_reply", lang))
        return
    if antispam.scan_injection(text):
        log.warning("retention_injection product_id=%s tg_user_id=%s hard_block=%s",
                    product["id"], pu.tg_user_id, cfg["injection_hard_block"])
        await db.log_admin_event_sampled(
            None, "injection_blocked",
            {"channel": "telegram", "tg_user_id": pu.tg_user_id},
            product_id=product["id"])
        if cfg["injection_hard_block"]:
            await client.send_message(pu.chat_id,
                                      _rtn_text("rtn_injection_reply", lang))
            return

    await _run_nika_turn(client, product, ru, pu, lang, text=text)


async def _handle_start(client: TelegramClient, product: dict[str, Any],
                        pu: ParsedUpdate) -> None:
    nonce = (pu.start_param or "").strip()
    lang = resolve_user_lang({}, pu.language_code)
    # Product-scoped: a nonce minted for another brand's bot must not redeem
    # here (cross-tenant profile leak) — it falls into the no-nonce branch.
    data = (await db.redeem_retention_nonce(nonce, product_id=product["id"])
            if nonce else None)
    if data is None:
        # No usable nonce. Telegram frequently drops the deeplink payload when
        # the player taps the native START button on an EXISTING chat (a known
        # client behaviour, especially on Telegram Desktop — the bot then only
        # receives a bare `/start`); the nonce is also single-use and short-TTL,
        # so a re-tap / lingering past the TTL / a reused link all land here.
        # Don't dead-end a player we ALREADY know (they entered via a valid
        # deeplink before) on "open from the site" — just re-run the
        # subscription gate and re-show the menu, so the entry is robust
        # regardless of whether Telegram forwarded the payload.
        ru = await db.get_retention_user(product["id"], pu.tg_user_id)
        if ru is not None:
            await _gate_and_menu(client, product, ru, pu)
            return
        await client.send_message(pu.chat_id,
                                  _rtn_text("rtn_need_deeplink", lang))
        return
    payload = data["payload"] or {}
    entry_type = "escalation" if data["escalation"] else "retention"
    # Funnel marker: a successful /start redemption (pairs with
    # retention_deeplink_created for the entry conversion).
    await db.log_admin_event(None, "retention_start",
                             {"entry_type": entry_type},
                             product_id=product["id"])
    ru = await db.upsert_retention_user(
        product["id"], pu.tg_user_id,
        tg_username=pu.tg_username,
        player_id=payload.get("id") or payload.get("player_id"),
        entry_type=entry_type,
        profile=player_sync.profile_from_payload(payload),
        profile_source="handshake",
    )
    # The deeplink carries the language the player's site conversation was
    # already running in — adopt it as the bot's conversation language so the
    # hand-off keeps the same language instead of jumping to the default.
    link_lang = payload.get("lang")
    if (isinstance(link_lang, str) and link_lang in language.supported_codes()
            and link_lang != ru.get("conv_lang")):
        await db.set_retention_conv_lang(int(ru["id"]), link_lang)
        ru = dict(ru, conv_lang=link_lang)
    await _gate_and_menu(client, product, ru, pu)


async def _gate_and_menu(client: TelegramClient, product: dict[str, Any],
                         ru: dict[str, Any], pu: ParsedUpdate) -> None:
    """Run the channel-subscription gate, then open the entry menu.

    The shared tail of every /start entry (fresh nonce redemption AND the
    payload-less re-entry of an already-linked player): if the player is not
    subscribed, prompt them onto the channel; otherwise mark them subscribed
    and show the two-option menu.
    """
    lang = resolve_user_lang(ru, pu.language_code)
    if not await check_subscription(client, product, pu.tg_user_id):
        await client.send_message(pu.chat_id,
                                  _rtn_text("rtn_subscribe_prompt", lang),
                                  reply_markup=_subscribe_markup(product, lang))
        return
    await db.set_retention_subscribed(int(ru["id"]), True)
    await _send_menu(client, pu.chat_id, ru, lang)


async def _handle_callback(client: TelegramClient, product: dict[str, Any],
                           pu: ParsedUpdate) -> None:
    ru = await db.get_retention_user(product["id"], pu.tg_user_id)
    lang = resolve_user_lang(ru or {}, pu.language_code)
    if pu.callback_id:
        await client.answer_callback(pu.callback_id)
    if ru is None:
        await client.send_message(pu.chat_id,
                                  _rtn_text("rtn_need_deeplink", lang))
        return
    if pu.callback_data == CB_CHECK_SUB:
        if await check_subscription(client, product, pu.tg_user_id,
                                    use_cache=False):
            await db.set_retention_subscribed(int(ru["id"]), True)
            await _send_menu(client, pu.chat_id, ru, lang)
        else:
            await client.send_message(pu.chat_id,
                                      _rtn_text("rtn_not_subscribed", lang),
                                      reply_markup=_subscribe_markup(product, lang))
        return
    # Both menu actions require an active subscription.
    if not await check_subscription(client, product, pu.tg_user_id):
        await client.send_message(pu.chat_id,
                                  _rtn_text("rtn_subscribe_prompt", lang),
                                  reply_markup=_subscribe_markup(product, lang))
        return
    if pu.callback_data == CB_MENU_MANAGER and ru.get("entry_type") == "escalation":
        await _route_to_manager(client, product, ru, pu.chat_id, lang)
    elif pu.callback_data == CB_MENU_NIKA:
        await client.send_message(pu.chat_id,
                                  _rtn_text("rtn_nika_start", lang))


async def _run_nika_turn(client: TelegramClient, product: dict[str, Any],
                         ru: dict[str, Any], pu: ParsedUpdate, lang: str,
                         text: Optional[str] = None) -> None:
    """One AI retention turn: candidates -> model -> photo/handoff/stage/send."""
    # Lazy profile refresh (§8 level 2): pull a fresh profile from the casino's
    # Player API when the snapshot is stale, so targeting uses current VIP/balance.
    ru = await maybe_pull_profile(product, ru)
    session = await _ensure_session(product["id"], ru, lang)
    if session is None:
        return
    if text is None:
        text = pu.text or ""
    meaningful = is_meaningful(text)
    # Bump engagement counters BEFORE selecting candidates (so the proactive
    # cooldown counter reflects this message); returns the refreshed row.
    ru = await db.bump_retention_activity(int(ru["id"]), meaningful=meaningful)
    # Keep the model's Layer-3 personalization in sync with the latest (possibly
    # just-pulled) profile snapshot without an extra DB write — the session's
    # stored user_context was fixed at creation.
    session["user_context"] = _user_context_from_ru(ru)
    # Introduction photo: a brand-new player (never received a photo, within
    # his first meaningful messages) gets one proactively this turn — the
    # cooldown is bypassed for the selection and Layer 3 carries the imperative
    # intro block. Daily cap + tier x stage still gate the candidate set.
    intro = await intro_photo_due(ru)
    candidates = await select_photo_candidates(product["id"], ru, text,
                                               bypass_cooldown=intro)
    intro = intro and bool(candidates)
    # Appearance grounding is a best-effort nice-to-have: a failed fetch must
    # never drop the player's message (the model just gets no appearance block).
    try:
        appearance = await db.retention_appearance_context(product["id"],
                                                           int(ru["id"]))
    except Exception:  # noqa: BLE001
        log.warning("retention_appearance_fetch_failed product=%s ru=%s",
                    product["id"], ru.get("id"))
        appearance = None

    # Native "typing…" indicator while the model thinks — a reasoning turn can
    # take many seconds and dead silence before a sudden paragraph is a bot tell.
    async with _typing(client, pu.chat_id):
        reply = await chat_service.handle_retention_message(
            session, text, candidates, appearance=appearance,
            # The player's real closeness/VIP progress (Layer-3 PROGRESSION
            # block) so Nika can explain how photos unlock accurately.
            progression=progression_context(ru),
            intro_photo=intro)

    # Keep the retention_users row's sticky language in step with the answer
    # drift (chat_service persists it on the session; the ru copy drives the
    # model-free bot chrome — gate messages, menus, route-out lines).
    if reply.ok and reply.lang and reply.lang != ru.get("conv_lang"):
        await db.set_retention_conv_lang(int(ru["id"]), reply.lang)

    # Route-out: Nika does not handle support / complaints / RG / human asks.
    # The hand-off message offers the player a CHOICE of destinations — their
    # personal manager (in Telegram) and/or the support chat on the site —
    # regardless of the entry type; whatever is configured shows up.
    if reply.handoff:
        # Send ONLY the structured hand-off choice: the model's own route-out
        # line ("I'll pass you to support...") duplicated the choice message's
        # intro, so the player saw two messages. The choice card is the single,
        # canonical hand-off message (the model reply is still persisted to the
        # transcript, just not sent).
        target = await _send_handoff_choice(client, product, ru, pu.chat_id,
                                            reply.lang)
        await db.log_admin_event(
            None, "retention_handoff",
            {"tg_user_id": ru.get("tg_user_id"), "target": target},
            product_id=product["id"])
        return

    # A validated site-map CTA ([[LINK:url]]) becomes one inline button under
    # the message — the play-nudge / engagement invitations land here.
    markup = None
    if reply.link_url:
        markup = inline_keyboard([[{"text": reply.link_label or reply.link_url,
                                    "url": reply.link_url}]])

    # Media delivery (file_id cache; first send uploads + caches the id).
    if reply.photo_id is not None:
        # Never send a bare image/video: fall back to a short localized caption
        # when the model returned media with no text — worded for what is
        # actually being sent ("here's my video" vs "here's my photo").
        chosen = next((c for c in candidates
                       if int(c.get("id", 0)) == reply.photo_id), None)
        is_video = bool(chosen) and chosen.get("media_type") == "video"
        caption = reply.reply or (fallback_video_caption(reply.lang) if is_video
                                  else fallback_photo_caption(reply.lang))
        await _send_photo(client, product, ru, pu.chat_id, reply.photo_id,
                          caption, session_id=session["id"],
                          reply_markup=markup)
    elif reply.reply:
        await _send_ai_text(client, pu.chat_id, reply.reply,
                            reply_markup=markup)

    # Stage progression gate (model hint + backend gate). A real advance is
    # celebrated with a follow-up persona note (settings-gated) so the player
    # KNOWS he leveled up and what keeps the progression going.
    if meaningful:
        new_stage = await maybe_advance_stage(ru)
        if new_stage is not None and settings.retention()["stage_up_notify"]:
            await _send_stage_up_note(client, product, ru, session,
                                      pu.chat_id, reply.lang, new_stage)


async def _send_stage_up_note(client: TelegramClient, product: dict[str, Any],
                              ru: dict[str, Any], session: dict[str, Any],
                              chat_id: int, lang: str, new_stage: int) -> None:
    """Follow up a just-unlocked closeness stage with a celebratory note.

    Sent right after the reply whose message earned the advance. Persisted like
    every proactive message (db.persist_ping_turn + ping_context), so the
    prompt history renders it with its trigger — the player asking "а что это
    было?" gets a warm, accurate explanation instead of a deflection — and the
    admin transcript shows the "⚡ proactive" marker. Best-effort: the stage
    advance itself is already committed, so any failure here only skips the
    note (never un-advances or drops the turn).
    """
    try:
        cfg = settings.retention()
        ceiling = min(tier_stage_ceiling(ru.get("vip_level"), cfg),
                      int(cfg["max_stage"]))
        thresholds = cfg.get("stage_advance_msgs") or []
        # Is a FURTHER stage reachable by chatting (threshold configured and
        # under the ceiling)? Governs the "keep chatting for more" hint.
        next_idx = new_stage - 1  # threshold index for (new_stage + 1)
        has_next = (new_stage + 1 <= ceiling
                    and 0 <= next_idx < len(thresholds))
        # Follow the language the conversation just ran in (the session dict's
        # stored conv_lang may predate this turn's drift).
        session = {**session, "conv_lang": lang}
        draft = await chat_service.generate_retention_ping(
            session, idle_days=0, reason="stage_up", intent="",
            stage_up={"at_ceiling": not has_next})
        if draft is None or not draft.text:
            return
        if not await _send_ai_text(client, chat_id, draft.text):
            log.warning("retention_stage_up_send_failed ru=%s", ru.get("id"))
            return
        await db.persist_ping_turn(
            session["id"], draft.text, ai_meta=draft.ai_meta,
            product_id=product["id"],
            ping_context=("stage_up: the player's closeness level just went "
                          "up - more daring photos unlocked for him"))
        await db.log_admin_event(
            session["id"], "retention_stage_up",
            {"tg_user_id": ru.get("tg_user_id"), "new_stage": new_stage},
            product_id=product["id"])
    except Exception:  # noqa: BLE001
        log.exception("retention_stage_up_note_failed ru=%s", ru.get("id"))


# Telegram's hard cap on a media caption (chars). A longer reply is split: the
# photo goes out captionless and the text follows as a normal message.
_TG_CAPTION_LIMIT = 1024


async def _send_photo(client: TelegramClient, product: dict[str, Any],
                      ru: dict[str, Any], chat_id: int, photo_id: int,
                      caption: str, session_id: Optional[str] = None,
                      reply_markup: Optional[dict[str, Any]] = None,
                      silent: bool = False
                      ) -> Optional[str]:
    """Send a media-library item (photo OR video), via cached file_id or a
    one-time upload — the row's media_type picks sendPhoto vs sendVideo.

    Returns what actually reached the player: "photo" (the media itself),
    "text" (the caption-only fallback — the media row was inactive or the
    file missing, but a message WAS delivered), or None (nothing went
    out). The distinction matters: a delivered text fallback must still be
    persisted/recorded as sent, or the player receives a message that exists
    in no transcript. `session_id` links the delivery to the chat session so
    the admin transcript can show it inline. `reply_markup` (a validated CTA
    button) rides on whatever message actually goes out.
    """
    # Overflow-split: Telegram rejects captions over 1024 chars, so a long model
    # reply on a photo turn would fail the ENTIRE send (and re-upload the binary
    # with the same over-long caption) while the turn is already persisted — a
    # ghost the player never saw. Send the photo captionless and deliver the full
    # text as a follow-up message (carrying the CTA button, if any).
    overflow_text: Optional[str] = None
    if caption and len(caption) > _TG_CAPTION_LIMIT:
        overflow_text = caption
        caption = ""
    photo_markup = None if overflow_text else reply_markup
    # Render the (model-generated) caption's light markup as Telegram HTML.
    caption_html = telegram_format.to_html(caption) if caption else None
    photo = await db.get_retention_photo(photo_id)
    if not photo or not photo.get("active"):
        text_out = overflow_text or caption
        if text_out and await _send_ai_text(client, chat_id, text_out,
                                            reply_markup=reply_markup,
                                            silent=silent):
            return "text"
        return None
    is_video = photo.get("media_type") == "video"
    file_id = photo.get("telegram_file_id")
    result = None
    err_code: Optional[int] = None
    if file_id:
        send_by_id = (client.send_video_file_id_verbose if is_video
                      else client.send_photo_file_id_verbose)
        result, err_code, _ = await send_by_id(
            chat_id, file_id, caption=caption_html, parse_mode="HTML",
            reply_markup=photo_markup, disable_notification=silent)
    if result is None:
        if err_code == 403:
            # The player blocked the bot — flip unreachable so the guards stop
            # retrying (mirrors delivery.send_text). Do NOT burn a Volume read +
            # re-upload on a 403; it would fail again identically.
            await db.set_retention_unreachable(int(ru["id"]), True)
            return None
        # No cached id (or a stale one) — upload from the Volume. Off-thread:
        # a multi-MB Volume read on the event loop stalls every concurrent turn.
        content = await asyncio.to_thread(_read_media, photo.get("storage_ref"))
        if content is None:
            text_out = overflow_text or caption
            if text_out and await _send_ai_text(client, chat_id, text_out,
                                                reply_markup=reply_markup,
                                                silent=silent):
                return "text"
            return None
        send_bytes = (client.send_video_bytes_verbose if is_video
                      else client.send_photo_bytes_verbose)
        result, err_code, _ = await send_bytes(
            chat_id, content,
            photo.get("storage_ref") or ("video.mp4" if is_video
                                         else "photo.jpg"),
            caption=caption_html, parse_mode="HTML",
            reply_markup=photo_markup, disable_notification=silent)
        if result is None and err_code == 403:
            await db.set_retention_unreachable(int(ru["id"]), True)
            return None
        new_file_id = (TelegramClient.extract_video_file_id(result) if is_video
                       else TelegramClient.extract_photo_file_id(result))
        if new_file_id:
            await db.set_photo_file_id(photo_id, new_file_id)
    if result is not None:
        await db.record_retention_photo_view(int(ru["id"]), photo_id,
                                             product["id"], session_id)
        if overflow_text:
            # Photo went out captionless; deliver the full text (+ button) now.
            await _send_ai_text(client, chat_id, overflow_text,
                                reply_markup=reply_markup, silent=silent)
        return "photo"
    return None


def _read_media(storage_ref: Optional[str]) -> Optional[bytes]:
    """Read a photo binary from the media directory (Railway Volume)."""
    if not storage_ref:
        return None
    import os
    # storage_ref is a bare filename; never allow path traversal out of the dir.
    safe = os.path.basename(storage_ref)
    path = os.path.join(config.RETENTION_MEDIA_DIR, safe)
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        log.warning("retention_media_missing ref=%s", storage_ref)
        return None
