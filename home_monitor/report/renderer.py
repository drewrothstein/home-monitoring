"""Render report batches to email-safe HTML."""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Tuple

from mjml import mjml2html

from home_monitor.report.charts import generate_site_charts
from home_monitor.report.mjml_builder import build_combined_report_mjml
from home_monitor.report.models import ReportBatch

logger = logging.getLogger(__name__)


@dataclass
class RenderedReport:
    html: str
    subject: str
    chart_pngs: Dict[str, bytes] = field(default_factory=dict)
    embed_base64: bool = False


def render_report(batch: ReportBatch, embed_base64: bool = False) -> RenderedReport:
    """
    Render a report batch to HTML.

    Args:
        batch: Report data
        embed_base64: If True, embed chart PNGs as base64 (file preview mode)
    """
    chart_pngs: Dict[str, bytes] = {}
    chart_b64: Dict[str, str] = {}
    chart_legends: Dict[str, List[Tuple[str, str]]] = {}

    for summary in batch.site_summaries:
        site_charts, site_legends = generate_site_charts(
            summary.location_name,
            summary.metrics,
            batch.period_type,
        )
        chart_pngs.update(site_charts)
        chart_legends.update(site_legends)
        if embed_base64:
            for cid, png_bytes in site_charts.items():
                chart_b64[cid] = base64.b64encode(png_bytes).decode("ascii")

    generated_at = (batch.generated_at or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M UTC")

    mjml = build_combined_report_mjml(
        batch,
        chart_images=chart_b64 if embed_base64 else {cid: cid for cid in chart_pngs},
        chart_legends=chart_legends,
        embed_base64=embed_base64,
        generated_at=generated_at,
    )

    try:
        html = mjml2html(mjml)
    except Exception as e:
        logger.error("MJML compilation failed: %s", e, exc_info=True)
        raise

    return RenderedReport(
        html=html,
        subject=batch.subject,
        chart_pngs=chart_pngs,
        embed_base64=embed_base64,
    )
