"""Retention CTA button ([[LINK:url]]) + the periodic play nudge.

Covers: the strip helper, site-map validation (only an admin-configured page
survives as a button), the every-N-replies play-nudge cadence and its Layer-3
block, the end-to-end decode in handle_retention_message / generate_retention_ping,
and the HTML menu framing.
"""
from __future__ import annotations

import chat_service
import db
import openai_client
import prompts
import retention
import settings

PAGES = [
    {"title": "Slots", "url": "https://nikabet.example/slots",
     "purpose": "play slot games"},
    {"title": "Cashier", "url": "https://nikabet.example/cashier",
     "purpose": "top up the balance"},
    {"title": "", "url": "https://nikabet.example/account", "purpose": ""},
]


# --- strip_link_tag ---------------------------------------------------------
def test_strip_link_tag_captures_and_strips():
    text = "[[LINK:https://x.example/slots]]\ncome spin a few with me"
    clean, url = prompts.strip_link_tag(text)
    assert clean == "come spin a few with me"
    assert url == "https://x.example/slots"


def test_strip_link_tag_first_wins_and_absent_is_none():
    clean, url = prompts.strip_link_tag(
        "[[LINK:https://a.example/1]]\n[[LINK:https://a.example/2]]\nhi")
    assert url == "https://a.example/1"
    assert clean == "hi"
    clean, url = prompts.strip_link_tag("just text")
    assert (clean, url) == ("just text", None)


# --- resolve_site_link ------------------------------------------------------
def test_resolve_site_link_exact_match_only(monkeypatch):
    monkeypatch.setattr(settings, "site_map", lambda: PAGES)
    url, label = chat_service.resolve_site_link("https://nikabet.example/slots")
    assert (url, label) == ("https://nikabet.example/slots", "Slots")
    # A title-less page falls back to the url as the button label.
    url, label = chat_service.resolve_site_link("https://nikabet.example/account")
    assert (url, label) == ("https://nikabet.example/account",
                            "https://nikabet.example/account")
    # Anything not in the site map is dropped — the model can never
    # button-ify an invented address.
    assert chat_service.resolve_site_link("https://evil.example/x") == (None, None)
    assert chat_service.resolve_site_link(None) == (None, None)


def test_resolve_site_link_empty_site_map(monkeypatch):
    monkeypatch.setattr(settings, "site_map", lambda: [])
    assert chat_service.resolve_site_link("https://nikabet.example/slots") == (
        None, None)


# --- play-nudge cadence -----------------------------------------------------
def test_play_nudge_due_cadence(monkeypatch):
    monkeypatch.setattr(settings, "retention",
                        lambda: {"play_reminder_every_msgs": 5})
    # message_count is the counter BEFORE this reply: reply N = count+1. The
    # cadence DRIFTS ±2 around the knob (a strictly periodic every-5th nudge is
    # a clockable pattern), so pin the schedule's PROPERTIES, not positions:
    # deterministic per session, never the first reply, gaps within every±2.
    fired = [n + 1 for n in range(0, 60)
             if chat_service.play_nudge_due(n, "sess-1")]
    again = [n + 1 for n in range(0, 60)
             if chat_service.play_nudge_due(n, "sess-1")]
    assert fired == again                      # stateless + reproducible
    assert fired and fired[0] > 1              # never the opening reply
    assert 3 <= fired[0] <= 7
    gaps = [b - a for a, b in zip(fired, fired[1:])]
    assert gaps and all(3 <= g <= 7 for g in gaps)
    # The jitter is keyed on session_id + cycle: schedules differ between
    # sessions or at least vary their gaps within one.
    other = [n + 1 for n in range(0, 60)
             if chat_service.play_nudge_due(n, "sess-2")]
    assert other != fired or len(set(gaps)) > 1


def test_play_nudge_zero_disables(monkeypatch):
    monkeypatch.setattr(settings, "retention",
                        lambda: {"play_reminder_every_msgs": 0})
    assert not any(chat_service.play_nudge_due(n) for n in range(0, 20))


def test_play_nudge_never_on_first_reply(monkeypatch):
    # every=1 would otherwise nudge the very first reply — the engagement
    # directive forbids a casino pitch in the opening turn.
    monkeypatch.setattr(settings, "retention",
                        lambda: {"play_reminder_every_msgs": 1})
    assert chat_service.play_nudge_due(0) is False
    assert chat_service.play_nudge_due(1) is True


# --- the Layer-3 block ------------------------------------------------------
def test_play_nudge_block_only_when_flagged():
    kwargs = dict(user_context={}, resolved_lang="en", user_text="hi")
    with_nudge = prompts.build_retention_dynamic_prompt(**kwargs, play_nudge=True)
    without = prompts.build_retention_dynamic_prompt(**kwargs)
    assert "=== PLAY NUDGE" in with_nudge
    assert "[[LINK:url]]" in with_nudge
    assert "=== PLAY NUDGE" not in without


def test_link_directive_rides_in_retention_core():
    core = prompts.get_retention_system_core()
    assert "SITE LINK BUTTON:" in core
    assert core == prompts.get_retention_system_core()  # still byte-stable


def test_ping_task_mentions_link_button():
    p = prompts.build_retention_ping_prompt(
        user_context={}, resolved_lang="en", idle_days=3,
        reason="quiet", intent="")
    assert "[[LINK:url]]" in p


# --- end-to-end decode ------------------------------------------------------
def _fake_result(text: str):
    return openai_client.ChatResult(
        text=text, lang="en", tokens_in=10, tokens_out=5, cached_in=0,
        model="gpt-5-mini", key_used="primary", latency_ms=1,
    )


class _FakeClient:
    def __init__(self, text):
        self._text = text

    async def complete(self, messages, session_id=None, on_failover=None):
        return _fake_result(self._text)


def _wire(monkeypatch, client, *, capture: dict):
    async def _kb(pid):
        return ""
    monkeypatch.setattr(db, "retention_kb_block", _kb)

    async def _history(sid, limit=20, after_id=0):
        return []
    monkeypatch.setattr(db, "get_history", _history)

    async def _client_for(pid):
        return client
    monkeypatch.setattr(openai_client, "client_for_product", _client_for)

    async def _persist(**kwargs):
        capture.update(kwargs)
        return 7
    monkeypatch.setattr(db, "persist_turn", _persist)

    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(db, "set_conv_lang", _noop)
    monkeypatch.setattr(db, "log_ai_interaction", _noop)
    monkeypatch.setattr(db, "log_admin_event_sampled", _noop)


def _session(**over):
    base = {"id": "sess-1", "product_id": 1, "user_context": {},
            "lang": "en", "conv_lang": None, "message_count": 0}
    base.update(over)
    return base


async def test_turn_link_validated_against_site_map(monkeypatch):
    cap: dict = {}
    monkeypatch.setattr(settings, "site_map", lambda: PAGES)
    _wire(monkeypatch,
          _FakeClient("[[LINK:https://nikabet.example/slots]]\ncome play"),
          capture=cap)

    reply = await chat_service.handle_retention_message(_session(), "hey")

    assert reply.link_url == "https://nikabet.example/slots"
    assert reply.link_label == "Slots"
    assert reply.reply == "come play"
    assert cap["assistant_text"] == "come play"  # tag never persisted


async def test_turn_link_outside_site_map_dropped(monkeypatch):
    cap: dict = {}
    monkeypatch.setattr(settings, "site_map", lambda: PAGES)
    _wire(monkeypatch,
          _FakeClient("[[LINK:https://evil.example/x]]\nhello"), capture=cap)

    reply = await chat_service.handle_retention_message(_session(), "hey")

    assert reply.link_url is None and reply.link_label is None
    assert reply.reply == "hello"


async def test_handoff_suppresses_link(monkeypatch):
    cap: dict = {}
    monkeypatch.setattr(settings, "site_map", lambda: PAGES)
    _wire(monkeypatch,
          _FakeClient("[[HANDOFF]]\n[[LINK:https://nikabet.example/slots]]\nbye"),
          capture=cap)

    reply = await chat_service.handle_retention_message(_session(), "operator!")

    assert reply.handoff is True
    assert reply.link_url is None  # leaving for support — no play button


async def test_ping_draft_carries_link(monkeypatch):
    monkeypatch.setattr(settings, "site_map", lambda: PAGES)

    async def _kb(pid):
        return ""
    monkeypatch.setattr(db, "retention_kb_block", _kb)

    async def _history(sid, limit=20, after_id=0):
        return []
    monkeypatch.setattr(db, "get_history", _history)

    async def _client_for(pid):
        return _FakeClient(
            "[[LINK:https://nikabet.example/cashier]]\nmiss you, come back")
    monkeypatch.setattr(openai_client, "client_for_product", _client_for)

    draft = await chat_service.generate_retention_ping(
        _session(), idle_days=5, reason="quiet", intent="warm check-in")

    assert draft is not None
    assert draft.text == "miss you, come back"
    assert draft.link_url == "https://nikabet.example/cashier"
    assert draft.link_label == "Cashier"


# --- HTML menu framing ------------------------------------------------------
def test_menu_html_bold_and_escaped():
    ru = {"full_name": "And<rey", "entry_type": "retention"}
    out = retention._menu_html(ru, "en")
    assert out.startswith("<b>")
    assert "And&lt;rey" in out                # player data is HTML-escaped
    assert "\n\n" in out


# --- hand-off choice ([[HANDOFF]] -> manager + site buttons) ----------------
class _FakeTg:
    def __init__(self):
        self.messages = []  # (chat_id, text, reply_markup, parse_mode)

    async def send_message(self, chat_id, text, *, reply_markup=None,
                           parse_mode=None):
        self.messages.append((chat_id, text, reply_markup, parse_mode))
        return {"message_id": 1}


def _labels(markup):
    return [b["text"] for row in markup["inline_keyboard"] for b in row]


def _urls(markup):
    return [b.get("url") for row in markup["inline_keyboard"] for b in row]


def test_site_support_url_prefers_contact_url(monkeypatch):
    monkeypatch.setattr(retention.translations, "text",
                        lambda key, lang: "https://x.example/contact"
                        if key == "contact_url" else "")
    assert retention._site_support_url("en") == "https://x.example/contact"


def test_site_support_url_falls_back_to_site_map_origin(monkeypatch):
    monkeypatch.setattr(retention.translations, "text", lambda key, lang: "")
    monkeypatch.setattr(settings, "site_map",
                        lambda: [{"title": "Slots",
                                  "url": "https://nikabet.example/casino/slots"}])
    assert retention._site_support_url("en") == "https://nikabet.example/"


def test_site_support_url_empty_when_nothing_configured(monkeypatch):
    monkeypatch.setattr(retention.translations, "text", lambda key, lang: "")
    monkeypatch.setattr(settings, "site_map", lambda: [])
    assert retention._site_support_url("en") == ""


def test_site_support_url_prefers_product_site_url(monkeypatch):
    # The product's explicit main-site URL wins over contact_url and site map,
    # so the "support on the site" hand-off button lands on the site itself.
    monkeypatch.setattr(retention.translations, "text",
                        lambda key, lang: "https://x.example/contact"
                        if key == "contact_url" else "")
    monkeypatch.setattr(settings, "site_map",
                        lambda: [{"title": "Slots",
                                  "url": "https://nikabet.example/casino"}])
    product = {"id": 1, "site_url": "https://nikabet.example/"}
    got = retention._site_support_url("en", product)
    assert got == "https://nikabet.example/"


PRODUCT = {"id": 1, "telegram_bot_username": "nika_bot"}
RU = {"id": 10, "tg_user_id": 7}


def _wire_handoff(monkeypatch, *, manager, site_map):
    async def _assign(pid, rid):
        return manager
    monkeypatch.setattr(retention.db, "assign_round_robin_manager", _assign)

    async def _log(*a, **k):
        return None
    monkeypatch.setattr(retention.db, "log_admin_event", _log)
    monkeypatch.setattr(settings, "site_map", lambda: site_map)


async def test_handoff_choice_both_destinations(monkeypatch):
    tg = _FakeTg()
    _wire_handoff(monkeypatch,
                  manager={"id": 3, "username": "mgr", "display_name": "Max"},
                  site_map=[{"title": "Home", "url": "https://nikabet.example/"}])

    target = await retention._send_handoff_choice(tg, PRODUCT, RU, 7, "en")

    assert target == "manager+site"
    chat_id, text, markup, parse_mode = tg.messages[0]
    assert parse_mode == "HTML" and text.startswith("<b>")
    urls = _urls(markup)
    assert "https://telegram.me/mgr" in urls
    assert "https://nikabet.example/" in urls
    assert len(urls) == 2
    assert any("Max" in lb for lb in _labels(markup))


async def test_handoff_choice_manager_only(monkeypatch):
    tg = _FakeTg()
    _wire_handoff(monkeypatch,
                  manager={"id": 3, "username": "mgr", "display_name": "Max"},
                  site_map=[])

    target = await retention._send_handoff_choice(tg, PRODUCT, RU, 7, "en")

    assert target == "manager"
    _cid, text, markup, parse_mode = tg.messages[0]
    assert "Max" in text
    assert _urls(markup) == ["https://telegram.me/mgr"]


async def test_handoff_choice_site_only(monkeypatch):
    tg = _FakeTg()
    _wire_handoff(monkeypatch, manager=None,
                  site_map=[{"title": "Home", "url": "https://nikabet.example/"}])

    target = await retention._send_handoff_choice(tg, PRODUCT, RU, 7, "en")

    assert target == "site"
    _cid, text, markup, _pm = tg.messages[0]
    assert "support" in text.lower()
    assert _urls(markup) == ["https://nikabet.example/"]


async def test_handoff_choice_nothing_configured(monkeypatch):
    tg = _FakeTg()
    _wire_handoff(monkeypatch, manager=None, site_map=[])

    target = await retention._send_handoff_choice(tg, PRODUCT, RU, 7, "en")

    assert target == "none"
    _cid, text, markup, _pm = tg.messages[0]
    assert markup is None
    assert "support" in text.lower()


async def test_handoff_manager_assign_failure_degrades(monkeypatch):
    """A manager-pool/DB failure must never kill the hand-off message."""
    tg = _FakeTg()

    async def _assign(pid, rid):
        raise RuntimeError("db down")
    monkeypatch.setattr(retention.db, "assign_round_robin_manager", _assign)
    monkeypatch.setattr(settings, "site_map",
                        lambda: [{"title": "Home",
                                  "url": "https://nikabet.example/"}])

    target = await retention._send_handoff_choice(tg, PRODUCT, RU, 7, "en")

    assert target == "site"
    assert tg.messages
