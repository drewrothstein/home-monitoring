"""Tests for report stat-card builders."""

from home_monitor.report import insights


def test_is_significant_above_threshold():
    significant, direction = insights.is_significant(200, 150, min_floor=20.0)
    assert significant is True
    assert direction == "up"


def test_is_significant_below_threshold_when_change_small():
    # 10% change is under the 25% default threshold.
    significant, direction = insights.is_significant(160, 150, min_floor=20.0)
    assert significant is False
    assert direction is None


def test_is_significant_respects_absolute_floor():
    # Large relative change but tiny absolute change stays quiet.
    significant, _ = insights.is_significant(3, 2, min_floor=20.0)
    assert significant is False


def test_coverage_card_surplus_is_good():
    power = {"production_kwh": 42.5, "consumption_kwh": 38.0}
    card = insights.build_coverage_card(power)
    assert card is not None
    assert card.emoji == "✅"
    assert card.value == "112%"
    assert card.tone == insights.GOOD


def test_coverage_card_shortfall_is_bad():
    power = {"production_kwh": 10.0, "consumption_kwh": 40.0}
    card = insights.build_coverage_card(power)
    assert card is not None
    assert card.value == "25%"
    assert card.tone == insights.BAD
    assert card.emoji == "⚠️"


def test_energy_cards_include_trend_chips():
    power = {"production_kwh": 42.5, "consumption_kwh": 38.0, "import_kwh": 0.0, "export_kwh": 6.9}
    cards = insights.build_energy_cards(
        power, {"avg_production_kwh": 40.0, "avg_consumption_kwh": 35.0}
    )
    labels = {c.label: c for c in cards}
    assert labels["Produced"].value == "42.5 kWh"
    assert "vs avg" in labels["Produced"].trend
    # More usage than average is flagged as a "bad" trend.
    assert labels["Used"].trend_tone == insights.BAD
    # Net export shows up as an Exported card with a good tone.
    assert "Exported" in labels
    assert labels["Exported"].tone == insights.GOOD


def test_battery_card_net_stored_chip():
    card = insights.build_battery_card(
        {"end_soc_pct": 78.0, "min_soc_pct": 45.0, "net_stored_kwh": 2.4}
    )
    assert card is not None
    assert card.value == "78%"
    assert card.trend == "▲ 2.4 kWh"
    assert card.trend_tone == insights.GOOD


def test_battery_card_single_site_has_no_breakdown():
    card = insights.build_battery_card({"end_soc_pct": 51.0, "site_socs": [51.0]})
    assert card is not None
    assert card.detail is None


def test_battery_card_multi_site_shows_breakdown():
    # FL spans two Tesla sites; combined value hides the per-site split.
    card = insights.build_battery_card({"end_soc_pct": 41.0, "site_socs": [82.8, 19.8]})
    assert card is not None
    assert card.value == "41%"
    assert card.detail == "83% / 20%"


def test_water_card_only_when_significant():
    assert insights.build_water_card({"today_gallons": 155, "avg_7d_gallons": 150}) is None

    card = insights.build_water_card({"today_gallons": 300, "avg_7d_gallons": 150})
    assert card is not None
    assert card.emoji == "💧"
    assert card.value == "300 gal"


def test_pool_heater_card_significant_vs_week():
    card = insights.build_pool_heater_card({"heater_hours": 3.0, "avg_7d_heater_hours": 0.0})
    assert card is not None
    assert card.value == "3.0 h"
    assert card.emoji == "🔥"


def test_pool_temp_card_shows_pool_and_air():
    card = insights.build_pool_temp_card({"avg_pool_temp_f": 87.0, "avg_air_temp_f": 90.0})
    assert card is not None
    assert card.value == "87° / 90°"
    assert "vs air" in card.trend
