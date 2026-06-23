from datetime import datetime, timezone

from api import admin


def test_range_includes_date_only_to_day():
    dt_from, dt_to = admin._range("2026-06-01", "2026-06-23")

    assert dt_from == datetime(2026, 6, 1, tzinfo=timezone.utc)
    assert dt_to == datetime(2026, 6, 24, tzinfo=timezone.utc)


def test_range_preserves_explicit_to_timestamp():
    _, dt_to = admin._range("2026-06-01", "2026-06-23T15:30:00+00:00")

    assert dt_to == datetime(2026, 6, 23, 15, 30, tzinfo=timezone.utc)
