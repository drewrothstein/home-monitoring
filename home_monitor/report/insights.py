"""Turn raw report metrics into compact visual stat cards.

Pure functions only: no SQL, no MJML. Each builder takes already-computed metric
dicts and returns either a :class:`StatCard` (emoji + 1-2 word label + value + a tiny
trend chip) or ``None`` when there is nothing worth showing. The daily email leads
with a grid of these cards so the interpretation reads at a glance; the underlying
numbers are secondary.

Tone drives color: ``good`` (green), ``bad`` (red/attention), ``neutral`` (default).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

# Significance defaults for conditional (water / propane / pool) cards: a metric is
# only "interesting" when it deviates meaningfully from its baseline AND clears a small
# absolute floor so tiny day-to-day noise stays out of the report.
DEFAULT_SIGNIFICANT_PCT = 0.25

GOOD = "good"
BAD = "bad"
NEUTRAL = "neutral"


@dataclass(frozen=True)
class StatCard:
    emoji: str
    label: str
    value: str
    tone: str = NEUTRAL
    trend: Optional[str] = None
    trend_tone: str = NEUTRAL
    # Small muted line under the trend chip (e.g. multi-site battery breakdown "83% / 20%").
    detail: Optional[str] = None

    def as_dict(self) -> Dict[str, Optional[str]]:
        return {
            "emoji": self.emoji,
            "label": self.label,
            "value": self.value,
            "tone": self.tone,
            "trend": self.trend,
            "trend_tone": self.trend_tone,
            "detail": self.detail,
        }


def is_significant(
    today: Optional[float],
    baseline: Optional[float],
    pct: float = DEFAULT_SIGNIFICANT_PCT,
    min_floor: float = 0.0,
) -> Tuple[bool, Optional[str]]:
    """Return ``(significant, direction)`` comparing ``today`` against ``baseline``.

    ``direction`` is ``"up"`` or ``"down"`` when significant, else ``None``.
    Significance requires the absolute change to clear ``min_floor`` and, when a
    positive baseline exists, the relative change to reach ``pct``.
    """
    if today is None:
        return False, None
    today = float(today)
    base = float(baseline or 0.0)
    diff = today - base

    if abs(diff) < min_floor:
        return False, None
    if base <= 0:
        return (today > min_floor), ("up" if today > 0 else None)
    if abs(diff) / base >= pct:
        return True, ("up" if diff > 0 else "down")
    return False, None


def _trend_chip(
    today: float,
    baseline: Optional[float],
    higher_is_good: bool,
) -> Tuple[Optional[str], str]:
    """Tiny "▲ 47%" comparison chip vs a baseline, with a color tone."""
    if not baseline or baseline <= 0:
        return None, NEUTRAL
    pct = (today - baseline) / baseline * 100.0
    if abs(pct) < 1:
        return "≈ avg", NEUTRAL
    going_up = pct > 0
    arrow = "▲" if going_up else "▼"
    tone = GOOD if going_up == higher_is_good else BAD
    return f"{arrow} {abs(pct):.0f}% vs avg", tone


def build_coverage_card(power: Dict[str, Any]) -> Optional[StatCard]:
    """Headline answer: did solar generation cover the day's usage?"""
    prod = float(power.get("production_kwh") or 0.0)
    cons = float(power.get("consumption_kwh") or 0.0)
    if cons <= 0:
        if prod > 0:
            return StatCard("☀️", "Produced", f"{prod:.1f} kWh", GOOD)
        return None

    coverage = prod / cons * 100.0
    if prod >= cons:
        return StatCard("✅", "Solar vs Usage", f"{coverage:.0f}%", GOOD)
    if coverage >= 70:
        return StatCard("🌤️", "Solar vs Usage", f"{coverage:.0f}%", NEUTRAL)
    return StatCard("⚠️", "Solar vs Usage", f"{coverage:.0f}%", BAD)


def build_energy_cards(
    power: Dict[str, Any],
    baseline_7d: Optional[Dict[str, Any]] = None,
) -> List[StatCard]:
    """Production, usage, and net grid flow cards (with vs-average trend chips)."""
    baseline_7d = baseline_7d or {}
    prod = float(power.get("production_kwh") or 0.0)
    cons = float(power.get("consumption_kwh") or 0.0)
    imp = float(power.get("import_kwh") or 0.0)
    exp = float(power.get("export_kwh") or 0.0)

    cards: List[StatCard] = []

    prod_trend, prod_tone = _trend_chip(
        prod, baseline_7d.get("avg_production_kwh"), higher_is_good=True
    )
    cards.append(StatCard("☀️", "Produced", f"{prod:.1f} kWh", NEUTRAL, prod_trend, prod_tone))

    cons_trend, cons_tone = _trend_chip(
        cons, baseline_7d.get("avg_consumption_kwh"), higher_is_good=False
    )
    cards.append(StatCard("🏠", "Used", f"{cons:.1f} kWh", NEUTRAL, cons_trend, cons_tone))

    net = imp - exp
    if net > 0.1:
        cards.append(StatCard("⚡", "Imported", f"{net:.1f} kWh", NEUTRAL))
    elif net < -0.1:
        cards.append(StatCard("🔌", "Exported", f"{abs(net):.1f} kWh", GOOD))
    else:
        cards.append(StatCard("⚖️", "Grid", "Balanced", GOOD))

    return cards


def build_battery_card(battery: Optional[Dict[str, Any]]) -> Optional[StatCard]:
    """Ending state of charge, with net energy stored/drawn as the trend chip."""
    if not battery or battery.get("end_soc_pct") is None:
        return None
    end = float(battery["end_soc_pct"])
    net = battery.get("net_stored_kwh")

    trend: Optional[str] = None
    trend_tone = NEUTRAL
    if net is not None and abs(net) >= 0.1:
        arrow = "▲" if net > 0 else "▼"
        trend = f"{arrow} {abs(net):.1f} kWh"
        trend_tone = GOOD if net > 0 else NEUTRAL

    # When a location spans multiple Tesla sites (e.g. FL), the single combined SOC can
    # hide very different per-site levels, so show a breakdown chip like "83% / 20%".
    detail: Optional[str] = None
    site_socs = battery.get("site_socs") or []
    if len(site_socs) > 1:
        detail = " / ".join(f"{s:.0f}%" for s in site_socs)

    return StatCard("🔋", "Battery", f"{end:.0f}%", NEUTRAL, trend, trend_tone, detail)


def build_water_card(water: Optional[Dict[str, Any]]) -> Optional[StatCard]:
    """Only appears when water use deviates >25% from the 7-day average."""
    if not water:
        return None
    today = water.get("today_gallons")
    avg = water.get("avg_7d_gallons")
    significant, _ = is_significant(today, avg, min_floor=20.0)
    if not significant:
        return None
    trend, tone = _trend_chip(float(today), avg, higher_is_good=False)
    return StatCard("💧", "Water", f"{today:.0f} gal", NEUTRAL, trend, tone)


def build_propane_card(propane: Optional[Dict[str, Any]]) -> Optional[StatCard]:
    """Only appears when propane draw deviates >25% from the 7-day average."""
    if not propane:
        return None
    today = propane.get("today_gallons")
    avg = propane.get("avg_7d_gallons")
    significant, _ = is_significant(today, avg, min_floor=2.0)
    if not significant:
        return None
    trend, tone = _trend_chip(float(today), avg, higher_is_good=False)
    return StatCard("🛢️", "Propane", f"{today:.1f} gal", NEUTRAL, trend, tone)


def build_pool_heater_card(pool: Optional[Dict[str, Any]]) -> Optional[StatCard]:
    """Pool heater runtime vs the past week. Filter/pump runtime is ignored (it runs
    on a fixed daily schedule, so it carries no signal)."""
    if not pool:
        return None
    today = pool.get("heater_hours") or 0.0
    avg = pool.get("avg_7d_heater_hours")
    significant, _ = is_significant(today, avg, min_floor=0.5)
    if not significant:
        return None
    trend, tone = _trend_chip(float(today), avg, higher_is_good=False)
    return StatCard("🔥", "Heater", f"{today:.1f} h", NEUTRAL, trend, tone)


def build_pool_temp_card(pool: Optional[Dict[str, Any]]) -> Optional[StatCard]:
    """Average pool temperature alongside the day's average air temperature."""
    if not pool:
        return None
    pool_t = pool.get("avg_pool_temp_f")
    air_t = pool.get("avg_air_temp_f")
    if pool_t is None or air_t is None:
        return None
    delta = pool_t - air_t
    if delta >= 0.5:
        trend, tone = f"▲ {delta:.0f}° vs air", GOOD
    elif delta <= -0.5:
        trend, tone = f"▼ {abs(delta):.0f}° vs air", NEUTRAL
    else:
        trend, tone = "≈ air", NEUTRAL
    return StatCard("🌡️", "Pool / Air", f"{pool_t:.0f}° / {air_t:.0f}°", NEUTRAL, trend, tone)
