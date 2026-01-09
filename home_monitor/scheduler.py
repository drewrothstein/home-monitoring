"""
Scheduler that runs the data fetcher on a regular interval.
"""

import logging
import signal
import sys
import time

from home_monitor.config import get_enphase_fetch_interval_cycles
from home_monitor.database import init_database
from home_monitor.fetcher import fetch_all_data

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Global flag for graceful shutdown
shutdown = False


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    global shutdown
    logger.info("Received shutdown signal, stopping scheduler...")
    shutdown = True


def run_scheduler(interval_minutes: int = 5):
    """
    Run the data fetcher on a schedule.

    Args:
        interval_minutes: Number of minutes between fetches (default: 5)
    """
    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Initialize database schema
    logger.info("Initializing database schema")
    try:
        init_database()
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}", exc_info=True)
        sys.exit(1)

    interval_seconds = interval_minutes * 60
    enphase_interval_cycles = get_enphase_fetch_interval_cycles()
    cycle_count = 0

    logger.info(f"Starting scheduler with {interval_minutes} minute interval")
    logger.info(
        f"Enphase API will be called every {enphase_interval_cycles} cycles "
        f"({interval_minutes * enphase_interval_cycles} minutes)"
    )

    while not shutdown:
        try:
            logger.info(f"Starting data fetch cycle {cycle_count}")
            fetch_all_data(cycle_count=cycle_count)
            logger.info("Data fetch cycle completed")
        except Exception as e:
            logger.error(f"Error during data fetch cycle: {e}", exc_info=True)
            # Continue running even if a fetch fails

        cycle_count += 1

        if shutdown:
            break

        logger.info(f"Waiting {interval_minutes} minutes until next fetch...")
        # Sleep in small increments to allow for quick shutdown
        sleep_interval = 1  # Check every second
        slept = 0
        while slept < interval_seconds and not shutdown:
            time.sleep(sleep_interval)
            slept += sleep_interval

    logger.info("Scheduler stopped")


if __name__ == "__main__":
    # Allow interval to be overridden via environment variable
    import os

    interval = int(os.getenv("FETCHER_INTERVAL_MINUTES", "5"))
    run_scheduler(interval_minutes=interval)
