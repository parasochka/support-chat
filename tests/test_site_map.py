"""Site map — the per-product list of official pages the model may link to.

Rendered into a STATIC block appended to BOTH Layer-1 cores (support + retention)
and named in each core's links policy. Empty ⇒ no block (cores unchanged), so the
byte-stability invariant is preserved when no pages are configured.
"""
from __future__ import annotations

import prompts
import settings
import tenancy


PAGES = [
    {"title": "Cashier", "url": "https://nikabet.example/cashier",
     "purpose": "where players top up their balance"},
    {"title": "Bonuses", "url": "https://nikabet.example/promo", "purpose": ""},
    {"title": "", "url": "https://nikabet.example/help", "purpose": "help center"},
]


# --- render_site_map_block --------------------------------------------------
def test_render_empty_is_blank():
    assert prompts.render_site_map_block([], "NikaBet") == ""
    assert prompts.render_site_map_block(None, "NikaBet") == ""
    # Rows without a URL contribute nothing (and an all-empty list yields "").
    assert prompts.render_site_map_block([{"title": "x", "url": ""}], "NikaBet") == ""


def test_render_lists_pages_deterministically():
    block = prompts.render_site_map_block(PAGES, "NikaBet")
    assert block.startswith("=== SITE MAP (official NikaBet pages) ===")
    # Each page's exact URL is present.
    assert "https://nikabet.example/cashier" in block
    assert "https://nikabet.example/promo" in block
    assert "https://nikabet.example/help" in block
    # Title + purpose formatting; a title-less row still lists its URL + purpose.
    assert "Cashier: https://nikabet.example/cashier - where players top up" in block
    assert "- https://nikabet.example/help - help center" in block
    # Ordering follows the input list (byte-stable within a product scope).
    assert block.index("cashier") < block.index("promo") < block.index("help")
    # Deterministic: same input -> same output.
    assert block == prompts.render_site_map_block(PAGES, "NikaBet")


def test_render_falls_back_brand_label():
    block = prompts.render_site_map_block(PAGES, "")
    assert "official the brand pages" in block


# --- validate_site_map ------------------------------------------------------
def test_validate_cleans_and_drops_blank_rows():
    out = settings.validate_site_map([
        {"title": " Cashier ", "url": " https://x.example/c ", "purpose": " top up "},
        {"title": "blank", "url": "  "},          # dropped: no URL
        {"url": "https://x.example/only-url"},     # title/purpose default to ""
    ])
    assert out == [
        {"title": "Cashier", "url": "https://x.example/c", "purpose": "top up"},
        {"title": "", "url": "https://x.example/only-url", "purpose": ""},
    ]


def test_validate_rejects_non_http_and_bad_shape():
    import pytest

    with pytest.raises(ValueError):
        settings.validate_site_map({"not": "a list"})
    with pytest.raises(ValueError):
        settings.validate_site_map(["a string, not an object"])
    with pytest.raises(ValueError):
        settings.validate_site_map([{"url": "ftp://x.example/f"}])
    with pytest.raises(ValueError):
        settings.validate_site_map([{"url": "javascript:alert(1)"}])
    # None -> empty list (a cleared setting).
    assert settings.validate_site_map(None) == []


def test_validate_caps_page_count():
    many = [{"url": f"https://x.example/{i}"} for i in range(200)]
    assert len(settings.validate_site_map(many)) == settings._SITE_MAP_MAX_PAGES


# --- injection into the Layer-1 cores --------------------------------------
def test_no_pages_keeps_cores_byte_stable(monkeypatch):
    """With no site map the cores must be byte-identical to the plain render."""
    monkeypatch.setattr(settings, "site_map", lambda: [])
    core = prompts.get_system_core()
    assert core == prompts.get_system_core()          # stable between calls
    assert "=== SITE MAP" not in core                 # no block appended
    rcore = prompts.get_retention_system_core()
    assert rcore == prompts.get_retention_system_core()
    assert "=== SITE MAP" not in rcore


def test_site_map_injected_into_both_cores(monkeypatch):
    monkeypatch.setattr(settings, "site_map", lambda: PAGES)
    support = prompts.get_system_core()
    retention = prompts.get_retention_system_core()
    for core in (support, retention):
        assert "=== SITE MAP" in core
        assert "https://nikabet.example/cashier" in core
        # Still byte-stable between requests within the (now non-empty) scope.
        assert core == core
    assert prompts.get_system_core() == support        # deterministic


def test_getter_resolves_product_over_global(monkeypatch):
    """settings.site_map(): the product list replaces the global one; with no
    product scope (or no product override) it falls back to the global list."""
    monkeypatch.setattr(settings, "_cache", {
        "site_map": [{"title": "Global", "url": "https://g.example/help"}]})
    monkeypatch.setattr(settings, "_product_cache", {7: {
        "site_map": [{"title": "Cashier", "url": "https://p.example/cashier"}]}})

    tenancy.set_current_product(None)
    assert [p["url"] for p in settings.site_map()] == ["https://g.example/help"]

    tenancy.set_current_product(7)
    try:
        assert [p["url"] for p in settings.site_map()] == ["https://p.example/cashier"]
    finally:
        tenancy.set_current_product(None)


def test_links_policy_names_site_pages():
    """Both cores' links policy allows the official site pages provided."""
    support = prompts.render_prompt_variables(prompts.SYSTEM_CORE)
    assert "site pages provided to you" in support
    retention = prompts.render_retention_prompt_variables(prompts.SYSTEM_CORE_RETENTION)
    assert "site pages provided to you" in retention
