"""Unit tests for dashboard generation."""

import pytest
from grafanalib.core import GridPos, SqlTarget

from scripts.generate_dashboard import (
    color_override,
    create_dashboard,
    create_panels,
    sql_battery_soc,
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

        # Should have built-in annotations and sprinkler runs
        annotation_names = [ann.get("name", "") for ann in annotations]
        assert any("Annotations & Alerts" in name for name in annotation_names)
        assert any("Sprinkler Runs" in name for name in annotation_names)
