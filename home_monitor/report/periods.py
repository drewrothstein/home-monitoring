"""Report period boundary calculations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal, Optional, Tuple
from zoneinfo import ZoneInfo

PeriodType = Literal["daily", "weekly", "monthly", "yearly"]

DEFAULT_TIMEZONE = "America/New_York"


@dataclass(frozen=True)
class PeriodBounds:
    period_type: PeriodType
    timezone: str
    start: datetime  # UTC-aware
    end: datetime  # UTC-aware, exclusive
    label: str


def _local_midnight_to_utc(local_date: date, tz_name: str) -> datetime:
    tz = ZoneInfo(tz_name)
    local_dt = datetime(local_date.year, local_date.month, local_date.day, tzinfo=tz)
    return local_dt.astimezone(ZoneInfo("UTC"))


def previous_period_bounds(
    period_type: PeriodType,
    tz_name: str,
    reference: Optional[datetime] = None,
) -> PeriodBounds:
    """
    Compute the previous completed period in the given timezone.

    Args:
        period_type: daily, weekly, monthly, or yearly
        tz_name: IANA timezone name
        reference: UTC-aware instant; defaults to now
    """
    reference = reference or datetime.now(ZoneInfo("UTC"))
    local_now = reference.astimezone(ZoneInfo(tz_name))
    local_today = local_now.date()

    if period_type == "daily":
        period_date = local_today - timedelta(days=1)
        start = _local_midnight_to_utc(period_date, tz_name)
        end = _local_midnight_to_utc(local_today, tz_name)
        label = period_date.strftime("%b %d, %Y")

    elif period_type == "weekly":
        # Previous Mon–Sun week ending last Sunday
        days_since_monday = local_today.weekday()
        this_monday = local_today - timedelta(days=days_since_monday)
        prev_monday = this_monday - timedelta(days=7)
        prev_sunday = this_monday - timedelta(days=1)
        start = _local_midnight_to_utc(prev_monday, tz_name)
        end = _local_midnight_to_utc(this_monday, tz_name)
        label = f"{prev_monday.strftime('%b %d')} – {prev_sunday.strftime('%b %d, %Y')}"

    elif period_type == "monthly":
        first_of_month = local_today.replace(day=1)
        last_month_end = first_of_month - timedelta(days=1)
        first_of_last_month = last_month_end.replace(day=1)
        start = _local_midnight_to_utc(first_of_last_month, tz_name)
        end = _local_midnight_to_utc(first_of_month, tz_name)
        label = first_of_last_month.strftime("%B %Y")

    elif period_type == "yearly":
        first_of_year = local_today.replace(month=1, day=1)
        last_year = first_of_year.year - 1
        start = _local_midnight_to_utc(date(last_year, 1, 1), tz_name)
        end = _local_midnight_to_utc(first_of_year, tz_name)
        label = str(last_year)

    else:
        raise ValueError(f"Unknown period type: {period_type}")

    return PeriodBounds(
        period_type=period_type,
        timezone=tz_name,
        start=start,
        end=end,
        label=label,
    )


def period_bounds_for_date(
    period_type: PeriodType,
    tz_name: str,
    reference_date: date,
) -> PeriodBounds:
    """Compute period bounds containing reference_date (for manual runs)."""
    if period_type == "daily":
        start = _local_midnight_to_utc(reference_date, tz_name)
        end = _local_midnight_to_utc(reference_date + timedelta(days=1), tz_name)
        label = reference_date.strftime("%b %d, %Y")

    elif period_type == "weekly":
        monday = reference_date - timedelta(days=reference_date.weekday())
        sunday = monday + timedelta(days=6)
        start = _local_midnight_to_utc(monday, tz_name)
        end = _local_midnight_to_utc(monday + timedelta(days=7), tz_name)
        label = f"{monday.strftime('%b %d')} – {sunday.strftime('%b %d, %Y')}"

    elif period_type == "monthly":
        first = reference_date.replace(day=1)
        if first.month == 12:
            next_month = first.replace(year=first.year + 1, month=1)
        else:
            next_month = first.replace(month=first.month + 1)
        start = _local_midnight_to_utc(first, tz_name)
        end = _local_midnight_to_utc(next_month, tz_name)
        label = first.strftime("%B %Y")

    elif period_type == "yearly":
        start = _local_midnight_to_utc(date(reference_date.year, 1, 1), tz_name)
        end = _local_midnight_to_utc(date(reference_date.year + 1, 1, 1), tz_name)
        label = str(reference_date.year)

    else:
        raise ValueError(f"Unknown period type: {period_type}")

    return PeriodBounds(
        period_type=period_type,
        timezone=tz_name,
        start=start,
        end=end,
        label=label,
    )


def trailing_window(period: PeriodBounds, days: int) -> Tuple[datetime, datetime]:
    """
    UTC window covering the ``days`` calendar days immediately before ``period``.

    Computed from local calendar dates (not raw UTC subtraction) so it stays aligned
    to local midnight across DST changes. Returns ``(start_utc, period.start)``.
    """
    tz = ZoneInfo(period.timezone)
    end_local_date = period.start.astimezone(tz).date()
    start_local_date = end_local_date - timedelta(days=days)
    start = _local_midnight_to_utc(start_local_date, period.timezone)
    return start, period.start


def previous_day_window(period: PeriodBounds) -> Tuple[datetime, datetime]:
    """UTC window for the single day immediately before ``period``."""
    return trailing_window(period, 1)


def batch_period_bounds(
    period_type: PeriodType,
    tz_name: str,
    reference: Optional[datetime] = None,
) -> Tuple[PeriodBounds, date]:
    """Batch period in send timezone; returns bounds and reference calendar date."""
    bounds = previous_period_bounds(period_type, tz_name, reference)
    ref_date = bounds.start.astimezone(ZoneInfo(tz_name)).date()
    return bounds, ref_date


def period_type_title(period_type: PeriodType) -> str:
    return {"daily": "Daily", "weekly": "Weekly", "monthly": "Monthly", "yearly": "Yearly"}[
        period_type
    ]
