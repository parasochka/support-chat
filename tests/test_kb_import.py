"""Bulk KB import parsers: JSON / CSV / Markdown -> normalized rows."""
from __future__ import annotations

import pytest

import kb_import


def test_parse_json():
    rows = kb_import.parse_json(
        '[{"topic_slug":"deposits","lang":"en","content":"Hi"},'
        ' {"topic_slug":"withdrawals","content":"No lang"}]'
    )
    assert rows[0] == {"topic_slug": "deposits", "lang": "en", "content": "Hi"}
    assert rows[1]["lang"] == "ru"  # default


def test_parse_csv():
    text = "topic_slug,lang,content\ndeposits,es,Hola\nbonuses,,Bonus\n"
    rows = kb_import.parse_csv(text)
    assert rows[0] == {"topic_slug": "deposits", "lang": "es", "content": "Hola"}
    assert rows[1]["lang"] == "ru"


def test_parse_markdown_h1_becomes_slug():
    rows = kb_import.parse_markdown("# Deposit Help\nBody line one\nBody line two")
    assert rows[0]["topic_slug"] == "deposit_help"
    assert "Body line one" in rows[0]["content"]


def test_parse_markdown_fallback_filename():
    rows = kb_import.parse_markdown("Just body, no heading", fallback_slug="deposits")
    assert rows[0]["topic_slug"] == "deposits"


def test_parse_dispatch_and_errors():
    assert kb_import.parse('[]', "json") == []
    with pytest.raises(kb_import.ImportError_):
        kb_import.parse("not json", "json")
    with pytest.raises(kb_import.ImportError_):
        kb_import.parse("x", "xml")
    with pytest.raises(kb_import.ImportError_):
        kb_import.parse_csv("wrong,header\n1,2")
    with pytest.raises(kb_import.ImportError_):
        kb_import.parse_json('[{"topic_slug":"","content":"x"}]')
