import asyncio
import json
from datetime import datetime, timezone

import db
import kb


def test_row_to_kb_variable_serializes_datetime():
    # `updated_at` must come out as an ISO string: JSONResponse cannot serialize a
    # raw datetime, which previously 500'd the admin Variables tab.
    row = {
        "key": "min_deposit",
        "description": "Minimum deposit",
        "value": "10 USDT",
        "updated_at": datetime(2026, 6, 23, 12, 0, tzinfo=timezone.utc),
        "updated_by": "owner",
    }
    out = db._row_to_kb_variable(row)
    assert out["updated_at"] == "2026-06-23T12:00:00+00:00"
    # The whole payload must be JSON-serializable (as JSONResponse would render it).
    json.dumps(out)


def test_row_to_kb_variable_handles_null_updated_at():
    out = db._row_to_kb_variable({"key": "k", "description": "", "value": "",
                                  "updated_at": None, "updated_by": None})
    assert out["updated_at"] is None


def test_render_variables_replaces_known_and_leaves_unknown(monkeypatch):
    async def fake_map(product_id=None):
        return {"min_deposit": "10 USDT", "support_hours": "24/7"}

    monkeypatch.setattr(kb.db, "get_kb_variables_map", fake_map)

    text = "Deposit starts at {min_deposit}. Support: {support_hours}. Missing: {unknown}."

    assert asyncio.run(kb.render_variables(text)) == (
        "Deposit starts at 10 USDT. Support: 24/7. Missing: {unknown}."
    )


def test_kb_block_for_topic_resolves_variables(monkeypatch):
    async def fake_content(topic_id):
        assert topic_id == 7
        return "Minimum deposit: {min_deposit}"

    async def fake_render(text, product_id=None):
        assert text == "Minimum deposit: {min_deposit}"
        return "Minimum deposit: 10 USDT"

    monkeypatch.setattr(kb.db, "get_kb_content", fake_content)
    monkeypatch.setattr(kb, "render_variables", fake_render)

    assert asyncio.run(kb.kb_block_for_topic(7)) == "Minimum deposit: 10 USDT"
