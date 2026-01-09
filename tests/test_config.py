"""Unit tests for the config module."""

import os
from unittest.mock import patch

import pytest

from home_monitor.config import (
    _get_location_credential,
    get_enphase_fetch_interval_cycles,
    get_flume_oauth_credentials,
    get_flume_username,
    get_iaqualink_credentials,
    get_openweather_api_key,
    get_rachio_credentials,
    get_tankutility_credentials,
    get_tempest_credentials,
    get_tesla_credentials,
)


class TestGetLocationCredential:
    """Tests for _get_location_credential helper function."""

    @patch.dict(os.environ, {"TEST_KEY": "global_value"})
    def test_get_global_credential(self):
        """Test getting global credential when location is None."""
        result = _get_location_credential("TEST_KEY")
        assert result == "global_value"

    @patch.dict(os.environ, {"TEST_KEY": "global_value"})
    def test_get_global_credential_with_location(self):
        """Test getting global credential when location-specific doesn't exist."""
        result = _get_location_credential("TEST_KEY", "NY")
        assert result == "global_value"

    @patch.dict(os.environ, {"TEST_KEY_NY": "ny_value", "TEST_KEY": "global_value"})
    def test_get_location_specific_credential(self):
        """Test getting location-specific credential."""
        result = _get_location_credential("TEST_KEY", "NY")
        assert result == "ny_value"

    @patch.dict(os.environ, {"TEST_KEY_FL_SOUTH": "fl_value"})
    def test_get_location_specific_with_dash(self):
        """Test location name with dash is converted to underscore."""
        result = _get_location_credential("TEST_KEY", "FL-South")
        assert result == "fl_value"

    @patch.dict(os.environ, {})
    def test_get_nonexistent_credential(self):
        """Test getting credential that doesn't exist."""
        result = _get_location_credential("NONEXISTENT_KEY")
        assert result is None


class TestGetTeslaCredentials:
    """Tests for get_tesla_credentials function."""

    @patch.dict(os.environ, {"TESLEMETRY_API_KEY": "test_key"})
    def test_get_tesla_credentials_success(self):
        """Test getting Tesla credentials when API key is set."""
        api_key, site_id = get_tesla_credentials()
        assert api_key == "test_key"
        assert site_id is None

    @patch.dict(os.environ, {}, clear=True)
    def test_get_tesla_credentials_missing(self):
        """Test getting Tesla credentials when API key is not set."""
        api_key, site_id = get_tesla_credentials()
        assert api_key is None
        assert site_id is None


class TestGetOpenWeatherApiKey:
    """Tests for get_openweather_api_key function."""

    @patch.dict(os.environ, {"OPENWEATHER_API_KEY": "global_key"})
    def test_get_global_key(self):
        """Test getting global OpenWeather API key."""
        result = get_openweather_api_key()
        assert result == "global_key"

    @patch.dict(os.environ, {"OPENWEATHER_API_KEY_NY": "ny_key"})
    def test_get_location_specific_key(self):
        """Test getting location-specific OpenWeather API key."""
        result = get_openweather_api_key("NY")
        assert result == "ny_key"

    @patch.dict(os.environ, {}, clear=True)
    def test_get_missing_key(self):
        """Test getting OpenWeather API key when not set."""
        result = get_openweather_api_key()
        assert result is None


class TestGetTempestCredentials:
    """Tests for get_tempest_credentials function."""

    @patch.dict(os.environ, {"TEMPEST_TOKEN": "global_token"})
    def test_get_global_token(self):
        """Test getting global Tempest token."""
        result = get_tempest_credentials()
        assert result == "global_token"

    @patch.dict(os.environ, {"TEMPEST_TOKEN_FL": "fl_token"})
    def test_get_location_specific_token(self):
        """Test getting location-specific Tempest token."""
        result = get_tempest_credentials("FL")
        assert result == "fl_token"

    @patch.dict(os.environ, {}, clear=True)
    def test_get_missing_token(self):
        """Test getting Tempest token when not set."""
        result = get_tempest_credentials()
        assert result is None


class TestGetFlumeOAuthCredentials:
    """Tests for get_flume_oauth_credentials function."""

    @patch.dict(os.environ, {"FLUME_CLIENT_ID": "client_id", "FLUME_CLIENT_SECRET": "secret"})
    def test_get_flume_oauth_credentials_success(self):
        """Test getting Flume OAuth credentials when both are set."""
        client_id, client_secret = get_flume_oauth_credentials()
        assert client_id == "client_id"
        assert client_secret == "secret"

    @patch.dict(os.environ, {}, clear=True)
    def test_get_flume_oauth_credentials_missing(self):
        """Test getting Flume OAuth credentials when not set."""
        client_id, client_secret = get_flume_oauth_credentials()
        assert client_id is None
        assert client_secret is None


class TestGetFlumeUsername:
    """Tests for get_flume_username function."""

    @patch.dict(os.environ, {"FLUME_USERNAME": "testuser"})
    def test_get_flume_username_success(self):
        """Test getting Flume username when set."""
        result = get_flume_username()
        assert result == "testuser"

    @patch.dict(os.environ, {}, clear=True)
    def test_get_flume_username_missing(self):
        """Test getting Flume username when not set."""
        result = get_flume_username()
        assert result is None


class TestGetRachioCredentials:
    """Tests for get_rachio_credentials function."""

    @patch.dict(os.environ, {"RACHIO_API_KEY": "test_key"})
    def test_get_rachio_credentials_success(self):
        """Test getting Rachio API key when set."""
        result = get_rachio_credentials()
        assert result == "test_key"

    @patch.dict(os.environ, {}, clear=True)
    def test_get_rachio_credentials_missing(self):
        """Test getting Rachio API key when not set."""
        result = get_rachio_credentials()
        assert result is None


class TestGetTankUtilityCredentials:
    """Tests for get_tankutility_credentials function."""

    @patch.dict(
        os.environ, {"TANK_UTILITY_EMAIL": "test@example.com", "TANK_UTILITY_PASSWORD": "pass"}
    )
    def test_get_tankutility_credentials_success(self):
        """Test getting Tank Utility credentials when both are set."""
        email, password = get_tankutility_credentials()
        assert email == "test@example.com"
        assert password == "pass"

    @patch.dict(os.environ, {}, clear=True)
    def test_get_tankutility_credentials_missing(self):
        """Test getting Tank Utility credentials when not set."""
        email, password = get_tankutility_credentials()
        assert email is None
        assert password is None


class TestGetIAqualinkCredentials:
    """Tests for get_iaqualink_credentials function."""

    @patch.dict(os.environ, {"IAQUALINK_EMAIL": "test@example.com", "IAQUALINK_PASSWORD": "pass"})
    def test_get_iaqualink_credentials_success(self):
        """Test getting iAqualink credentials when both are set."""
        email, password = get_iaqualink_credentials()
        assert email == "test@example.com"
        assert password == "pass"

    @patch.dict(os.environ, {}, clear=True)
    def test_get_iaqualink_credentials_missing(self):
        """Test getting iAqualink credentials when not set."""
        email, password = get_iaqualink_credentials()
        assert email is None
        assert password is None


class TestGetEnphaseFetchIntervalCycles:
    """Tests for get_enphase_fetch_interval_cycles function."""

    @patch.dict(os.environ, {"ENPHASE_FETCH_INTERVAL_CYCLES": "5"})
    def test_get_custom_interval(self):
        """Test getting custom fetch interval from environment."""
        result = get_enphase_fetch_interval_cycles()
        assert result == 5

    @patch.dict(os.environ, {}, clear=True)
    def test_get_default_interval(self):
        """Test getting default fetch interval when not set."""
        result = get_enphase_fetch_interval_cycles()
        assert result == 3

    @patch.dict(os.environ, {"ENPHASE_FETCH_INTERVAL_CYCLES": "invalid"})
    def test_get_interval_with_invalid_value(self):
        """Test getting default interval when value is invalid."""
        result = get_enphase_fetch_interval_cycles()
        assert result == 3
