"""Tests for report period calculations."""

from datetime import datetime
from zoneinfo import ZoneInfo

from home_monitor.report.periods import (
    period_bounds_for_date,
    period_type_title,
    previous_period_bounds,
)


def test_daily_previous_period():
    ref = datetime(2026, 6, 27, 12, 0, tzinfo=ZoneInfo("UTC"))
    bounds = previous_period_bounds("daily", "America/New_York", reference=ref)
    assert bounds.label == "Jun 26, 2026"
    local_start = bounds.start.astimezone(ZoneInfo("America/New_York"))
    assert local_start.day == 26
    assert local_start.hour == 0


def test_weekly_previous_period():
    ref = datetime(2026, 6, 30, 12, 0, tzinfo=ZoneInfo("UTC"))  # Monday
    bounds = previous_period_bounds("weekly", "America/New_York", reference=ref)
    assert "Jun 22" in bounds.label
    assert "Jun 28" in bounds.label


def test_monthly_previous_period():
    ref = datetime(2026, 3, 15, 12, 0, tzinfo=ZoneInfo("UTC"))
    bounds = previous_period_bounds("monthly", "America/New_York", reference=ref)
    assert bounds.label == "February 2026"


def test_yearly_previous_period():
    ref = datetime(2026, 6, 15, 12, 0, tzinfo=ZoneInfo("UTC"))
    bounds = previous_period_bounds("yearly", "America/New_York", reference=ref)
    assert bounds.label == "2025"


def test_period_bounds_for_date_daily():
    from datetime import date

    bounds = period_bounds_for_date("daily", "America/New_York", date(2026, 6, 26))
    assert bounds.label == "Jun 26, 2026"


def test_period_type_title():
    assert period_type_title("daily") == "Daily"
    assert period_type_title("yearly") == "Yearly"
