"""Email delivery for reports (file, console, SMTP)."""

from __future__ import annotations

import base64
import logging
import smtplib
from dataclasses import dataclass
from datetime import datetime, timezone
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from home_monitor.config import get_report_config
from home_monitor.report.models import ReportBatch
from home_monitor.report.renderer import RenderedReport, render_report

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent / "reports" / "output"


@dataclass
class DeliveryResult:
    status: str
    sent_at: Optional[datetime] = None
    error_message: Optional[str] = None
    raw_data: Optional[Dict[str, Any]] = None


def deliver_report(batch: ReportBatch, rendered: Optional[RenderedReport] = None) -> DeliveryResult:
    """Deliver report via configured mode."""
    config = get_report_config()
    mode = config["email_mode"]

    if rendered is None:
        embed = mode == "file"
        rendered = render_report(batch, embed_base64=embed)

    if mode == "file":
        return _deliver_file(batch, rendered)
    if mode == "console":
        return _deliver_console(batch, rendered)
    if mode == "smtp":
        return _deliver_smtp(batch, rendered, config)
    if mode == "resend":
        return _deliver_resend(batch, rendered, config)
    raise ValueError(f"Unknown REPORT_EMAIL_MODE: {mode}")


def _deliver_file(batch: ReportBatch, rendered: RenderedReport) -> DeliveryResult:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    period_label = batch.batch_period.start.strftime("%Y-%m-%d")
    filename = f"{batch.period_type}-{period_label}.html"
    path = OUTPUT_DIR / filename
    path.write_text(rendered.html, encoding="utf-8")
    logger.info("Report written to %s", path)
    return DeliveryResult(
        status="sent",
        sent_at=datetime.now(timezone.utc),
        raw_data={"output_path": str(path), "mode": "file"},
    )


def _deliver_console(batch: ReportBatch, rendered: RenderedReport) -> DeliveryResult:
    print(f"Subject: {rendered.subject}")
    print("=" * 60)
    for summary in batch.site_summaries:
        power = summary.metrics.get("power", {})
        print(
            f"{summary.location_name}: "
            f"prod={power.get('production_kwh', 0):.1f} kWh, "
            f"cons={power.get('consumption_kwh', 0):.1f} kWh"
        )
    print("=" * 60)
    return DeliveryResult(
        status="sent",
        sent_at=datetime.now(timezone.utc),
        raw_data={"mode": "console"},
    )


def _deliver_smtp(
    batch: ReportBatch,
    rendered: RenderedReport,
    config: dict,
) -> DeliveryResult:
    msg = MIMEMultipart("related")
    msg["Subject"] = rendered.subject
    msg["From"] = config["email_from"]
    msg["To"] = config["email_to"]

    msg_alt = MIMEMultipart("alternative")
    msg.attach(msg_alt)
    msg_alt.attach(MIMEText(_plain_text_summary(batch), "plain"))
    msg_alt.attach(MIMEText(rendered.html, "html"))

    for cid, png_bytes in rendered.chart_pngs.items():
        img = MIMEImage(png_bytes, _subtype="png")
        img.add_header("Content-ID", f"<{cid}>")
        img.add_header("Content-Disposition", "inline", filename=f"{cid}.png")
        msg.attach(img)

    try:
        with smtplib.SMTP(config["smtp_host"], config["smtp_port"]) as server:
            if config["smtp_use_tls"]:
                server.starttls()
            if config["smtp_user"] and config["smtp_password"]:
                server.login(config["smtp_user"], config["smtp_password"])
            server.sendmail(config["email_from"], [config["email_to"]], msg.as_string())
        logger.info("Report emailed to %s", config["email_to"])
        return DeliveryResult(
            status="sent",
            sent_at=datetime.now(timezone.utc),
            raw_data={"mode": "smtp", "recipient": config["email_to"]},
        )
    except Exception as e:
        logger.error("SMTP delivery failed: %s", e, exc_info=True)
        return DeliveryResult(status="failed", error_message=str(e), raw_data={"mode": "smtp"})


def _deliver_resend(
    batch: ReportBatch,
    rendered: RenderedReport,
    config: dict,
) -> DeliveryResult:
    api_key = config["resend_api_key"]
    if not api_key:
        msg = "REPORT_RESEND_API_KEY is not set"
        logger.error(msg)
        return DeliveryResult(status="failed", error_message=msg, raw_data={"mode": "resend"})

    recipients = _parse_recipients(config["email_to"])
    if not recipients:
        msg = "REPORT_EMAIL_TO is not set"
        logger.error(msg)
        return DeliveryResult(status="failed", error_message=msg, raw_data={"mode": "resend"})

    # Charts are referenced in the HTML as cid:<cid>; attach each PNG with a
    # matching content_id so Resend embeds them inline rather than as downloads.
    attachments = [
        {
            "filename": f"{cid}.png",
            "content": base64.b64encode(png_bytes).decode("ascii"),
            "content_id": cid,
            "content_type": "image/png",
        }
        for cid, png_bytes in rendered.chart_pngs.items()
    ]

    payload: Dict[str, Any] = {
        "from": config["email_from"],
        "to": recipients,
        "subject": rendered.subject,
        "html": rendered.html,
        "text": _plain_text_summary(batch),
    }
    if attachments:
        payload["attachments"] = attachments

    try:
        response = requests.post(
            config["resend_api_url"],
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )
        if response.status_code >= 400:
            error = _resend_error_message(response)
            logger.error("Resend delivery failed (%s): %s", response.status_code, error)
            return DeliveryResult(
                status="failed",
                error_message=f"{response.status_code}: {error}",
                raw_data={"mode": "resend", "status_code": response.status_code},
            )

        message_id = None
        try:
            message_id = response.json().get("id")
        except ValueError:
            pass

        logger.info("Report emailed via Resend to %s (id=%s)", ", ".join(recipients), message_id)
        return DeliveryResult(
            status="sent",
            sent_at=datetime.now(timezone.utc),
            raw_data={"mode": "resend", "recipients": recipients, "message_id": message_id},
        )
    except requests.RequestException as e:
        logger.error("Resend delivery failed: %s", e, exc_info=True)
        return DeliveryResult(status="failed", error_message=str(e), raw_data={"mode": "resend"})


def _parse_recipients(raw: str) -> List[str]:
    return [addr.strip() for addr in (raw or "").split(",") if addr.strip()]


def _resend_error_message(response: requests.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text or "unknown error"
    if isinstance(body, dict):
        return body.get("message") or body.get("error") or response.text or "unknown error"
    return response.text or "unknown error"


def _plain_text_summary(batch: ReportBatch) -> str:
    lines = [batch.subject, ""]
    for summary in batch.site_summaries:
        power = summary.metrics.get("power", {})
        lines.append(f"{summary.location_name}:")
        lines.append(f"  Production: {power.get('production_kwh', 0):.1f} kWh")
        lines.append(f"  Consumption: {power.get('consumption_kwh', 0):.1f} kWh")
        lines.append(f"  Grid import: {power.get('import_kwh', 0):.1f} kWh")
        lines.append(f"  Grid export: {power.get('export_kwh', 0):.1f} kWh")
        lines.append("")
    return "\n".join(lines)
