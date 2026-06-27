"""Orchestrate multi-site report batch generation and delivery."""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Optional

from home_monitor.config import get_report_config
from home_monitor.database import (
    get_locations,
    get_report_delivery,
    insert_report_delivery,
    prune_report_deliveries,
    prune_report_summaries,
)
from home_monitor.report.aggregator import compute_site_summary
from home_monitor.report.email import deliver_report
from home_monitor.report.models import ReportBatch
from home_monitor.report.periods import (
    PeriodType,
    previous_period_bounds,
)
from home_monitor.site_config import ensure_site_in_database, get_sites

logger = logging.getLogger(__name__)


def _resolve_locations(site_filter: Optional[str] = None) -> list:
    """Ensure sites.json entries exist in DB and return location rows."""
    sites = get_sites()
    if not sites:
        logger.warning("No sites configured in sites.json")
        return []

    names = [site_filter] if site_filter else sorted(sites.keys())
    for site_name in names:
        if site_name not in sites:
            logger.warning("Site '%s' not found in sites.json", site_name)
            continue
        try:
            ensure_site_in_database(site_name)
        except Exception as e:
            logger.error("Failed to register site '%s' in database: %s", site_name, e)

    locations = get_locations()
    if site_filter:
        locations = [loc for loc in locations if loc["name"] == site_filter]
    return locations


def run_report_batch(
    period_type: PeriodType,
    reference_date: Optional[date] = None,
    send: bool = True,
    site_filter: Optional[str] = None,
    force: bool = False,
) -> ReportBatch:
    """
    Generate summaries for all sites, render combined email, optionally deliver.

    Args:
        period_type: daily, weekly, monthly, or yearly
        reference_date: Explicit period date for manual runs
        send: If True, deliver via configured email mode
        site_filter: Optional single site name (debug)
    """
    config = get_report_config()
    batch_tz = config["send_timezone"]

    if reference_date:
        from home_monitor.report.periods import period_bounds_for_date

        batch_period = period_bounds_for_date(period_type, batch_tz, reference_date)
    else:
        batch_period = previous_period_bounds(period_type, batch_tz)

    existing = get_report_delivery(period_type, batch_period.start)
    if existing and existing.get("status") == "sent" and send and not force:
        logger.info(
            "Report already sent for %s starting %s",
            period_type,
            batch_period.start,
        )
        return ReportBatch(
            period_type=period_type,
            batch_period=batch_period,
            delivery_id=existing["id"],
            summary_ids=existing.get("summary_ids") or [],
        )

    locations = _resolve_locations(site_filter)

    site_summaries = []
    for loc in locations:
        summary = compute_site_summary(
            location_id=loc["id"],
            location_name=loc["name"],
            db_timezone=loc.get("timezone"),
            period_type=period_type,
            reference_date=reference_date,
        )
        site_summaries.append(summary)

    summary_ids = [s.id for s in site_summaries if s.id is not None]
    batch = ReportBatch(
        period_type=period_type,
        batch_period=batch_period,
        site_summaries=site_summaries,
        generated_at=datetime.now(timezone.utc),
        summary_ids=summary_ids,
    )

    if not site_summaries:
        if not locations:
            logger.warning(
                "No site summaries generated for %s report: no locations in database "
                "(check sites.json and ensure sites are configured)",
                period_type,
            )
        else:
            logger.warning("No site summaries generated for %s report", period_type)
        return batch

    delivery_result = None
    if send:
        delivery_result = deliver_report(batch)

    insert_report_delivery(
        period_type=period_type,
        period_start=batch_period.start,
        period_end=batch_period.end,
        recipient=config["email_to"],
        subject=batch.subject,
        summary_ids=summary_ids,
        status=delivery_result.status if delivery_result else "skipped",
        sent_at=delivery_result.sent_at if delivery_result else None,
        error_message=delivery_result.error_message if delivery_result else None,
        raw_data=delivery_result.raw_data if delivery_result else None,
    )

    prune_report_summaries()
    prune_report_deliveries()

    return batch


def preview_report(
    period_type: PeriodType,
    reference_date: Optional[date] = None,
    site_filter: Optional[str] = None,
) -> ReportBatch:
    """Generate report and write preview (file/console mode)."""
    return run_report_batch(
        period_type=period_type,
        reference_date=reference_date,
        send=True,
        site_filter=site_filter,
        force=True,
    )
