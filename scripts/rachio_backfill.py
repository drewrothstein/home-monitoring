#!/usr/bin/env python3
"""
Rachio backfill script to fetch historical watering events.

Usage:
    python scripts/rachio_backfill.py --start-date 2024-01-01
    python scripts/rachio_backfill.py --start-date 2024-01-01 --end-date 2024-06-01
    python scripts/rachio_backfill.py --start-date 2024-01-01 --site FL

Note: The Rachio API doesn't document how far back historical events can be fetched.
Testing suggests events may be available for several months to a year.
The API has a rate limit of 3,500 requests per day.
"""

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone

from home_monitor.apis.rachio import RachioApiClient
from home_monitor.config import get_rachio_credentials
from home_monitor.database import get_sprinkler_run_exists, init_database, insert_sprinkler_run
from home_monitor.site_config import ensure_site_in_database, get_sites

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def parse_date(date_str: str) -> datetime:
    """Parse a date string (YYYY-MM-DD) to a datetime object."""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.replace(tzinfo=timezone.utc)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"Invalid date format: '{date_str}'. Expected YYYY-MM-DD (e.g., 2024-01-15)"
        )


def backfill_rachio_data(
    site_name: str,
    site_config: dict,
    location_id: int,
    start_date: datetime,
    end_date: datetime,
    chunk_days: int = 7,
) -> dict:
    """
    Backfill Rachio watering events for a date range.

    Fetches events in chunks to handle potential API limitations.

    Args:
        site_name: Site name (e.g., "FL")
        site_config: Site configuration dictionary
        location_id: Database location ID
        start_date: Start of backfill range (inclusive)
        end_date: End of backfill range (inclusive)
        chunk_days: Number of days to fetch per API request (default: 7)

    Returns:
        Dictionary with counts: {new_runs, skipped_runs, total_fetched, api_calls}
    """
    rachio_config = site_config.get("rachio")
    if not rachio_config:
        logger.error(f"[Rachio] Not configured for site {site_name}")
        return {"new_runs": 0, "skipped_runs": 0, "total_fetched": 0, "api_calls": 0}

    api_key = get_rachio_credentials()
    if not api_key:
        logger.error("[Rachio] API key not found. Set RACHIO_API_KEY in your .env file.")
        return {"new_runs": 0, "skipped_runs": 0, "total_fetched": 0, "api_calls": 0}

    device_id = rachio_config.get("device_id")
    if not device_id:
        logger.error(f"[Rachio] device_id not configured for site {site_name}")
        return {"new_runs": 0, "skipped_runs": 0, "total_fetched": 0, "api_calls": 0}

    client = RachioApiClient(api_key=api_key, device_id=device_id)

    stats = {"new_runs": 0, "skipped_runs": 0, "total_fetched": 0, "api_calls": 0}

    # Process in chunks
    current_start = start_date
    while current_start < end_date:
        current_end = min(current_start + timedelta(days=chunk_days), end_date)

        logger.info(
            f"[Rachio] Fetching events for {site_name}: "
            f"{current_start.strftime('%Y-%m-%d')} to {current_end.strftime('%Y-%m-%d')}"
        )

        try:
            watering_runs = client.fetch_watering_events(
                start_time=current_start,
                end_time=current_end,
            )
            stats["api_calls"] += 1
            stats["total_fetched"] += len(watering_runs)

            for run in watering_runs:
                # Check if this run already exists
                if get_sprinkler_run_exists(
                    location_id=location_id,
                    device_id=device_id,
                    zone_id=run["zone_id"],
                    start_time=run["start_time"],
                ):
                    stats["skipped_runs"] += 1
                    continue

                # Insert new run
                insert_sprinkler_run(
                    location_id=location_id,
                    device_id=device_id,
                    zone_id=run["zone_id"],
                    zone_name=run.get("zone_name"),
                    zone_number=run.get("zone_number"),
                    start_time=run["start_time"],
                    end_time=run["end_time"],
                    duration_seconds=run.get("duration_seconds"),
                    schedule_type=run.get("schedule_type"),
                    source="rachio",
                    raw_data=run.get("raw_data"),
                )
                stats["new_runs"] += 1

            if watering_runs:
                logger.info(
                    f"[Rachio] Found {len(watering_runs)} events in this chunk "
                    f"({stats['new_runs']} new so far)"
                )

        except Exception as e:
            logger.error(
                f"[Rachio] Error fetching events for {current_start.strftime('%Y-%m-%d')} "
                f"to {current_end.strftime('%Y-%m-%d')}: {e}"
            )

        current_start = current_end

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Backfill Rachio watering events from a specified start date.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/rachio_backfill.py --start-date 2024-01-01
  python scripts/rachio_backfill.py --start-date 2024-01-01 --end-date 2024-06-01
  python scripts/rachio_backfill.py --start-date 2024-01-01 --site FL

Note: The Rachio API doesn't document how far back events can be fetched.
The API has a rate limit of 3,500 requests per day.
        """,
    )
    parser.add_argument(
        "--start-date",
        type=parse_date,
        required=True,
        help="Start date for backfill (YYYY-MM-DD format, e.g., 2024-01-01)",
    )
    parser.add_argument(
        "--end-date",
        type=parse_date,
        default=None,
        help="End date for backfill (YYYY-MM-DD format). Defaults to now.",
    )
    parser.add_argument(
        "--site",
        type=str,
        default=None,
        help="Site name to backfill (e.g., FL). If not specified, backfills all sites with Rachio.",
    )
    parser.add_argument(
        "--chunk-days",
        type=int,
        default=7,
        help="Number of days to fetch per API request (default: 7)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes",
    )

    args = parser.parse_args()

    # Set end date to now if not specified
    end_date = args.end_date or datetime.now(timezone.utc)

    # Validate date range
    if args.start_date >= end_date:
        logger.error("Start date must be before end date")
        sys.exit(1)

    days_to_fetch = (end_date - args.start_date).days
    logger.info(
        f"Backfilling Rachio data from {args.start_date.strftime('%Y-%m-%d')} "
        f"to {end_date.strftime('%Y-%m-%d')} ({days_to_fetch} days)"
    )

    if args.dry_run:
        logger.info("DRY RUN: No data will be saved")

    # Initialize database
    logger.info("Initializing database...")
    init_database()

    # Get sites with Rachio configured
    sites = get_sites()
    rachio_sites = {name: config for name, config in sites.items() if config.get("rachio")}

    if not rachio_sites:
        logger.error("No sites with Rachio configuration found in sites.json")
        sys.exit(1)

    # Filter to specific site if requested
    if args.site:
        if args.site not in rachio_sites:
            available = ", ".join(rachio_sites.keys())
            logger.error(
                f"Site '{args.site}' not found or doesn't have Rachio configured. "
                f"Available sites with Rachio: {available}"
            )
            sys.exit(1)
        rachio_sites = {args.site: rachio_sites[args.site]}

    logger.info(f"Processing {len(rachio_sites)} site(s): {', '.join(rachio_sites.keys())}")

    # Process each site
    total_stats = {"new_runs": 0, "skipped_runs": 0, "total_fetched": 0, "api_calls": 0}

    for site_name, site_config in rachio_sites.items():
        logger.info(f"\n{'=' * 60}")
        logger.info(f"Processing site: {site_name}")
        logger.info(f"{'=' * 60}")

        try:
            location_id = ensure_site_in_database(site_name)
        except Exception as e:
            logger.error(f"Failed to get location_id for site '{site_name}': {e}")
            continue

        if args.dry_run:
            device_id = site_config.get("rachio", {}).get("device_id")
            logger.info(f"[DRY RUN] Would backfill site {site_name} (device: {device_id})")
            logger.info(
                f"[DRY RUN] Date range: {args.start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}"
            )
            continue

        stats = backfill_rachio_data(
            site_name=site_name,
            site_config=site_config,
            location_id=location_id,
            start_date=args.start_date,
            end_date=end_date,
            chunk_days=args.chunk_days,
        )

        for key in total_stats:
            total_stats[key] += stats[key]

        logger.info(
            f"[Rachio] {site_name} complete: "
            f"{stats['new_runs']} new, {stats['skipped_runs']} existing, "
            f"{stats['total_fetched']} total fetched ({stats['api_calls']} API calls)"
        )

    # Summary
    logger.info(f"\n{'=' * 60}")
    logger.info("BACKFILL COMPLETE")
    logger.info(f"{'=' * 60}")
    logger.info(f"Total new runs stored: {total_stats['new_runs']}")
    logger.info(f"Total existing runs skipped: {total_stats['skipped_runs']}")
    logger.info(f"Total events fetched: {total_stats['total_fetched']}")
    logger.info(f"Total API calls made: {total_stats['api_calls']}")


if __name__ == "__main__":
    main()
