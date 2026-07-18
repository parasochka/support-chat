"""Telegram webhook hardening: update_id dedup + per-chat serialization.

Updates run as BackgroundTasks, so without these two guards (a) a Telegram
redelivery of the same update produced the whole turn — model reply included
— twice, and (b) two quick messages from one player processed CONCURRENTLY:
the second model turn didn't see the first in history and the replies
interleaved. Both guards are in-memory (single-instance Phase-1 state, like
the rate-limit and subscription caches).
"""
from __future__ import annotations

import asyncio

import db
import retention


def _msg_update(update_id: int, user_id: int, text: str) -> dict:
    return {"update_id": update_id,
            "message": {"from": {"id": user_id, "language_code": "en"},
                        "chat": {"id": user_id}, "text": text}}


async def test_duplicate_update_is_processed_once(monkeypatch):
    retention.reset_state()
    handled: list[str] = []

    async def _token(pid):
        return "tok"

    async def _msg(client, product, pu):
        handled.append(pu.text)

    monkeypatch.setattr(db, "get_product_telegram_token", _token)
    monkeypatch.setattr(retention, "_handle_message", _msg)

    upd = _msg_update(101, 5, "hello")
    await retention.handle_update({"id": 1}, upd)
    await retention.handle_update({"id": 1}, upd)  # Telegram redelivery
    assert handled == ["hello"]

    # A different update_id processes normally; dedup is per product.
    await retention.handle_update({"id": 1}, _msg_update(102, 5, "again"))
    await retention.handle_update({"id": 2}, _msg_update(101, 5, "other bot"))
    assert handled == ["hello", "again", "other bot"]
    retention.reset_state()


async def test_updates_for_one_chat_are_serialized(monkeypatch):
    retention.reset_state()
    order: list[tuple[str, str]] = []

    async def _token(pid):
        return "tok"

    async def _msg(client, product, pu):
        order.append(("start", pu.text))
        await asyncio.sleep(0.01)  # yield so a concurrent turn COULD interleave
        order.append(("end", pu.text))

    monkeypatch.setattr(db, "get_product_telegram_token", _token)
    monkeypatch.setattr(retention, "_handle_message", _msg)

    await asyncio.gather(
        retention.handle_update({"id": 1}, _msg_update(201, 7, "first")),
        retention.handle_update({"id": 1}, _msg_update(202, 7, "second")),
    )
    # Same player: strictly one turn at a time, in arrival order.
    assert order == [("start", "first"), ("end", "first"),
                     ("start", "second"), ("end", "second")]

    # Different players are NOT serialized against each other.
    order.clear()
    await asyncio.gather(
        retention.handle_update({"id": 1}, _msg_update(203, 8, "a")),
        retention.handle_update({"id": 1}, _msg_update(204, 9, "b")),
    )
    starts = [t for t in order if t[0] == "start"]
    assert len(starts) == 2 and len(order) == 4
    retention.reset_state()
