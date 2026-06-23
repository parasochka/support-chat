import asyncio

import kb


def test_render_variables_replaces_known_and_leaves_unknown(monkeypatch):
    async def fake_map():
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

    async def fake_render(text):
        assert text == "Minimum deposit: {min_deposit}"
        return "Minimum deposit: 10 USDT"

    monkeypatch.setattr(kb.db, "get_kb_content", fake_content)
    monkeypatch.setattr(kb, "render_variables", fake_render)

    assert asyncio.run(kb.kb_block_for_topic(7)) == "Minimum deposit: 10 USDT"
