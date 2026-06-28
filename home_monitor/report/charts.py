"""Matplotlib chart generation for email reports."""

from __future__ import annotations

import io
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.ticker as mticker  # noqa: E402

from home_monitor.report.chart_style import COLORS, MONTH_NAMES, apply_chart_style  # noqa: E402

apply_chart_style()


def _fig_to_png(fig: plt.Figure) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor="white", edgecolor="none")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def _empty_chart(site_name: str, message: str) -> bytes:
    fig, ax = plt.subplots(figsize=(7, 2.2))
    ax.text(
        0.5,
        0.5,
        message,
        ha="center",
        va="center",
        transform=ax.transAxes,
        fontsize=11,
        color=COLORS["muted"],
    )
    ax.set_title(site_name, fontsize=13, fontweight="bold", color=COLORS["text"])
    ax.axis("off")
    return _fig_to_png(fig)


def _month_label(yyyy_mm: str) -> str:
    year, month = yyyy_mm.split("-")
    return MONTH_NAMES[int(month) - 1]


def _prepare_period_series(
    daily_breakdown: List[Dict[str, Any]],
    period_type: str,
) -> Tuple[List[str], List[float], List[float], str, float]:
    """
    Prepare x labels and y series for period charts.

    Returns: labels, production, consumption, title_suffix, fig_width
    """
    if not daily_breakdown:
        return [], [], [], "Energy", 7.0

    if period_type == "yearly":
        monthly: Dict[str, Dict[str, float]] = defaultdict(
            lambda: {"production_kwh": 0.0, "consumption_kwh": 0.0}
        )
        for row in daily_breakdown:
            key = row["date"][:7]
            monthly[key]["production_kwh"] += row.get("production_kwh", 0) or 0
            monthly[key]["consumption_kwh"] += row.get("consumption_kwh", 0) or 0
        keys = sorted(monthly.keys())
        labels = [_month_label(k) for k in keys]
        production = [monthly[k]["production_kwh"] for k in keys]
        consumption = [monthly[k]["consumption_kwh"] for k in keys]
        return labels, production, consumption, "Monthly Energy", 7.5

    labels = [row["date"][5:] for row in daily_breakdown]  # MM-DD
    production = [row.get("production_kwh", 0) or 0 for row in daily_breakdown]
    consumption = [row.get("consumption_kwh", 0) or 0 for row in daily_breakdown]

    n = len(labels)
    if period_type == "monthly" and n > 20:
        fig_width = min(10.0, 6.0 + n * 0.12)
        return labels, production, consumption, "Daily Energy", fig_width

    if period_type == "weekly":
        friendly_labels = []
        for row in daily_breakdown:
            dt = datetime.strptime(row["date"], "%Y-%m-%d")
            friendly_labels.append(f"{dt.strftime('%a')} {dt.month}/{dt.day}")
        return friendly_labels, production, consumption, "Daily Energy", 7.0

    return labels, production, consumption, "Daily Energy", 7.0


def _sparse_xticks(n: int, period_type: str) -> int:
    """Label every Nth tick to avoid overlap."""
    if period_type == "yearly":
        return 1
    if period_type == "monthly":
        if n <= 14:
            return 1
        if n <= 31:
            return 2
        return max(1, n // 12)
    if n <= 7:
        return 1
    return max(1, n // 10)


def generate_period_energy_chart(
    daily_breakdown: List[Dict[str, Any]],
    site_name: str,
    period_type: str,
) -> bytes:
    """Grouped bar chart of energy by day or month depending on period."""
    labels, production, consumption, title_suffix, fig_width = _prepare_period_series(
        daily_breakdown, period_type
    )
    if not labels:
        return _empty_chart(site_name, "No energy data for this period")

    fig_height = 3.6 if period_type != "yearly" else 3.2
    fig, ax = plt.subplots(figsize=(fig_width, fig_height))
    x = range(len(labels))
    width = 0.38 if len(labels) <= 12 else 0.42

    ax.bar(
        [i - width / 2 for i in x],
        production,
        width,
        color=COLORS["production"],
        label="_nolegend_",
        zorder=3,
    )
    ax.bar(
        [i + width / 2 for i in x],
        consumption,
        width,
        color=COLORS["consumption"],
        label="_nolegend_",
        zorder=3,
    )

    ax.set_ylabel("kWh", fontweight="500")
    ax.set_title(f"{site_name} — {title_suffix}", pad=12)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.0f}"))
    ax.grid(axis="y", alpha=0.9, zorder=0)

    step = _sparse_xticks(len(labels), period_type)
    tick_positions = list(x)[::step]
    tick_labels = [labels[i] for i in tick_positions]
    ax.set_xticks(tick_positions)
    ax.set_xticklabels(tick_labels, rotation=0 if len(labels) <= 12 else 45, ha="center")

    fig.tight_layout()
    return _fig_to_png(fig)


def generate_hourly_power_chart(
    hourly_profile: List[Dict[str, Any]],
    site_name: str,
) -> bytes:
    """Line chart of average kW by hour of day (daily reports)."""
    if not hourly_profile:
        return _empty_chart(site_name, "No hourly power data")

    hours = [p["hour_label"] for p in hourly_profile]
    production = [p.get("production_kw", 0) or 0 for p in hourly_profile]
    consumption = [p.get("consumption_kw", 0) or 0 for p in hourly_profile]

    fig, ax = plt.subplots(figsize=(7.5, 3.4))
    x = range(len(hours))

    ax.plot(x, production, color=COLORS["production"], label="_nolegend_", zorder=3)
    ax.fill_between(x, production, alpha=0.12, color=COLORS["production"], zorder=2)
    ax.plot(x, consumption, color=COLORS["consumption"], label="_nolegend_", zorder=3)
    ax.fill_between(x, consumption, alpha=0.10, color=COLORS["consumption"], zorder=2)

    ax.set_ylabel("Avg kW", fontweight="500")
    ax.set_title(f"{site_name} — Power Through the Day", pad=12)
    step = 2
    tick_x = list(x)[::step]
    ax.set_xticks(tick_x)
    ax.set_xticklabels([hours[i] for i in tick_x], rotation=0, fontsize=8)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.1f}"))
    ax.grid(axis="y", alpha=0.9, zorder=0)
    ax.set_xlim(-0.5, len(hours) - 0.5)

    fig.tight_layout()
    return _fig_to_png(fig)


def generate_pool_temp_air_chart(
    temp_profile: List[Dict[str, Any]],
    site_name: str,
) -> bytes:
    """Line chart comparing pool temperature to air temperature through the day."""
    points = [
        p
        for p in temp_profile
        if p.get("pool_temp_f") is not None or p.get("air_temp_f") is not None
    ]
    if not points:
        return _empty_chart(site_name, "No pool temperature data")

    hours = [p["hour_label"] for p in temp_profile]
    pool_temp = [p.get("pool_temp_f") for p in temp_profile]
    air_temp = [p.get("air_temp_f") for p in temp_profile]

    fig, ax = plt.subplots(figsize=(7.5, 3.4))
    x = range(len(hours))

    ax.plot(x, pool_temp, color=COLORS["consumption"], label="_nolegend_", zorder=3)
    ax.plot(
        x,
        air_temp,
        color=COLORS["import"],
        label="_nolegend_",
        zorder=3,
        linestyle="--",
    )

    ax.set_ylabel("°F", fontweight="500")
    ax.set_title(f"{site_name} — Pool vs Air Temperature", pad=12)
    step = 2
    tick_x = list(x)[::step]
    ax.set_xticks(tick_x)
    ax.set_xticklabels([hours[i] for i in tick_x], rotation=0, fontsize=8)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.0f}"))
    ax.grid(axis="y", alpha=0.9, zorder=0)
    ax.set_xlim(-0.5, len(hours) - 0.5)

    fig.tight_layout()
    return _fig_to_png(fig)


def generate_site_charts(
    site_name: str,
    metrics: Dict[str, Any],
    period_type: str,
) -> Tuple[Dict[str, bytes], Dict[str, List[Tuple[str, str]]]]:
    """Generate chart PNGs for a site. Returns (cid -> bytes, cid -> legend items)."""
    charts: Dict[str, bytes] = {}
    legends: Dict[str, List[Tuple[str, str]]] = {}
    site_key = site_name.lower()
    prod_cons_legend = [
        ("Production", COLORS["production"]),
        ("Consumption", COLORS["consumption"]),
    ]

    if period_type == "daily":
        hourly = metrics.get("hourly_profile")
        if hourly:
            cid = f"chart-{site_key}-hourly"
            charts[cid] = generate_hourly_power_chart(hourly, site_name)
            legends[cid] = prod_cons_legend

        pool = metrics.get("pool") or {}
        temp_profile = pool.get("temp_profile")
        if temp_profile and any(
            p.get("pool_temp_f") is not None or p.get("air_temp_f") is not None
            for p in temp_profile
        ):
            cid = f"chart-{site_key}-pooltemp"
            charts[cid] = generate_pool_temp_air_chart(temp_profile, site_name)
            legends[cid] = [
                ("Pool", COLORS["consumption"]),
                ("Air", COLORS["import"]),
            ]
        return charts, legends

    breakdown = metrics.get("daily_breakdown")
    if breakdown:
        cid = f"chart-{site_key}-energy"
        charts[cid] = generate_period_energy_chart(breakdown, site_name, period_type)
        legends[cid] = prod_cons_legend

    return charts, legends


# Backward-compatible alias for tests
def generate_daily_energy_chart(
    daily_breakdown: List[Dict[str, Any]],
    site_name: str,
    title: str = "Daily Energy",
) -> bytes:
    return generate_period_energy_chart(daily_breakdown, site_name, "weekly")
