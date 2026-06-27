"""Report scheduler — checks hourly for due reports."""

from __future__ import annotations

import logging
import signal
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from home_monitor.config import get_report_config
from home_monitor.database import init_database
from home_monitor.report.batch import run_report_batch
from home_monitor.report.periods import PeriodType

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

shutdown = False


def signal_handler(signum, frame):
    global shutdown
    logger.info("Received shutdown signal, stopping report scheduler...")
    shutdown = True


def _is_due(period_type: PeriodType, config: dict, now: datetime) -> bool:
    tz = ZoneInfo(config["send_timezone"])
    local = now.astimezone(tz)

    if period_type == "daily":
        if not config["daily_enabled"]:
            return False
        return (
            local.hour == config["daily_hour"] and local.minute < config["check_interval_minutes"]
        )

    if period_type == "weekly":
        if not config["weekly_enabled"]:
            return False
        return (
            local.weekday() == 0
            and local.hour == config["daily_hour"]
            and local.minute < config["check_interval_minutes"]
        )

    if period_type == "monthly":
        if not config["monthly_enabled"]:
            return False
        return (
            local.day == 1
            and local.hour == config["daily_hour"]
            and local.minute < config["check_interval_minutes"]
        )

    if period_type == "yearly":
        if not config["yearly_enabled"]:
            return False
        return (
            local.month == 1
            and local.day == 1
            and local.hour == config["daily_hour"]
            and local.minute < config["check_interval_minutes"]
        )

    return False


def run_scheduler(interval_minutes: int | None = None) -> None:
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    logger.info("Initializing database schema")
    try:
        init_database()
    except Exception as e:
        logger.error("Failed to initialize database: %s", e, exc_info=True)
        sys.exit(1)

    config = get_report_config()
    interval = interval_minutes or config["check_interval_minutes"]
    interval_seconds = interval * 60

    logger.info("Starting report scheduler (check every %d minutes)", interval)

    while not shutdown:
        now = datetime.now(ZoneInfo("UTC"))
        for period_type in ("daily", "weekly", "monthly", "yearly"):
            if _is_due(period_type, config, now):
                logger.info("Running scheduled %s report", period_type)
                try:
                    run_report_batch(period_type=period_type, send=True, force=False)
                except Exception as e:
                    logger.error("Scheduled %s report failed: %s", period_type, e, exc_info=True)

        if shutdown:
            break

        slept = 0
        while slept < interval_seconds and not shutdown:
            time.sleep(1)
            slept += 1

    logger.info("Report scheduler stopped")


if __name__ == "__main__":
    import os

    interval = int(os.getenv("REPORT_CHECK_INTERVAL_MINUTES", "60"))
    run_scheduler(interval_minutes=interval)
