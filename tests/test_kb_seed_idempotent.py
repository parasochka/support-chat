"""kb_seed.run() is a NON-DESTRUCTIVE bootstrap.

Regression guard for the bug where every restart re-ran the seed and wiped the
owner's admin-panel KB edits back to the placeholder at version 1 (and reset the
topic title/order/active). The seed must create the built-in topics + placeholder
KB only when missing, and never touch a topic that already exists or already has
KB. Exercised against a tiny in-memory fake `db` (the suite has no real Postgres).
"""
from __future__ import annotations

import db
from seed import kb_seed


class _FakeDB:
    """Minimal in-memory stand-in for the db helpers kb_seed calls."""

    def __init__(self) -> None:
        self.topics: dict[str, dict] = {}       # slug -> topic row
        self.entries: list[dict] = []           # kb_entries rows
        self._tid = 0
        self._eid = 0

    async def get_topic_by_slug(self, slug):
        return self.topics.get(slug)

    async def upsert_topic(self, slug, title, display_order, active=True):
        row = self.topics.get(slug)
        if row is None:
            self._tid += 1
            row = {"id": self._tid, "slug": slug}
            self.topics[slug] = row
        row.update(title=title, display_order=display_order, active=active)
        return row["id"]

    async def list_kb_entries(self, topic_id, include_inactive=False):
        rows = [e for e in self.entries if e["topic_id"] == topic_id]
        if not include_inactive:
            rows = [e for e in rows if e["active"]]
        return rows

    async def create_kb_entry(self, topic_id, lang, content):
        prior = [e for e in self.entries if e["topic_id"] == topic_id and e["lang"] == lang]
        ver = max((e["version"] for e in prior), default=0) + 1
        for e in prior:
            e["active"] = False
        self._eid += 1
        self.entries.append({
            "id": self._eid, "topic_id": topic_id, "lang": lang,
            "content": content, "version": ver, "active": True,
        })
        return self._eid


def _install(monkeypatch):
    fake = _FakeDB()
    for name in ("get_topic_by_slug", "upsert_topic", "list_kb_entries", "create_kb_entry"):
        monkeypatch.setattr(db, name, getattr(fake, name))
    return fake


async def test_first_run_seeds_all_topics_at_version_1(monkeypatch):
    fake = _install(monkeypatch)
    await kb_seed.run()

    # Every built-in topic created, each with exactly one active ru KB at version 1.
    assert len(fake.topics) == len(kb_seed.TOPICS)
    for t in kb_seed.TOPICS:
        tid = fake.topics[t["slug"]]["id"]
        active = await fake.list_kb_entries(tid)
        assert [e["version"] for e in active] == [1]
        assert active[0]["lang"] == "ru"


async def test_rerun_does_not_clobber_owner_edits(monkeypatch):
    fake = _install(monkeypatch)
    await kb_seed.run()  # initial bootstrap

    # Owner edits "bonuses" in the admin panel: renames it, hides it, and pushes a
    # new KB version with real content.
    bonuses = fake.topics["bonuses"]
    await fake.upsert_topic(
        slug="bonuses", title={"ru": "ПЕРЕИМЕНОВАНО"}, display_order=42, active=False,
    )
    await fake.create_kb_entry(bonuses["id"], "ru", "РЕАЛЬНЫЙ контент бонусов")

    # A redeploy re-runs the seed.
    await kb_seed.run()

    # Topic metadata is untouched.
    assert fake.topics["bonuses"]["title"] == {"ru": "ПЕРЕИМЕНОВАНО"}
    assert fake.topics["bonuses"]["display_order"] == 42
    assert fake.topics["bonuses"]["active"] is False

    # KB was NOT reset: the active entry is still the owner's edit (version 2), and
    # no fresh placeholder was inserted.
    active = await fake.list_kb_entries(bonuses["id"])
    assert len(active) == 1
    assert active[0]["version"] == 2
    assert active[0]["content"] == "РЕАЛЬНЫЙ контент бонусов"


async def test_rerun_is_a_noop_when_nothing_changed(monkeypatch):
    fake = _install(monkeypatch)
    await kb_seed.run()
    entries_after_first = len(fake.entries)

    await kb_seed.run()  # second boot, no edits

    # No duplicate placeholders piled up; entry count is stable.
    assert len(fake.entries) == entries_after_first
    for t in kb_seed.TOPICS:
        tid = fake.topics[t["slug"]]["id"]
        assert len(await fake.list_kb_entries(tid)) == 1
