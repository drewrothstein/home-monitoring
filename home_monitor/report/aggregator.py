"""Aggregate site metrics into report summaries."""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Dict, Optional

from home_monitor.database import insert_report_summary
from home_monitor.report import insights, queries
from home_monitor.report.models import SiteReportSummary
from home_monitor.report.periods import (
    DEFAULT_TIMEZONE,
    PeriodBounds,
    PeriodType,
    period_bounds_for_date,
    previous_day_window,
    previous_period_bounds,
    trailing_window,
)
from home_monitor.site_config import get_sites

logger = logging.getLogger(__name__)

INTEGRATION_KEYS = {
    "flume": "water",
    "rachio": "water",
    "tankutility": "propane",
    "iaqualink": "pool",
    "span": "span",
    "tempest": "tempest",
}


def _site_timezone(site_name: str, site_config: dict, db_timezone: Optional[str]) -> str:
    if db_timezone:
        return db_timezone
    if site_config.get("timezone"):
        return site_config["timezone"]
    return DEFAULT_TIMEZONE


def _has_integration(site_config: dict, key: str) -> bool:
    return key in site_config


TRAILING_DAYS = 7


def aggregate_site_metrics(
    location_id: int,
    location_name: str,
    site_config: dict,
    period: PeriodBounds,
    include_daily_breakdown: bool = False,
    include_hourly_profile: bool = False,
) -> Dict[str, Any]:
    """Compute metrics dict for one site and period."""
    start, end = period.start, period.end
    metrics: Dict[str, Any] = {
        "site_name": location_name,
        "period_label": period.label,
        "timezone": period.timezone,
    }

    power = queries.fetch_power_energy_totals(location_id, start, end)
    peaks = queries.fetch_power_peaks(location_id, start, end)
    metrics["power"] = {**power, **peaks}

    battery = queries.fetch_battery_metrics(location_id, start, end)
    if battery:
        metrics["battery"] = battery

    if include_daily_breakdown:
        metrics["daily_breakdown"] = queries.fetch_daily_breakdown(
            location_id, start, end, period.timezone
        )

    if include_hourly_profile:
        metrics["hourly_profile"] = queries.fetch_hourly_power_profile(
            location_id, start, end, period.timezone
        )

    if period.period_type == "daily":
        _add_daily_interpretations(metrics, location_id, site_config, period, battery)
    else:
        _add_legacy_details(metrics, location_id, site_config, start, end)

    return metrics


def _add_daily_interpretations(
    metrics: Dict[str, Any],
    location_id: int,
    site_config: dict,
    period: PeriodBounds,
    battery: Optional[Dict[str, Any]],
) -> None:
    """Attach a hero card + grid of stat cards (and conditional comparisons) for a day."""
    start, end = period.start, period.end
    tz = period.timezone
    trail_start, trail_end = trailing_window(period, TRAILING_DAYS)
    prev_start, prev_end = previous_day_window(period)

    baseline_7d = queries.fetch_trailing_daily_averages(location_id, trail_start, trail_end, tz)
    metrics["baseline_7d"] = baseline_7d

    # Hero answer ("did solar cover usage?") plus the headline grid of metric cards.
    hero = insights.build_coverage_card(metrics["power"])
    metrics["hero_card"] = hero.as_dict() if hero else None

    cards = insights.build_energy_cards(metrics["power"], baseline_7d)
    battery_card = insights.build_battery_card(battery)
    if battery_card:
        cards.append(battery_card)

    if _has_integration(site_config, "flume") or _has_integration(site_config, "rachio"):
        today = queries.fetch_water_metrics(location_id, start, end)
        if today:
            week = queries.fetch_water_metrics(location_id, trail_start, trail_end) or {}
            prev = queries.fetch_water_metrics(location_id, prev_start, prev_end) or {}
            water = {
                "today_gallons": today.get("total_gallons"),
                "avg_7d_gallons": week.get("avg_daily_gallons"),
                "prev_day_gallons": prev.get("total_gallons"),
                "sprinkler_days": today.get("sprinkler_days", 0),
            }
            metrics["water"] = water
            water_card = insights.build_water_card(water)
            if water_card:
                cards.append(water_card)

    if _has_integration(site_config, "tankutility"):
        today_gal = queries.fetch_propane_usage_gallons(location_id, start, end)
        if today_gal is not None:
            week_gal = queries.fetch_propane_usage_gallons(location_id, trail_start, trail_end)
            prev_gal = queries.fetch_propane_usage_gallons(location_id, prev_start, prev_end)
            level = queries.fetch_propane_metrics(location_id, start, end) or {}
            propane = {
                "today_gallons": today_gal,
                "avg_7d_gallons": (week_gal / TRAILING_DAYS) if week_gal is not None else None,
                "prev_day_gallons": prev_gal,
                "end_pct": level.get("end_pct"),
                "end_gallons": level.get("end_gallons"),
            }
            metrics["propane"] = propane
            propane_card = insights.build_propane_card(propane)
            if propane_card:
                cards.append(propane_card)

    if _has_integration(site_config, "iaqualink"):
        today_pool = queries.fetch_pool_metrics(location_id, start, end)
        if today_pool:
            week_pool = queries.fetch_pool_metrics(location_id, trail_start, trail_end) or {}
            prev_pool = queries.fetch_pool_metrics(location_id, prev_start, prev_end) or {}
            week_heater = week_pool.get("heater_hours")
            pool = {
                "avg_pool_temp_f": today_pool.get("avg_pool_temp_f"),
                "avg_spa_temp_f": today_pool.get("avg_spa_temp_f"),
                "avg_air_temp_f": today_pool.get("avg_air_temp_f"),
                "heater_hours": today_pool.get("heater_hours"),
                "pump_hours": today_pool.get("pump_hours"),
                "prev_day_heater_hours": prev_pool.get("heater_hours"),
                "avg_7d_heater_hours": (
                    week_heater / TRAILING_DAYS if week_heater is not None else None
                ),
                "temp_profile": queries.fetch_pool_temp_air_profile(location_id, start, end, tz),
            }
            metrics["pool"] = pool
            heater_card = insights.build_pool_heater_card(pool)
            if heater_card:
                cards.append(heater_card)
            temp_card = insights.build_pool_temp_card(pool)
            if temp_card:
                cards.append(temp_card)

    metrics["cards"] = [c.as_dict() for c in cards]


def _add_legacy_details(
    metrics: Dict[str, Any],
    location_id: int,
    site_config: dict,
    start,
    end,
) -> None:
    """Weekly/monthly/yearly retain the simpler detail metrics (no daily comparisons)."""
    if _has_integration(site_config, "flume") or _has_integration(site_config, "rachio"):
        water = queries.fetch_water_metrics(location_id, start, end)
        if water:
            metrics["water"] = water

    if _has_integration(site_config, "tankutility"):
        propane = queries.fetch_propane_metrics(location_id, start, end)
        if propane:
            metrics["propane"] = propane

    if _has_integration(site_config, "iaqualink"):
        pool = queries.fetch_pool_metrics(location_id, start, end)
        if pool:
            metrics["pool"] = pool

    if _has_integration(site_config, "tempest"):
        tempest = queries.fetch_tempest_metrics(location_id, start, end)
        if tempest:
            metrics["tempest"] = tempest


def compute_site_summary(
    location_id: int,
    location_name: str,
    db_timezone: Optional[str],
    period_type: PeriodType,
    reference_date: Optional[date] = None,
    reference: Optional[datetime] = None,
) -> SiteReportSummary:
    """Aggregate metrics for one site and persist to report_summaries."""
    sites = get_sites()
    site_config = sites.get(location_name, {})

    tz = _site_timezone(location_name, site_config, db_timezone)
    if reference_date:
        period = period_bounds_for_date(period_type, tz, reference_date)
    else:
        period = previous_period_bounds(period_type, tz, reference)

    include_breakdown = period_type in ("weekly", "monthly", "yearly")
    include_hourly = period_type == "daily"

    try:
        metrics = aggregate_site_metrics(
            location_id,
            location_name,
            site_config,
            period,
            include_daily_breakdown=include_breakdown,
            include_hourly_profile=include_hourly,
        )
        summary_id = insert_report_summary(
            location_id=location_id,
            period_type=period_type,
            period_start=period.start,
            period_end=period.end,
            metrics=metrics,
            status="computed",
        )
        return SiteReportSummary(
            id=summary_id,
            location_id=location_id,
            location_name=location_name,
            period=period,
            metrics=metrics,
            status="computed",
        )
    except Exception as e:
        logger.error(
            "Failed to aggregate report for %s (%s): %s",
            location_name,
            period_type,
            e,
            exc_info=True,
        )
        summary_id = insert_report_summary(
            location_id=location_id,
            period_type=period_type,
            period_start=period.start,
            period_end=period.end,
            metrics=None,
            status="failed",
            error_message=str(e),
        )
        return SiteReportSummary(
            id=summary_id,
            location_id=location_id,
            location_name=location_name,
            period=period,
            metrics={},
            status="failed",
            error_message=str(e),
        )
