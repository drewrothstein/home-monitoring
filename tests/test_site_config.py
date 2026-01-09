"""Unit tests for the site_config module."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from home_monitor.site_config import (
    get_site,
    get_sites,
    get_sites_config_path,
    load_sites_config,
    validate_site_config,
)


class TestGetSitesConfigPath:
    """Tests for get_sites_config_path function."""

    @patch("home_monitor.site_config.os.getenv")
    def test_get_path_from_env_var(self, mock_getenv):
        """Test getting path from SITES_CONFIG_PATH environment variable."""
        test_path = Path("/custom/path/sites.json")
        mock_getenv.return_value = str(test_path)

        with patch.object(Path, "exists", return_value=True):
            result = get_sites_config_path()
            assert result == test_path

    @patch("home_monitor.site_config.os.getenv")
    def test_get_path_from_env_var_not_found(self, mock_getenv):
        """Test error when SITES_CONFIG_PATH points to non-existent file."""
        test_path = Path("/nonexistent/sites.json")
        mock_getenv.return_value = str(test_path)

        with patch.object(Path, "exists", return_value=False):
            with pytest.raises(FileNotFoundError):
                get_sites_config_path()

    @patch("home_monitor.site_config.os.getenv", return_value=None)
    @patch("home_monitor.site_config.Path.cwd")
    def test_get_path_from_cwd(self, mock_cwd, mock_getenv):
        """Test getting path from current working directory."""
        test_path = Path("/cwd/sites.json")
        mock_cwd.return_value = Path("/cwd")

        with patch.object(Path, "exists", return_value=True):
            result = get_sites_config_path()
            assert result == test_path


class TestLoadSitesConfig:
    """Tests for load_sites_config function."""

    def test_load_valid_config(self):
        """Test loading a valid sites configuration."""
        config_data = {
            "sites": {
                "NY": {
                    "capacity_kw": 10.0,
                    "openweather": {"latitude": 40.7, "longitude": -74.0},
                }
            }
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(config_data, f)
            temp_path = Path(f.name)

        try:
            with patch("home_monitor.site_config.get_sites_config_path", return_value=temp_path):
                result = load_sites_config()
                assert result == config_data
                assert "sites" in result
                assert "NY" in result["sites"]
        finally:
            temp_path.unlink()

    def test_load_config_missing_sites_key(self):
        """Test error when config is missing 'sites' key."""
        config_data = {"invalid": "config"}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(config_data, f)
            temp_path = Path(f.name)

        try:
            with patch("home_monitor.site_config.get_sites_config_path", return_value=temp_path):
                with pytest.raises(ValueError, match="must have a 'sites' key"):
                    load_sites_config()
        finally:
            temp_path.unlink()

    def test_load_config_sites_not_dict(self):
        """Test error when 'sites' is not a dictionary."""
        config_data = {"sites": "not a dict"}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(config_data, f)
            temp_path = Path(f.name)

        try:
            with patch("home_monitor.site_config.get_sites_config_path", return_value=temp_path):
                with pytest.raises(ValueError, match="'sites' must be a dictionary"):
                    load_sites_config()
        finally:
            temp_path.unlink()


class TestValidateSiteConfig:
    """Tests for validate_site_config function."""

    def test_validate_valid_config(self):
        """Test validating a valid site configuration."""
        site_config = {
            "capacity_kw": 10.0,
            "openweather": {"latitude": 40.7, "longitude": -74.0},
        }
        # Should not raise
        validate_site_config("NY", site_config)

    def test_validate_config_not_dict(self):
        """Test error when site config is not a dictionary."""
        with pytest.raises(ValueError, match="must be a dictionary"):
            validate_site_config("NY", "not a dict")

    def test_validate_missing_capacity_kw(self):
        """Test error when capacity_kw is missing."""
        site_config = {"openweather": {"latitude": 40.7, "longitude": -74.0}}
        with pytest.raises(ValueError, match="'capacity_kw' is required"):
            validate_site_config("NY", site_config)

    def test_validate_capacity_kw_not_number(self):
        """Test error when capacity_kw is not a number."""
        site_config = {
            "capacity_kw": "10",
            "openweather": {"latitude": 40.7, "longitude": -74.0},
        }
        with pytest.raises(ValueError, match="'capacity_kw' must be a number"):
            validate_site_config("NY", site_config)

    def test_validate_capacity_kw_negative(self):
        """Test error when capacity_kw is negative."""
        site_config = {
            "capacity_kw": -10,
            "openweather": {"latitude": 40.7, "longitude": -74.0},
        }
        with pytest.raises(ValueError, match="'capacity_kw' must be non-negative"):
            validate_site_config("NY", site_config)

    def test_validate_timezone_not_string(self):
        """Test error when timezone is not a string."""
        site_config = {
            "capacity_kw": 10.0,
            "timezone": 123,
            "openweather": {"latitude": 40.7, "longitude": -74.0},
        }
        with pytest.raises(ValueError, match="'timezone' must be a string"):
            validate_site_config("NY", site_config)

    def test_validate_missing_coordinates(self):
        """Test error when coordinates are missing."""
        site_config = {"capacity_kw": 10.0}
        with pytest.raises(ValueError, match="location coordinates required"):
            validate_site_config("NY", site_config)

    def test_validate_coordinates_from_openweather(self):
        """Test coordinates can come from openweather config."""
        site_config = {
            "capacity_kw": 10.0,
            "openweather": {"latitude": 40.7, "longitude": -74.0},
        }
        # Should not raise
        validate_site_config("NY", site_config)

    def test_validate_coordinates_from_location_block(self):
        """Test coordinates can come from location block."""
        site_config = {
            "capacity_kw": 10.0,
            "location": {"latitude": 40.7, "longitude": -74.0},
        }
        # Should not raise
        validate_site_config("NY", site_config)

    def test_validate_tempest_missing_station_id(self):
        """Test error when tempest is configured but station_id is missing."""
        site_config = {
            "capacity_kw": 10.0,
            "openweather": {"latitude": 40.7, "longitude": -74.0},
            "tempest": {},
        }
        with pytest.raises(ValueError, match="tempest.station_id.*required"):
            validate_site_config("NY", site_config)

    def test_validate_tempest_valid(self):
        """Test valid tempest configuration."""
        site_config = {
            "capacity_kw": 10.0,
            "openweather": {"latitude": 40.7, "longitude": -74.0},
            "tempest": {"station_id": "12345"},
        }
        # Should not raise
        validate_site_config("NY", site_config)

    def test_validate_openweather_missing_latitude(self):
        """Test error when openweather is configured but latitude is missing."""
        site_config = {
            "capacity_kw": 10.0,
            "openweather": {"longitude": -74.0},
            "location": {"latitude": 40.7, "longitude": -74.0},  # Provide coordinates via location block
        }
        with pytest.raises(ValueError, match="openweather.latitude.*required"):
            validate_site_config("NY", site_config)

    def test_validate_openweather_latitude_not_number(self):
        """Test error when openweather latitude is not a number."""
        site_config = {
            "capacity_kw": 10.0,
            "openweather": {"latitude": "40.7", "longitude": -74.0},
        }
        with pytest.raises(ValueError, match="'openweather.latitude' must be a number"):
            validate_site_config("NY", site_config)

    def test_validate_tesla_site_ids_not_list(self):
        """Test error when tesla.site_ids is not a list."""
        site_config = {
            "capacity_kw": 10.0,
            "openweather": {"latitude": 40.7, "longitude": -74.0},
            "tesla": {"site_ids": "not a list"},
        }
        with pytest.raises(ValueError, match="'tesla.site_ids' must be a list"):
            validate_site_config("NY", site_config)

    def test_validate_tesla_site_ids_empty(self):
        """Test error when tesla.site_ids is empty."""
        site_config = {
            "capacity_kw": 10.0,
            "openweather": {"latitude": 40.7, "longitude": -74.0},
            "tesla": {"site_ids": []},
        }
        with pytest.raises(ValueError, match="'tesla.site_ids' cannot be empty"):
            validate_site_config("NY", site_config)

    def test_validate_tesla_valid(self):
        """Test valid tesla configuration."""
        site_config = {
            "capacity_kw": 10.0,
            "openweather": {"latitude": 40.7, "longitude": -74.0},
            "tesla": {"site_ids": ["12345", "67890"]},
        }
        # Should not raise
        validate_site_config("NY", site_config)


class TestGetSites:
    """Tests for get_sites function."""

    def test_get_sites_success(self):
        """Test getting all sites from configuration."""
        config_data = {
            "sites": {
                "NY": {"capacity_kw": 10.0, "openweather": {"latitude": 40.7, "longitude": -74.0}},
                "FL": {"capacity_kw": 15.0, "openweather": {"latitude": 25.7, "longitude": -80.2}},
            }
        }

        with patch("home_monitor.site_config.load_sites_config", return_value=config_data):
            result = get_sites()
            assert len(result) == 2
            assert "NY" in result
            assert "FL" in result


class TestGetSite:
    """Tests for get_site function."""

    def test_get_site_success(self):
        """Test getting a specific site from configuration."""
        sites = {
            "NY": {"capacity_kw": 10.0, "openweather": {"latitude": 40.7, "longitude": -74.0}},
            "FL": {"capacity_kw": 15.0, "openweather": {"latitude": 25.7, "longitude": -80.2}},
        }

        with patch("home_monitor.site_config.get_sites", return_value=sites):
            result = get_site("NY")
            assert result == sites["NY"]

    def test_get_site_not_found(self):
        """Test getting a site that doesn't exist."""
        sites = {"NY": {"capacity_kw": 10.0}}

        with patch("home_monitor.site_config.get_sites", return_value=sites):
            result = get_site("FL")
            assert result is None
