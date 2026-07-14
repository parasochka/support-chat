"""The suspended-`t.me`-domain migration: both normalizers + the render seams.

`t.me` had its registrar delegation suspended (errors outside the Telegram
apps), so every player-/model-facing Telegram link is served on the public
`telegram.me` alias. `tglinks` owns the rewrite; the KB / prompt-variable /
site-map read seams apply it so operator-stored `t.me` is fixed without a DB
migration.
"""
from __future__ import annotations

import kb
import settings
import tglinks


# --- normalize_tg_url (single whole-URL field) ------------------------------
def test_normalize_tg_url_whole_field():
    assert tglinks.normalize_tg_url(
        "https://t.me/nika_bot?start=abc") == "https://telegram.me/nika_bot?start=abc"
    assert tglinks.normalize_tg_url("http://www.t.me/chan") == "https://telegram.me/chan"
    assert tglinks.normalize_tg_url("https://t.me/x") == "https://telegram.me/x"
    # non-t.me + blanks pass through
    assert tglinks.normalize_tg_url("https://casino.example/x") == "https://casino.example/x"
    assert tglinks.normalize_tg_url("https://telegram.me/x") == "https://telegram.me/x"
    assert tglinks.normalize_tg_url("") == ""
    assert tglinks.normalize_tg_url(None) == ""
    # a host that merely CONTAINS t.me is not rewritten
    assert tglinks.normalize_tg_url(
        "https://not-t.me.evil.com/x") == "https://not-t.me.evil.com/x"


# --- normalize_tg_text (link embedded in free text) -------------------------
def test_normalize_tg_text_inline():
    assert tglinks.normalize_tg_text(
        "напиши нам в https://t.me/support сегодня"
    ) == "напиши нам в https://telegram.me/support сегодня"
    # bare host (no scheme), and a markdown link
    assert tglinks.normalize_tg_text("канал t.me/nika тут") == "канал telegram.me/nika тут"
    assert tglinks.normalize_tg_text(
        "[канал](https://t.me/chan)") == "[канал](https://telegram.me/chan)"
    # www preserved
    assert tglinks.normalize_tg_text("www.t.me/x") == "www.telegram.me/x"
    # multiple occurrences
    assert tglinks.normalize_tg_text(
        "t.me/a и t.me/b") == "telegram.me/a и telegram.me/b"


def test_normalize_tg_text_leaves_lookalikes():
    # a subdomain / suffix that contains t.me is NOT the host — untouched
    assert tglinks.normalize_tg_text("go to not-t.me/x") == "go to not-t.me/x"
    assert tglinks.normalize_tg_text("sub.t.me/x") == "sub.t.me/x"
    # `t.me` not followed by a path/query/space boundary (e.g. t.metering)
    assert tglinks.normalize_tg_text("the t.metering system") == "the t.metering system"
    # blanks
    assert tglinks.normalize_tg_text("") == ""
    assert tglinks.normalize_tg_text(None) == ""


# --- render seams -----------------------------------------------------------
async def test_render_variables_normalizes_kb_text(monkeypatch):
    async def _vars(_pid):
        return {"support": "https://t.me/support"}  # a variable value carrying t.me
    monkeypatch.setattr(kb.db, "get_kb_variables_map", _vars)

    out = await kb.render_variables(
        "Пиши в t.me/nika или {support}", product_id=1)
    assert out == "Пиши в telegram.me/nika или https://telegram.me/support"


def test_site_map_normalizes_urls(monkeypatch):
    monkeypatch.setattr(settings, "_cache", {
        "site_map": [
            {"title": "Channel", "url": "https://t.me/nika_channel", "purpose": "news"},
            {"title": "Home", "url": "https://casino.example/", "purpose": "main"},
        ]})
    monkeypatch.setattr(settings.tenancy, "current_product_id", lambda: None)
    pages = settings.site_map()
    urls = [p["url"] for p in pages]
    assert urls == ["https://telegram.me/nika_channel", "https://casino.example/"]


def test_prompt_variables_normalize(monkeypatch):
    monkeypatch.setattr(settings, "_group",
                        lambda k: {"brand_name": "Play at t.me/nika"}
                        if k == "prompt_variables" else {})
    out = settings.prompt_variables()
    assert out["brand_name"] == "Play at telegram.me/nika"
