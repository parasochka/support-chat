"""Deterministic weighted A/B assignment: stable per session, weighted split."""
from __future__ import annotations

import prompt_store

_TWO = [{"id": 1, "ab_weight": 1}, {"id": 2, "ab_weight": 1}]


def test_deterministic_for_same_session():
    a = prompt_store.assign_version("session-abc", _TWO)
    b = prompt_store.assign_version("session-abc", _TWO)
    assert a == b
    assert a in (1, 2)


def test_split_covers_both_versions():
    seen = {prompt_store.assign_version(f"s{i}", _TWO) for i in range(200)}
    assert seen == {1, 2}


def test_weight_zero_everywhere_returns_none():
    assert prompt_store.assign_version("x", [{"id": 1, "ab_weight": 0}]) is None


def test_single_active_version_always_chosen():
    versions = [{"id": 5, "ab_weight": 3}, {"id": 6, "ab_weight": 0}]
    picks = {prompt_store.assign_version(f"s{i}", versions) for i in range(50)}
    assert picks == {5}


def test_weighting_is_respected_roughly():
    versions = [{"id": 1, "ab_weight": 9}, {"id": 2, "ab_weight": 1}]
    counts = {1: 0, 2: 0}
    for i in range(1000):
        counts[prompt_store.assign_version(f"u{i}", versions)] += 1
    # id 1 (90% weight) should dominate clearly.
    assert counts[1] > counts[2] * 3
