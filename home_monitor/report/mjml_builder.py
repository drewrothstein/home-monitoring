"""Compose MJML email documents from report data (no template engine)."""

from __future__ import annotations

import html
from typing import Any, Dict, List, Optional, Tuple

from home_monitor.report.chart_style import COLORS
from home_monitor.report.models import ReportBatch, SiteReportSummary
from home_monitor.report.periods import period_type_title

FONT_STACK = (
    "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, "
    "'Helvetica Neue', Helvetica, Arial, sans-serif"
)

STAT_CARDS = [
    ("Production", "production_kwh", "kWh", COLORS["production"], "#f0fdf4"),
    ("Consumption", "consumption_kwh", "kWh", COLORS["consumption"], "#eff6ff"),
    ("Grid Import", "import_kwh", "kWh", COLORS["import"], "#fff7ed"),
    ("Grid Export", "export_kwh", "kWh", COLORS["export"], "#faf5ff"),
]


def _esc(value: Any) -> str:
    return html.escape(str(value))


def _fmt_num(value: Optional[float], decimals: int = 1, suffix: str = "") -> str:
    if value is None:
        return "—"
    return f"{value:.{decimals}f}{suffix}"


def hero_stats_mjml(power: Dict[str, Any]) -> str:
    """Four stat cards in a row."""
    columns = []
    for label, key, unit, color, bg in STAT_CARDS:
        value = _fmt_num(power.get(key), suffix=f" {unit}")
        columns.append(
            f"""
    <mj-column width="25%" background-color="{bg}" padding="14px 8px">
      <mj-text align="center" font-size="11px" font-weight="600"
        color="{COLORS['muted']}" letter-spacing="0.5px"
        text-transform="uppercase" padding-bottom="6px">{_esc(label)}</mj-text>
      <mj-text align="center" font-size="24px" font-weight="700"
        color="{color}" line-height="1.2" padding-top="0">{_esc(value)}</mj-text>
    </mj-column>
            """
        )
    return f'<mj-group width="100%">{"".join(columns)}</mj-group>'


def chart_legend_mjml(items: List[Tuple[str, str]]) -> str:
    """HTML legend rendered below chart (replaces unreadable matplotlib legend)."""
    if not items:
        return ""
    parts = []
    for label, color in items:
        parts.append(
            f'<span style="display:inline-block;margin-right:16px;font-size:13px;'
            f'color:{COLORS["text"]};">'
            f'<span style="display:inline-block;width:10px;height:10px;'
            f"background-color:{color};border-radius:2px;margin-right:6px;"
            f'vertical-align:middle;"></span>{_esc(label)}</span>'
        )
    return f"""
    <mj-text align="center" padding="4px 0 12px 0" line-height="1.8">
      {''.join(parts)}
    </mj-text>
    """


def metrics_table_mjml(headers: List[str], rows: List[List[str]]) -> str:
    header_row = "".join(
        f'<td style="padding:10px 12px;font-weight:600;font-size:12px;color:{COLORS["muted"]};'
        f'text-transform:uppercase;letter-spacing:0.4px;background-color:#f1f5f9;">'
        f"{_esc(h)}</td>"
        for h in headers
    )
    body_rows = ""
    for i, row in enumerate(rows):
        bg = "#ffffff" if i % 2 == 0 else "#f8fafc"
        cells = "".join(
            f'<td style="padding:10px 12px;font-size:14px;color:{COLORS["text"]};'
            f'border-bottom:1px solid #e2e8f0;background-color:{bg};">{_esc(c)}</td>'
            for c in row
        )
        body_rows += f"<tr>{cells}</tr>"

    return f"""
    <mj-table cellpadding="0" cellspacing="0" width="100%" font-family="{FONT_STACK}">
      <tr>{header_row}</tr>
      {body_rows}
    </mj-table>
    """


def chart_image_mjml(cid: str, alt: str, embed_base64: Optional[str] = None) -> str:
    if embed_base64:
        src = f"data:image/png;base64,{embed_base64}"
    else:
        src = f"cid:{cid}"
    return f"""
    <mj-image src="{src}" alt="{_esc(alt)}" width="560px" padding="8px 0 0 0" border-radius="4px" />
    """


def _detail_line(text: str) -> str:
    return f"""
    <mj-text font-size="14px" color="{COLORS['text']}" line-height="1.6" padding="4px 0">{_esc(text)}</mj-text>
    """


def site_section_mjml(
    summary: SiteReportSummary,
    chart_images: Dict[str, str],
    chart_legends: Dict[str, List[Tuple[str, str]]],
    period_type: str,
    embed_base64: bool = False,
) -> str:
    """Build MJML section for one site."""
    metrics = summary.metrics
    if summary.status == "failed":
        return f"""
        <mj-section background-color="#fef2f2" padding="20px">
          <mj-column>
            <mj-text font-size="20px" font-weight="700" color="#dc2626">{_esc(summary.location_name)}</mj-text>
            <mj-text color="#dc2626" font-size="14px">Failed to compute metrics: {_esc(summary.error_message)}</mj-text>
          </mj-column>
        </mj-section>
        """

    parts: List[str] = []
    parts.append(
        f"""
    <mj-section background-color="#ffffff" padding="24px 20px 8px 20px">
      <mj-column>
        <mj-text font-size="22px" font-weight="700" color="{COLORS['text']}" padding-bottom="4px">
          {_esc(summary.location_name)}
        </mj-text>
        <mj-text font-size="13px" color="{COLORS['muted']}">
          {_esc(metrics.get("period_label", ""))} · {_esc(metrics.get("timezone", ""))}
        </mj-text>
      </mj-column>
    </mj-section>
    """
    )

    power = metrics.get("power", {})
    if power:
        parts.append(
            f"""
        <mj-section background-color="#ffffff" padding="8px 12px">
          {hero_stats_mjml(power)}
        </mj-section>
        """
        )
        peak_text = (
            f"Peak production {_fmt_num(power.get('max_production_kw'), suffix=' kW')} · "
            f"Peak consumption {_fmt_num(power.get('max_consumption_kw'), suffix=' kW')}"
        )
        parts.append(
            f"""
        <mj-section background-color="#ffffff" padding="0 20px 8px 20px">
          <mj-column>
            <mj-text font-size="13px" color="{COLORS['muted']}" align="center">{_esc(peak_text)}</mj-text>
          </mj-column>
        </mj-section>
        """
        )

    site_key = summary.location_name.lower()
    for cid, b64 in chart_images.items():
        if not cid.startswith(f"chart-{site_key}"):
            continue
        if "hourly" in cid:
            chart_title = "Power Through the Day"
        else:
            chart_title = "Monthly Energy" if period_type == "yearly" else "Daily Energy"

        legend_html = chart_legend_mjml(chart_legends.get(cid, []))
        parts.append(
            f"""
        <mj-section background-color="#ffffff" padding="12px 20px">
          <mj-column background-color="{COLORS['background']}" border-radius="8px" padding="16px">
            <mj-text font-size="15px" font-weight="600" color="{COLORS['text']}" padding-bottom="8px">
              {chart_title}
            </mj-text>
            {chart_image_mjml(cid, f"{summary.location_name} {chart_title}", b64 if embed_base64 else None)}
            {legend_html}
          </mj-column>
        </mj-section>
            """
        )

    detail_lines: List[str] = []
    battery = metrics.get("battery")
    if battery:
        pct = "%"
        detail_lines.append(
            f"Battery {_fmt_num(battery.get('end_soc_pct'), decimals=0, suffix=pct)} "
            f"(low {_fmt_num(battery.get('min_soc_pct'), decimals=0, suffix=pct)}, "
            f"high {_fmt_num(battery.get('max_soc_pct'), decimals=0, suffix=pct)})"
        )

    water = metrics.get("water")
    if water:
        detail_lines.append(
            f"Water {_fmt_num(water.get('total_gallons'), suffix=' gal')} total · "
            f"{_fmt_num(water.get('avg_daily_gallons'), suffix=' gal/day avg')} · "
            f"{water.get('sprinkler_days', 0)} sprinkler days"
        )

    propane = metrics.get("propane")
    if propane:
        line = (
            f"Propane {_fmt_num(propane.get('start_pct'), decimals=0, suffix='%')} → "
            f"{_fmt_num(propane.get('end_pct'), decimals=0, suffix='%')}"
        )
        if propane.get("end_gallons") is not None:
            line += f" ({_fmt_num(propane.get('end_gallons'), suffix=' gal')})"
        detail_lines.append(line)

    pool = metrics.get("pool")
    if pool:
        pool_parts = []
        if pool.get("avg_pool_temp_f"):
            pool_parts.append(f"Pool {_fmt_num(pool['avg_pool_temp_f'], suffix='°F')}")
        if pool.get("avg_spa_temp_f"):
            pool_parts.append(f"Spa {_fmt_num(pool['avg_spa_temp_f'], suffix='°F')}")
        pool_parts.append(f"Pump {_fmt_num(pool.get('pump_hours'), suffix=' h')}")
        pool_parts.append(f"Heater {_fmt_num(pool.get('heater_hours'), suffix=' h')}")
        detail_lines.append(" · ".join(pool_parts))

    tempest = metrics.get("tempest")
    if tempest:
        detail_lines.append(
            f"Avg solar irradiance {_fmt_num(tempest.get('avg_ghi'), suffix=' W/m²')}"
        )

    if detail_lines:
        detail_content = "".join(_detail_line(line) for line in detail_lines)
        parts.append(
            f"""
        <mj-section background-color="#ffffff" padding="0 20px 12px 20px">
          <mj-column background-color="#f1f5f9" border-radius="8px" padding="12px 16px">
            {detail_content}
          </mj-column>
        </mj-section>
            """
        )

    span = metrics.get("span")
    if span and span.get("top_circuits"):
        rows = [[c["name"], _fmt_num(c["energy_kwh"], suffix=" kWh")] for c in span["top_circuits"]]
        parts.append(
            f"""
        <mj-section background-color="#ffffff" padding="8px 20px 16px 20px">
          <mj-column>
            <mj-text font-size="15px" font-weight="600" color="{COLORS['text']}" padding-bottom="8px">
              Top Circuits by Energy
            </mj-text>
            {metrics_table_mjml(["Circuit", "Energy"], rows)}
          </mj-column>
        </mj-section>
            """
        )

    parts.append(
        """
    <mj-section padding="0 20px">
      <mj-column>
        <mj-divider border-color="#e2e8f0" border-width="1px" />
      </mj-column>
    </mj-section>
    """
    )

    return "".join(parts)


def build_combined_report_mjml(
    batch: ReportBatch,
    chart_images: Dict[str, str],
    chart_legends: Dict[str, List[Tuple[str, str]]],
    embed_base64: bool = False,
    generated_at: str = "",
) -> str:
    """Build full MJML document for combined multi-site report."""
    title = period_type_title(batch.period_type)
    site_sections = "".join(
        site_section_mjml(
            s,
            chart_images,
            chart_legends,
            batch.period_type,
            embed_base64=embed_base64,
        )
        for s in batch.site_summaries
    )

    return f"""
<mjml>
  <mj-head>
    <mj-title>Home Monitor {title} Report</mj-title>
    <mj-attributes>
      <mj-all font-family="{FONT_STACK}" />
      <mj-text font-size="15px" color="{COLORS['text']}" line-height="1.55" />
      <mj-section padding="0" />
    </mj-attributes>
    <mj-style inline="inline">
      body {{ -webkit-font-smoothing: antialiased; }}
    </mj-style>
  </mj-head>
  <mj-body background-color="#f1f5f9" width="600px">
    <mj-section background-color="#0f172a" padding="28px 24px">
      <mj-column>
        <mj-text color="#94a3b8" font-size="12px" font-weight="600"
          letter-spacing="1px" text-transform="uppercase" align="center" padding-bottom="8px">
          Home Monitor
        </mj-text>
        <mj-text color="#ffffff" font-size="26px" font-weight="700" align="center" line-height="1.3">
          {title} Report
        </mj-text>
        <mj-text color="#cbd5e1" font-size="15px" align="center" padding-top="8px">
          {_esc(batch.batch_period.label)}
        </mj-text>
      </mj-column>
    </mj-section>
    <mj-section padding="16px 0 0 0">
      <mj-column>
        <mj-spacer height="4px" />
      </mj-column>
    </mj-section>
    {site_sections}
    <mj-section background-color="#f1f5f9" padding="20px">
      <mj-column>
        <mj-text font-size="12px" color="#94a3b8" align="center">
          Generated {_esc(generated_at)}
        </mj-text>
      </mj-column>
    </mj-section>
  </mj-body>
</mjml>
"""
