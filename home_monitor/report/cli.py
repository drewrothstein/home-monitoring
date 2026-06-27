"""CLI entry point for manual report generation."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date

from home_monitor.config import get_report_config
from home_monitor.database import init_database
from home_monitor.report.batch import preview_report, run_report_batch

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate and deliver home monitor reports")
    parser.add_argument(
        "command",
        choices=["preview", "send", "generate"],
        help="preview=file/console output, send=SMTP/Resend, generate=summary only",
    )
    parser.add_argument(
        "--period",
        required=True,
        choices=["daily", "weekly", "monthly", "yearly"],
    )
    parser.add_argument("--date", type=_parse_date, help="Reference date (YYYY-MM-DD)")
    parser.add_argument("--site", help="Single site name (debug)")
    parser.add_argument(
        "--open",
        action="store_true",
        help="Open generated HTML in browser (preview only, macOS)",
    )

    args = parser.parse_args(argv)

    if args.command == "send":
        # Honor a configured delivery mode (smtp/resend); otherwise default to smtp.
        if os.getenv("REPORT_EMAIL_MODE", "").lower() not in ("smtp", "resend"):
            os.environ["REPORT_EMAIL_MODE"] = "smtp"
    elif args.command == "preview":
        os.environ.setdefault("REPORT_EMAIL_MODE", "file")

    try:
        init_database()
    except Exception as e:
        logger.error("Database init failed: %s", e)
        return 1

    if args.command == "generate":
        batch = run_report_batch(
            period_type=args.period,
            reference_date=args.date,
            send=False,
            site_filter=args.site,
            force=True,
        )
        logger.info(
            "Generated %d site summaries for %s report",
            len(batch.site_summaries),
            args.period,
        )
        return 0

    if args.command == "preview":
        batch = preview_report(
            period_type=args.period,
            reference_date=args.date,
            site_filter=args.site,
        )
    else:
        batch = run_report_batch(
            period_type=args.period,
            reference_date=args.date,
            send=True,
            site_filter=args.site,
            force=True,
        )

    config = get_report_config()
    if args.open and config["email_mode"] == "file":
        from pathlib import Path

        output_dir = Path(__file__).resolve().parent.parent.parent / "reports" / "output"
        period_label = batch.batch_period.start.strftime("%Y-%m-%d")
        path = output_dir / f"{args.period}-{period_label}.html"
        if path.exists():
            import subprocess

            subprocess.run(["open", str(path)], check=False)
        else:
            logger.warning("Output file not found: %s", path)

    logger.info("Report complete: %s", batch.subject)
    return 0


if __name__ == "__main__":
    sys.exit(main())
