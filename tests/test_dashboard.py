"""Unit tests for dashboard generation."""

import pytest
from grafanalib.core import GridPos, SqlTarget

from scripts.generate_dashboard import (
    _annotation_time_sql_exprs,
    build_sql_codified_annotations,
    color_override,
    create_dashboard,
    create_panels,
    DEFAULT_ANNOTATIONS_ALL_DAY_TZ,
    sql_battery_soc,
    sql_current_production_consumption_export,
    SQL_BATTERY_SOC_TIMESERIES,
    SQL_SPRINKLER_RUNS_ANNOTATIONS,
    sql_tesla_exported_today_kwh,
    sql_target,
    threshold,
)


class TestThreshold:
    """Tests for threshold function."""

    def test_threshold_with_value(self):
        """Test threshold creation with a value."""
        result = threshold("green", 50.0)
        assert result == {"color": "green", "value": 50.0}

    def test_threshold_without_value(self):
        """Test threshold creation without a value (base threshold)."""
        result = threshold("red")
        assert result == {"color": "red", "value": None}

    def test_threshold_with_none_value(self):
        """Test threshold creation with explicit None value."""
        result = threshold("yellow", None)
        assert result == {"color": "yellow", "value": None}


class TestSqlTarget:
    """Tests for sql_target function."""

    def test_sql_target_defaults(self):
        """Test sql_target with default parameters."""
        sql = "SELECT * FROM test"
        result = sql_target(sql)

        assert isinstance(result, SqlTarget)
        assert result.rawSql == sql
        assert result.refId == "A"
        assert result.format == "time_series"
        assert result.datasource == "Home Monitor PostgreSQL"

    def test_sql_target_custom_params(self):
        """Test sql_target with custom parameters."""
        sql = "SELECT * FROM test WHERE id = $id"
        result = sql_target(sql, ref_id="B", format_mode="table")

        assert isinstance(result, SqlTarget)
        assert result.rawSql == sql
        assert result.refId == "B"
        assert result.format == "table"
        assert result.datasource == "Home Monitor PostgreSQL"


class TestSqlTeslaExportedToday:
    """Tesla daily export query (integrates power_exported, ignores Grafana range)."""

    def test_sql_contains_tesla_source_and_timezone_literal(self):
        sql = sql_tesla_exported_today_kwh("America/Chicago")
        assert "source = 'tesla'" in sql
        assert "'America/Chicago'" in sql
        assert "Exported Today (kWh)" in sql
        assert "$location" in sql


class TestSqlCurrentProductionConsumptionExport:
    """Merged top-row gauge query (production, consumption, Tesla export today)."""

    def test_sql_includes_all_three_metrics(self):
        sql = sql_current_production_consumption_export("America/Los_Angeles")
        assert "Production (kW)" in sql
        assert "Consumption (kW)" in sql
        assert "Exported Today (kWh)" in sql
        assert "source = 'tesla'" in sql
        assert "'America/Los_Angeles'" in sql
        assert "$source" in sql


class TestSqlBatterySocTimeseries:
    """Tests for SQL_BATTERY_SOC_TIMESERIES (Grafana panel query)."""

    def test_battery_soc_timeseries_dedupes_and_labels_by_bank(self):
        """One series per bank/index per minute, not arbitrary row numbers."""
        assert "DISTINCT ON" in SQL_BATTERY_SOC_TIMESERIES
        assert "battery_banks" in SQL_BATTERY_SOC_TIMESERIES
        assert "battery_bank_id" in SQL_BATTERY_SOC_TIMESERIES
        assert "raw_data->'battery'->>'index'" in SQL_BATTERY_SOC_TIMESERIES
        assert "energy_site_id" in SQL_BATTERY_SOC_TIMESERIES
        assert "Single" in SQL_BATTERY_SOC_TIMESERIES
        assert "Double" in SQL_BATTERY_SOC_TIMESERIES
        assert "bbc.n" in SQL_BATTERY_SOC_TIMESERIES


class TestSqlBatterySoc:
    """Tests for sql_battery_soc function."""

    def test_sql_battery_soc_basic(self):
        """Test SQL generation for battery SOC."""
        sql = sql_battery_soc(0)

        assert isinstance(sql, str)
        assert "battery_index = 0" in sql
        assert "Battery 1" in sql
        assert "battery_readings" in sql
        assert "battery_banks" in sql

    def test_sql_battery_soc_multiple_batteries(self):
        """Test SQL generation for different battery indices."""
        sql0 = sql_battery_soc(0)
        sql1 = sql_battery_soc(1)

        assert "battery_index = 0" in sql0
        assert "battery_index = 1" in sql1
        assert "Battery 1" in sql0
        assert "Battery 2" in sql1


class TestColorOverride:
    """Tests for color_override function."""

    def test_color_override(self):
        """Test color override creation."""
        result = color_override(".*Production.*", "green")

        assert isinstance(result, dict)
        assert result["matcher"]["id"] == "byRegexp"
        assert result["matcher"]["options"] == ".*Production.*"
        assert len(result["properties"]) == 1
        assert result["properties"][0]["id"] == "color"
        assert result["properties"][0]["value"]["fixedColor"] == "green"
        assert result["properties"][0]["value"]["mode"] == "fixed"


class TestCreatePanels:
    """Tests for create_panels function."""

    def test_create_panels_returns_list(self):
        """Test that create_panels returns a list."""
        panels = create_panels()

        assert isinstance(panels, list)
        assert len(panels) > 0

    def test_create_panels_structure(self):
        """Test that panels have required structure."""
        panels = create_panels()

        for panel in panels:
            assert isinstance(panel, dict)
            assert "id" in panel
            assert "gridPos" in panel
            assert "type" in panel
            assert "title" in panel

            # Check gridPos structure
            grid_pos = panel["gridPos"]
            assert "h" in grid_pos
            assert "w" in grid_pos
            assert "x" in grid_pos
            assert "y" in grid_pos

    def test_create_panels_has_expected_panels(self):
        """Test that expected panels are created."""
        panels = create_panels()

        # Check for some expected panels by title
        titles = [panel.get("title", "") for panel in panels]
        assert any("Production" in title and "Consumption" in title for title in titles)
        assert any("Battery SoC" in title for title in titles)
        hero = next(p for p in panels if p.get("id") == 9)
        assert "Exported Today (kWh)" in hero["targets"][0]["rawSql"]
        overrides = hero["fieldConfig"]["overrides"]
        assert any(
            o.get("matcher", {}).get("options") == "Exported Today (kWh)" for o in overrides
        )


class TestCreateDashboard:
    """Tests for create_dashboard function."""

    def test_create_dashboard_structure(self):
        """Test that dashboard has required structure."""
        dashboard = create_dashboard()

        assert isinstance(dashboard, dict)
        assert dashboard["uid"] == "home-monitor"
        assert dashboard["title"] == "Home Monitor"
        assert "panels" in dashboard
        assert isinstance(dashboard["panels"], list)
        assert len(dashboard["panels"]) > 0

    def test_create_dashboard_has_templating(self):
        """Test that dashboard has templating variables."""
        dashboard = create_dashboard()

        assert "templating" in dashboard
        assert "list" in dashboard["templating"]
        templating_vars = dashboard["templating"]["list"]

        # Should have location and source variables
        var_names = [var["name"] for var in templating_vars]
        assert "location" in var_names
        assert "source" in var_names
        loc_var = next(v for v in templating_vars if v["name"] == "location")
        assert loc_var["datasource"] == "Home Monitor PostgreSQL"

    def test_create_dashboard_has_time_options(self):
        """Test that dashboard has time range options."""
        dashboard = create_dashboard()

        assert "time" in dashboard
        assert "timepicker" in dashboard
        assert "time_options" in dashboard["timepicker"]
        assert isinstance(dashboard["timepicker"]["time_options"], list)
        assert len(dashboard["timepicker"]["time_options"]) > 0

    def test_create_dashboard_has_annotations(self):
        """Test that dashboard has annotations configured."""
        dashboard = create_dashboard()

        assert "annotations" in dashboard
        assert "list" in dashboard["annotations"]
        annotations = dashboard["annotations"]["list"]

        annotation_names = [ann.get("name", "") for ann in annotations]
        assert any("Annotations & Alerts" in name for name in annotation_names)
        assert any(name == "Annotations" for name in annotation_names)
        assert any("Sprinkler Runs" in name for name in annotation_names)
        anno = next(a for a in annotations if a.get("name") == "Annotations")
        assert isinstance(anno.get("rawQuery"), str)
        assert anno["rawQuery"] == anno.get("query")
        assert anno["target"]["rawSql"] == anno["rawQuery"]
        builtin = next(
            a for a in annotations if a.get("name") == "Annotations & Alerts"
        )
        assert builtin.get("hide") is True

    def test_build_sql_codified_annotations_empty(self):
        """Empty codified list yields a no-op query."""
        sql = build_sql_codified_annotations(
            [], all_day_timezone=DEFAULT_ANNOTATIONS_ALL_DAY_TZ
        )
        assert 'WHERE false' in sql

    def test_build_sql_codified_skips_location_when_no_sites(self):
        """No $location filter unless an entry uses locations (avoids anno query breakage)."""
        sql = build_sql_codified_annotations(
            [
                {
                    "all_day": True,
                    "date": "2026-03-01",
                    "title": "t",
                    "text": "",
                    "tags": "",
                },
            ],
            all_day_timezone=DEFAULT_ANNOTATIONS_ALL_DAY_TZ,
        )
        assert "$location" not in sql

    def test_build_sql_codified_includes_fl_and_location_filter(self):
        """Entries with locations are scoped via Site variable."""
        sql = build_sql_codified_annotations(
            [
                {
                    "all_day": True,
                    "date": "2026-03-01",
                    "title": "t",
                    "text": "",
                    "tags": "",
                    "locations": ["FL"],
                },
            ],
            all_day_timezone=DEFAULT_ANNOTATIONS_ALL_DAY_TZ,
        )
        assert "$location" in sql
        assert "'FL'" in sql
        assert " AS timeend" in sql
        assert '"timeEnd"' not in sql

    def test_sprinkler_annotations_overlap_visible_range(self):
        """Runs that overlap the dashboard window are included (not only fully inside it)."""
        assert "sr.start_time <= $__timeTo()" in SQL_SPRINKLER_RUNS_ANNOTATIONS
        assert "sr.end_time >= $__timeFrom()" in SQL_SPRINKLER_RUNS_ANNOTATIONS

    def test_sprinkler_annotations_use_timeend_column(self):
        """Grafana SQL annotations expect a lowercase timeend field for regions."""
        assert " AS timeend" in SQL_SPRINKLER_RUNS_ANNOTATIONS
        assert "timeEnd" not in SQL_SPRINKLER_RUNS_ANNOTATIONS

    def test_build_sql_codified_annotations_escapes_and_regions(self):
        """Titles and optional time_end appear in generated SQL."""
        sql = build_sql_codified_annotations(
            [
                {
                    "time": "2025-01-01T12:00:00+00:00",
                    "time_end": "2025-01-01T14:00:00+00:00",
                    "title": "O'Brien",
                    "text": "Note",
                    "tags": "a,b",
                    "locations": ["Home"],
                },
            ],
            all_day_timezone=DEFAULT_ANNOTATIONS_ALL_DAY_TZ,
        )
        assert "O''Brien" in sql
        assert " AS timeend" in sql
        assert "Home" in sql

    def test_annotation_time_sql_exprs_all_day_range(self):
        """All-day range uses local TZ and end-exclusive midnight."""
        ts_sql, end_sql = _annotation_time_sql_exprs(
            {
                "all_day": True,
                "date_start": "2026-03-10",
                "date_end": "2026-03-11",
                "title": "x",
            },
            DEFAULT_ANNOTATIONS_ALL_DAY_TZ,
        )
        assert "AT TIME ZONE" in ts_sql
        assert "America/New_York" in ts_sql
        assert "2026-03-10" in ts_sql
        assert "2026-03-12" in end_sql
