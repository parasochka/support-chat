"""Derived dashboard metrics: rates, cost-per-session, cache-hit ratio, and the
documented resolution_rate proxy."""
from __future__ import annotations

import metrics


def _raw(**over):
    base = {
        "sessions_total": 100,
        "sessions_engaged": 80,
        "sessions_with_ai": 64,
        "sessions_open": 30,
        "sessions_escalated": 20,
        "avg_messages_per_session": 4.5,
        "cost_usd_total": 8.0,
        "cached_in_total": 600,
        "tokens_in_total": 1000,
        "events": {"key_failover": 3, "rate_limited": 7, "injection_blocked": 2},
    }
    base.update(over)
    return base


def test_escalation_and_resolution_rates():
    o = metrics.overview(_raw())
    assert o["escalation_rate"] == 0.25          # 20 / 80
    assert o["resolution_rate"] == 0.75          # 1 - 0.25
    assert o["resolution_rate_is_proxy"] is True


def test_cost_and_cache_ratio():
    o = metrics.overview(_raw())
    # Average cost divides by sessions that actually called OpenAI (64), NOT all
    # engaged sessions (80): greeting-only / model-free sessions made no API call.
    assert o["cost_usd_per_session"] == round(8.0 / 64, 6)
    assert o["cache_hit_ratio"] == 0.6           # 600 / 1000


def test_cost_per_session_excludes_zero_api_sessions():
    # 50 engaged sessions but only 10 ever hit the API → average is over 10, so a
    # crowd of greeting-only "zero" sessions can't deflate per-conversation spend.
    o = metrics.overview(_raw(sessions_engaged=50, sessions_with_ai=10,
                              cost_usd_total=5.0))
    assert o["cost_usd_per_session"] == round(5.0 / 10, 6)


def test_event_counters_surface():
    o = metrics.overview(_raw())
    assert o["failovers"] == 3
    assert o["rate_limit_blocks"] == 7
    assert o["injection_blocks"] == 2


def test_zero_engaged_is_safe():
    o = metrics.overview(_raw(sessions_engaged=0, sessions_with_ai=0,
                              sessions_escalated=0,
                              tokens_in_total=0, cached_in_total=0))
    assert o["escalation_rate"] == 0.0
    assert o["resolution_rate"] == 1.0
    assert o["cost_usd_per_session"] == 0.0
    assert o["cache_hit_ratio"] == 0.0


def test_resolution_proxy_counts_abandoned():
    # 30 of the 60 non-escalated engaged sessions are still open (abandoned),
    # yet resolution_rate counts them as "resolved" — that's the documented proxy.
    o = metrics.overview(_raw())
    assert o["sessions_open"] == 30
    assert o["resolution_rate"] == 0.75  # includes the 30 abandoned
