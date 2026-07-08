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

import logging
import re
import secrets
import time
import unicodedata
import uuid
from typing import Any, Optional

import antispam
import chat_service
import db
import language
import settings
import tenancy
import translations
from telegram_transport import (ParsedUpdate, TelegramClient, inline_keyboard,
                                parse_update)

log = logging.getLogger(__name__)

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
def new_nonce() -> str:
    """A short, URL-safe, unguessable one-time deeplink token (<= 64 chars)."""
    return secrets.token_urlsafe(15)  # ~20 chars


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
    nonce = new_nonce()
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
# Profile / tier helpers
# ---------------------------------------------------------------------------
_PROFILE_FIELDS = ("full_name", "email", "activation_status", "country",
                   "balance", "vip_level", "registration_date")


def _profile_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract the whitelisted profile fields (_CONTEXT_FIELDS snapshot)."""
    return {f: payload.get(f) for f in _PROFILE_FIELDS if payload.get(f) is not None}


async def maybe_pull_profile(product: dict[str, Any], ru: dict[str, Any]
                             ) -> dict[str, Any]:
    """Lazy profile refresh (§8 level 2): if the snapshot is stale and the product
    exposes a Player API, pull the fresh profile and update the snapshot.

    Best-effort — any failure leaves the existing snapshot untouched and returns
    it, so the schema degrades (not breaks) when the casino's API is down/absent.
    Returns the (possibly refreshed) retention_user row.
    """
    import datetime as _dt
    import httpx
    url = (product.get("player_api_url") or "").strip()
    player_id = ru.get("player_id")
    if not url or not player_id:
        return ru
    ttl = int(settings.retention()["profile_pull_ttl_sec"])
    if ttl <= 0:
        return ru
    last = ru.get("profile_updated_at")
    if last:
        try:
            last_dt = _dt.datetime.fromisoformat(str(last))
            now = _dt.datetime.now(last_dt.tzinfo)
            if (now - last_dt).total_seconds() < ttl:
                return ru  # fresh enough
        except (ValueError, TypeError):
            pass
    key = await db.get_product_player_api_key(product["id"])
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(url, params={"player_id": player_id},
                                    headers=headers)
        if resp.status_code != 200:
            log.warning("retention_profile_pull_http status=%s", resp.status_code)
            return ru
        data = resp.json()
    except Exception as exc:  # noqa: BLE001 - a pull failure must not break the turn
        log.warning("retention_profile_pull_failed error=%s", exc)
        return ru
    payload = data if isinstance(data, dict) else {}
    profile = _profile_from_payload(payload)
    # The Player API may also report casino activity (the ping matrix keys on
    # these); pass the timestamps through — db parses/validates them.
    for f in ("last_login_at", "last_played_at", "last_deposit_at"):
        if payload.get(f) is not None:
            profile[f] = payload[f]
    if not profile:
        return ru
    await db.update_retention_profile(product["id"], player_id, profile, "pull")
    refreshed = await db.get_retention_user(product["id"], ru["tg_user_id"])
    return refreshed or ru


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
    alnum = [c for c in (text or "") if c.isalnum()]
    return len(set(alnum)) >= 2 or len(alnum) >= 2


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
    import datetime as _dt
    today = _dt.date.today().isoformat()
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


async def maybe_advance_stage(ru: dict[str, Any], stage_up_hint: bool) -> Optional[int]:
    """Apply the backend stage-advance gate. Returns the new stage or None.

    The model only HINTS; the backend decides on threshold + tier ceiling +
    spacing. `ru` must be the freshly-bumped row.
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
    meaningful = int(ru.get("meaningful_msgs") or 0)
    soft_ok = threshold is None or meaningful >= threshold
    if not soft_ok:
        return None
    # A pure hint still needs the soft threshold; the automatic advance needs the
    # threshold too — both share the spacing guard.
    if not stage_up_hint and (threshold is None or meaningful < threshold):
        return None
    # spacing: at most one advance per stage_advance_min_hours.
    import datetime as _dt
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
    """Build the whitelisted Layer-3 player context from a retention_user row."""
    ctx = {k: ru.get(k) for k in _PROFILE_FIELDS if ru.get(k)}
    if ru.get("player_id"):
        ctx["id"] = ru.get("player_id")
    return ctx


def session_expired(session: dict[str, Any], *,
                    now: Optional[Any] = None) -> bool:
    """True when the Telegram chat has sat idle past `session_idle_minutes`.

    Idleness is measured from the session's `updated_at` (bumped on every
    persisted turn). 0 disables the lifecycle entirely (one endless session —
    the pre-lifecycle behaviour). A session with no messages yet never expires
    (there is nothing to close).
    """
    import datetime as _dt
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
    now_dt = now or _dt.datetime.now(last_dt.tzinfo)
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


async def check_subscription(client: TelegramClient, product: dict[str, Any],
                             tg_user_id: int, *, use_cache: bool = True) -> bool:
    """True when the player is subscribed to the product's channel (or no channel
    is configured — a product without a channel skips the gate)."""
    channel = product.get("telegram_channel_id")
    if not channel:
        return True
    key = (int(product["id"]), int(tg_user_id))
    now = time.monotonic()
    if use_cache:
        expiry = _sub_cache.get(key)
        if expiry is not None and expiry > now:
            return True
    ok = await client.is_subscribed(channel, tg_user_id)
    if ok:
        if len(_sub_cache) > _SUB_CACHE_PRUNE_THRESHOLD:
            stale = [k for k, exp in _sub_cache.items() if exp <= now]
            for k in stale:
                _sub_cache.pop(k, None)
        _sub_cache[key] = now + _SUB_CACHE_TTL_SEC
    else:
        _sub_cache.pop(key, None)
    return ok


def reset_state() -> None:
    """Test helper: clear the in-memory subscription cache."""
    _sub_cache.clear()


def _persona_name() -> str:
    """The product's TELEGRAM persona name (product scope is already set).

    Resolves through the retention prompt variables (retention_persona_name
    override > the inherited support persona_name), so the bot chrome always
    matches the persona the retention prompt actually runs.
    """
    return (settings.retention_prompt_variables().get("retention_persona_name")
            or "Nika")


def _rtn_text(key: str, lang: str) -> str:
    """A retention copy string with the {persona} placeholder substituted."""
    return translations.text(key, lang).replace("{persona}", _persona_name())


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


def _menu_text(ru: dict[str, Any], lang: str) -> str:
    """The first message after /start: a warm greeting FROM the persona (by the
    player's first name when the profile snapshot has one) + the menu prompt."""
    full_name = str(ru.get("full_name") or "").strip()
    first_name = full_name.split()[0] if full_name else ""
    key = "rtn_menu_greeting" if first_name else "rtn_menu_greeting_noname"
    greeting = _rtn_text(key, lang).replace("{name}", first_name)
    return f"{greeting}\n\n{_rtn_text('rtn_menu_prompt', lang)}"


async def _send_menu(client: TelegramClient, chat_id: int, ru: dict[str, Any],
                     lang: str) -> None:
    await client.send_message(
        chat_id, _menu_text(ru, lang),
        reply_markup=_menu_markup(ru.get("entry_type", "retention"), lang),
    )


async def _route_to_manager(client: TelegramClient, product: dict[str, Any],
                            ru: dict[str, Any], chat_id: int, lang: str,
                            reason: str = "menu") -> None:
    manager = await db.assign_round_robin_manager(product["id"], int(ru["id"]))
    if manager is None:
        await client.send_message(chat_id, _rtn_text("rtn_manager_none", lang))
        return
    link = f"https://t.me/{manager['username']}"
    intro = _rtn_text("rtn_manager_intro", lang).replace(
        "{manager}", f"{manager['display_name']} ({link})")
    await client.send_message(chat_id, intro,
                              reply_markup=inline_keyboard([[{
                                  "text": f"👤 {manager['display_name']}",
                                  "url": link}]]))
    await db.log_admin_event(
        None, "retention_manager_handoff",
        {"tg_user_id": ru.get("tg_user_id"), "manager_id": manager["id"],
         "from_reason": reason}, product_id=product["id"])


# ---------------------------------------------------------------------------
# The webhook entry point
# ---------------------------------------------------------------------------
async def handle_update(product: dict[str, Any], update: dict[str, Any]) -> None:
    """Process one Telegram update for a product. Never raises into the webhook."""
    tenancy.set_current_product(product["id"])
    token = await db.get_product_telegram_token(product["id"])
    if not token:
        log.warning("retention_no_bot_token product_id=%s", product["id"])
        return
    client = TelegramClient(token)
    pu = parse_update(update)
    if pu.tg_user_id is None or pu.chat_id is None:
        return
    try:
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
    # Rate limit FIRST (before any DB/Telegram round-trip) and drop silently: a
    # hammering user gets no reply, burns no tokens, and can't grow the log
    # unboundedly (the admin event is sampled).
    spam_key = f"tg:{product['id']}:{pu.tg_user_id}"
    try:
        antispam.check_rate_limit(spam_key)
    except antispam.AntiSpamError:
        await db.log_admin_event_sampled(
            None, "rate_limited",
            {"channel": "telegram", "tg_user_id": pu.tg_user_id})
        return

    ru = await db.get_retention_user(product["id"], pu.tg_user_id)
    if ru is None:
        # Never entered via a deeplink — require the site.
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
    if not await check_subscription(client, product, pu.tg_user_id):
        await db.set_retention_subscribed(int(ru["id"]), False)
        await client.send_message(pu.chat_id,
                                  _rtn_text("rtn_subscribe_prompt", lang),
                                  reply_markup=_subscribe_markup(product, lang))
        return
    if not ru.get("subscribed"):
        await db.set_retention_subscribed(int(ru["id"]), True)

    # Input gates: overlong text is truncated (not rejected — chats are human);
    # junk/no-content messages and injection attempts get a canned in-persona
    # line and never reach the model.
    cfg = settings.antispam()
    text = pu.text or ""
    max_chars = int(cfg["max_input_chars"])
    if len(text) > max_chars:
        text = text[:max_chars]
    try:
        antispam.check_low_content(text)
    except antispam.AntiSpamError:
        await db.log_admin_event_sampled(
            None, "low_content_blocked", {"channel": "telegram"})
        await client.send_message(pu.chat_id,
                                  _rtn_text("rtn_low_content_reply", lang))
        return
    if antispam.scan_injection(text):
        await db.log_admin_event_sampled(
            None, "injection_blocked",
            {"channel": "telegram", "tg_user_id": pu.tg_user_id})
        if cfg["injection_hard_block"]:
            await client.send_message(pu.chat_id,
                                      _rtn_text("rtn_injection_reply", lang))
            return

    await _run_nika_turn(client, product, ru, pu, lang, text=text)


async def _handle_start(client: TelegramClient, product: dict[str, Any],
                        pu: ParsedUpdate) -> None:
    nonce = (pu.start_param or "").strip()
    lang = resolve_user_lang({}, pu.language_code)
    data = await db.redeem_retention_nonce(nonce) if nonce else None
    if data is None:
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
        profile=_profile_from_payload(payload),
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
    lang = resolve_user_lang(ru, pu.language_code)
    # Subscription gate before any menu.
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
        await _route_to_manager(client, product, ru, pu.chat_id, lang, reason="menu")
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
    candidates = await select_photo_candidates(product["id"], ru, text)

    reply = await chat_service.handle_retention_message(session, text, candidates)

    # Keep the retention_users row's sticky language in step with the answer
    # drift (chat_service persists it on the session; the ru copy drives the
    # model-free bot chrome — gate messages, menus, route-out lines).
    if reply.ok and reply.lang and reply.lang != ru.get("conv_lang"):
        await db.set_retention_conv_lang(int(ru["id"]), reply.lang)

    # Route-out: Nika does not handle support / complaints / RG / human asks.
    if reply.handoff:
        await db.log_admin_event(
            None, "retention_handoff",
            {"tg_user_id": ru.get("tg_user_id"),
             "target": "manager" if ru.get("entry_type") == "escalation" else "support"},
            product_id=product["id"])
        if reply.reply:
            await client.send_message(pu.chat_id, reply.reply)
        if ru.get("entry_type") == "escalation":
            await _route_to_manager(client, product, ru, pu.chat_id, reply.lang,
                                    reason="handoff")
        else:
            # Give the route-out a real destination: the product's per-language
            # support contact URL (the same translations `contact_url` the widget
            # escalation card uses) as an inline button, when one is configured.
            markup = None
            url = (translations.text("contact_url", reply.lang) or "").strip()
            if url.startswith(("http://", "https://")):
                markup = inline_keyboard([[{
                    "text": translations.text("escalation_button", reply.lang),
                    "url": url}]])
            await client.send_message(
                pu.chat_id, _rtn_text("rtn_handoff_support", reply.lang),
                reply_markup=markup)
        return

    # Photo delivery (file_id cache; first send uploads + caches the id).
    if reply.photo_id is not None:
        # Never send a bare image: fall back to a short localized caption when
        # the model returned a photo with no text.
        caption = reply.reply or _rtn_text("rtn_photo_caption", reply.lang)
        await _send_photo(client, product, ru, pu.chat_id, reply.photo_id,
                          caption, session_id=session["id"])
    elif reply.reply:
        await client.send_message(pu.chat_id, reply.reply)

    # Stage progression gate (model hint + backend gate).
    if meaningful:
        await maybe_advance_stage(ru, reply.stage_up_hint)


async def _send_photo(client: TelegramClient, product: dict[str, Any],
                      ru: dict[str, Any], chat_id: int, photo_id: int,
                      caption: str, session_id: Optional[str] = None) -> bool:
    """Send a media-library photo, using the cached file_id or uploading once.

    Returns True when the photo itself was delivered (the ping worker keys its
    ledger on it); a caption-only fallback returns False. `session_id` links the
    delivery to the chat session so the admin transcript can show it inline.
    """
    photo = await db.get_retention_photo(photo_id)
    if not photo or not photo.get("active"):
        if caption:
            await client.send_message(chat_id, caption)
        return False
    file_id = photo.get("telegram_file_id")
    result = None
    if file_id:
        result = await client.send_photo_file_id(chat_id, file_id, caption=caption or None)
    if result is None:
        # No cached id (or the cached id failed) — upload from the Volume.
        content = _read_media(photo.get("storage_ref"))
        if content is None:
            if caption:
                await client.send_message(chat_id, caption)
            return False
        result = await client.send_photo_bytes(
            chat_id, content, photo.get("storage_ref") or "photo.jpg",
            caption=caption or None)
        new_file_id = TelegramClient.extract_photo_file_id(result)
        if new_file_id:
            await db.set_photo_file_id(photo_id, new_file_id)
    if result is not None:
        await db.record_retention_photo_view(int(ru["id"]), photo_id,
                                             product["id"], session_id)
        return True
    return False


def _read_media(storage_ref: Optional[str]) -> Optional[bytes]:
    """Read a photo binary from the media directory (Railway Volume)."""
    if not storage_ref:
        return None
    import os
    import config
    # storage_ref is a bare filename; never allow path traversal out of the dir.
    safe = os.path.basename(storage_ref)
    path = os.path.join(config.RETENTION_MEDIA_DIR, safe)
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        log.warning("retention_media_missing ref=%s", storage_ref)
        return None
