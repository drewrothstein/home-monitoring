"""Tests for MJML report rendering."""

from datetime import datetime, timezone

from home_monitor.report.charts import (
    generate_hourly_power_chart,
    generate_period_energy_chart,
)
from home_monitor.report.mjml_builder import build_combined_report_mjml, hero_stats_mjml
from home_monitor.report.models import ReportBatch, SiteReportSummary
from home_monitor.report.periods import PeriodBounds
from home_monitor.report.renderer import render_report


def _sample_batch() -> ReportBatch:
    period = PeriodBounds(
        period_type="daily",
        timezone="America/New_York",
        start=datetime(2026, 6, 26, 4, 0, tzinfo=timezone.utc),
        end=datetime(2026, 6, 27, 4, 0, tzinfo=timezone.utc),
        label="Jun 26, 2026",
    )
    summary = SiteReportSummary(
        id=1,
        location_id=1,
        location_name="FL",
        period=period,
        metrics={
            "site_name": "FL",
            "period_label": "Jun 26, 2026",
            "timezone": "America/New_York",
            "power": {
                "production_kwh": 42.5,
                "consumption_kwh": 38.0,
                "import_kwh": 5.2,
                "export_kwh": 12.1,
                "max_production_kw": 8.5,
                "max_consumption_kw": 6.2,
            },
            "baseline_7d": {
                "avg_production_kwh": 40.0,
                "avg_consumption_kwh": 35.0,
                "avg_import_kwh": 4.0,
                "avg_export_kwh": 10.0,
                "days": 7,
            },
            "hero_card": {
                "emoji": "✅",
                "label": "Solar vs Usage",
                "value": "112%",
                "tone": "good",
                "trend": None,
                "trend_tone": "neutral",
            },
            "cards": [
                {
                    "emoji": "☀️",
                    "label": "Produced",
                    "value": "42.5 kWh",
                    "tone": "neutral",
                    "trend": "▲ 6% vs avg",
                    "trend_tone": "good",
                },
                {
                    "emoji": "🏠",
                    "label": "Used",
                    "value": "38.0 kWh",
                    "tone": "neutral",
                    "trend": "▲ 9% vs avg",
                    "trend_tone": "bad",
                },
                {
                    "emoji": "🔌",
                    "label": "Exported",
                    "value": "6.9 kWh",
                    "tone": "good",
                    "trend": None,
                    "trend_tone": "neutral",
                },
                {
                    "emoji": "🔋",
                    "label": "Battery",
                    "value": "78%",
                    "tone": "neutral",
                    "trend": "▲ 2.0 kWh",
                    "trend_tone": "good",
                },
                {
                    "emoji": "💧",
                    "label": "Water",
                    "value": "250 gal",
                    "tone": "neutral",
                    "trend": "▲ 67% vs avg",
                    "trend_tone": "bad",
                },
            ],
            "hourly_profile": [
                {"hour": h, "hour_label": "12p", "production_kw": 3.0, "consumption_kw": 2.0}
                for h in range(24)
            ],
            "battery": {
                "end_soc_pct": 78,
                "start_soc_pct": 76,
                "min_soc_pct": 45,
                "max_soc_pct": 92,
                "net_stored_kwh": 2.0,
                "total_capacity_kwh": 40.5,
            },
        },
    )
    return ReportBatch(
        period_type="daily",
        batch_period=period,
        site_summaries=[summary],
        generated_at=datetime(2026, 6, 27, 12, 0, tzinfo=timezone.utc),
        summary_ids=[1],
    )


def test_hero_stats_mjml_contains_values():
    mjml = hero_stats_mjml(
        {
            "production_kwh": 42.5,
            "consumption_kwh": 38.0,
            "import_kwh": 5.2,
            "export_kwh": 12.1,
        }
    )
    assert "42.5 kWh" in mjml
    assert "Production" in mjml


def test_build_combined_report_mjml_structure():
    batch = _sample_batch()
    mjml = build_combined_report_mjml(batch, chart_images={}, chart_legends={}, embed_base64=False)
    assert "<mjml>" in mjml
    assert "FL" in mjml
    assert "Daily Report" in mjml
    # Card-first layout: hero coverage card, metric cards, and conditional water card.
    assert "112%" in mjml  # hero coverage value
    assert "42.5 kWh" in mjml and "Produced" in mjml
    assert "Water" in mjml and "250 gal" in mjml
    # Cards stack on mobile (no mj-group), and top-circuits is gone.
    assert "mj-group" not in mjml
    assert "Top Circuits" not in mjml


def test_render_report_produces_html():
    batch = _sample_batch()
    rendered = render_report(batch, embed_base64=True)
    assert "<html" in rendered.html.lower() or "<!doctype" in rendered.html.lower()
    assert "FL" in rendered.html
    assert rendered.subject.startswith("Home Monitor Daily Report")


def test_generate_hourly_power_chart_png():
    hourly = [
        {"hour": h, "hour_label": f"{h}a", "production_kw": h * 0.5, "consumption_kw": 2.0}
        for h in range(24)
    ]
    png = generate_hourly_power_chart(hourly, "FL")
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_yearly_chart_aggregates_to_months():
    breakdown = [
        {"date": f"2025-{m:02d}-15", "production_kwh": m * 10, "consumption_kwh": m * 8}
        for m in range(1, 13)
    ]
    png = generate_period_energy_chart(breakdown, "FL", "yearly")
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
