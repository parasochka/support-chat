"""Derived dashboard metrics — pure functions over raw aggregate counters.

The raw SQL aggregation lives in `db.py`; the rate/ratio derivations live here
as side-effect-free functions so they can be unit-tested without a database.

Metric definitions (documented per the brief §4):
  - sessions_engaged   = sessions with >= 1 user message (message_count > 0).
  - escalation_rate    = sessions_escalated / sessions_engaged.
  - resolution_rate    = 1 - escalation_rate. PROXY ONLY: it counts "not
                         escalated", which includes abandoned sessions. Track
                         sessions_open separately to see abandonment.
  - avg_messages_per_session = avg(message_count) over engaged sessions.
  - cost_usd_per_session = cost_usd_total / sessions_with_ai, where sessions_with_ai
                         is the count of sessions that made >= 1 OpenAI call. Greeting-
                         only "zero" sessions (and model-free hand-offs) made no call,
                         so they are excluded — the average reflects real spend.
  - cache_hit_ratio    = sum(cached_in) / sum(tokens_in)  (prefix-cache economics).
  - failovers / rate_limit_blocks / injection_blocks come from admin_events.
"""
from __future__ import annotations

from typing import Any


def _safe_div(num: float, den: float) -> float:
    return (num / den) if den else 0.0


def overview(raw: dict[str, Any]) -> dict[str, Any]:
    """Turn raw aggregate counters (from db.overview_aggregates) into the
    overview payload with derived rates.

    `resolution_rate` is a PROXY (see module docstring); the UI must label it so.
    """
    engaged = raw["sessions_engaged"]
    escalated = raw["sessions_escalated"]
    escalation_rate = _safe_div(escalated, engaged)
    events = raw.get("events", {})
    return {
        "sessions_total": raw["sessions_total"],
        "sessions_engaged": engaged,
        "sessions_open": raw["sessions_open"],
        "sessions_escalated": escalated,
        "escalation_rate": round(escalation_rate, 4),
        # PROXY: "not escalated" includes abandoned sessions — see sessions_open.
        "resolution_rate": round(1 - escalation_rate, 4),
        "resolution_rate_is_proxy": True,
        "avg_messages_per_session": round(raw["avg_messages_per_session"], 2),
        "cost_usd_total": round(raw["cost_usd_total"], 6),
        # Average cost is over sessions that actually called OpenAI, NOT all engaged
        # sessions: a session can be "engaged" (message_count > 0) yet make no API
        # call (the model-free message-cap hand-off), and a greeting-only session
        # makes none at all. Dividing by sessions_with_ai keeps "zero" sessions out
        # of the average so it reflects real per-conversation spend (and matches the
        # cost_per_session timeseries, which counts distinct sessions in the logs).
        "cost_usd_per_session": round(
            _safe_div(raw["cost_usd_total"], raw.get("sessions_with_ai", 0)), 6
        ),
        "cache_hit_ratio": round(
            _safe_div(raw["cached_in_total"], raw["tokens_in_total"]), 4
        ),
        "failovers": events.get("key_failover", 0),
        "rate_limit_blocks": events.get("rate_limited", 0),
        "injection_blocks": events.get("injection_blocked", 0),
        "escalations": events.get("escalation", 0),
    }
