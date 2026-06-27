"""Aggregate site metrics into report summaries."""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Dict, Optional

from home_monitor.database import insert_report_summary
from home_monitor.report import queries
from home_monitor.report.models import SiteReportSummary
from home_monitor.report.periods import (
    DEFAULT_TIMEZONE,
    PeriodBounds,
    PeriodType,
    period_bounds_for_date,
    previous_period_bounds,
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

    if _has_integration(site_config, "span"):
        span = queries.fetch_span_top_circuits(location_id, start, end)
        if span:
            metrics["span"] = span

    if _has_integration(site_config, "tempest"):
        tempest = queries.fetch_tempest_metrics(location_id, start, end)
        if tempest:
            metrics["tempest"] = tempest

    return metrics


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
