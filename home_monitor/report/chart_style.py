"""Shared matplotlib styling for report charts."""

from __future__ import annotations

import matplotlib as mpl

# Brand colors aligned with email template
COLORS = {
    "production": "#16a34a",
    "consumption": "#2563eb",
    "import": "#ea580c",
    "export": "#9333ea",
    "text": "#1e293b",
    "muted": "#64748b",
    "grid": "#e2e8f0",
    "background": "#f8fafc",
}

MONTH_NAMES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def apply_chart_style() -> None:
    """Configure matplotlib for clean, email-friendly charts."""
    mpl.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": [
                "Helvetica Neue",
                "Helvetica",
                "Arial",
                "DejaVu Sans",
            ],
            "font.size": 10,
            "axes.titlesize": 13,
            "axes.titleweight": "bold",
            "axes.labelsize": 10,
            "axes.labelcolor": COLORS["text"],
            "axes.titlecolor": COLORS["text"],
            "xtick.labelsize": 9,
            "ytick.labelsize": 9,
            "xtick.color": COLORS["muted"],
            "ytick.color": COLORS["muted"],
            "figure.facecolor": "white",
            "axes.facecolor": COLORS["background"],
            "axes.edgecolor": COLORS["grid"],
            "axes.spines.top": False,
            "axes.spines.right": False,
            "grid.color": COLORS["grid"],
            "grid.linewidth": 0.8,
            "lines.linewidth": 2.2,
            "savefig.dpi": 144,
        }
    )
