"""Unit tests for the Tank Utility fetcher's insert dedupe."""

import os
from datetime import datetime
from unittest.mock import MagicMock, patch

from home_monitor.fetcher import fetch_tankutility_data

DEVICE_ID = "abc123def456"
EMAIL = "user@example.com"
PASSWORD = "secret"

DEVICE_DATA = {
    "device": {
        "name": "Home Tank",
        "capacity": 500,
        "fuel_type": "propane",
        "battery_level": "good",
        "battery_warn": False,
        "battery_crit": False,
        "lastReading": {
            "tank": 62.5,
            "temperature": 41.0,
            "time": 1767414205000,
            "time_iso": "2026-01-03T04:23:25.000Z",
        },
    }
}


def _response(status=200, json_data=None):
    resp = MagicMock()
    resp.status_code = status
    resp.text = ""
    resp.json.return_value = json_data
    return resp


def _api_responses(*args, **kwargs):
    """Return the token response for getToken and device data for everything else."""
    url = args[0] if args else kwargs.get("url", "")
    if url.endswith("/getToken"):
        return _response(json_data={"token": "tok"})
    return _response(json_data=DEVICE_DATA)


@patch.dict(os.environ, {"TANK_UTILITY_EMAIL": EMAIL, "TANK_UTILITY_PASSWORD": PASSWORD})
class TestFetchTankUtilityData:
    """Tests for fetch_tankutility_data."""

    SITE = {"timezone": "America/New_York", "tankutility": {"device_id": DEVICE_ID}}

    @patch("home_monitor.fetcher.insert_propane_reading")
    @patch("home_monitor.fetcher.get_propane_reading_exists", return_value=False)
    @patch("home_monitor.apis.tankutility.requests.get", side_effect=_api_responses)
    def test_inserts_new_reading(self, _get, mock_exists, mock_insert):
        fetch_tankutility_data("NY", self.SITE, location_id=3)

        # Device reading time (UTC) converted to naive site-local time
        expected_ts = datetime(2026, 1, 2, 23, 23, 25)
        mock_exists.assert_called_once_with(3, DEVICE_ID, expected_ts)
        mock_insert.assert_called_once()
        kwargs = mock_insert.call_args[1]
        assert kwargs["location_id"] == 3
        assert kwargs["device_id"] == DEVICE_ID
        assert kwargs["timestamp"] == expected_ts
        assert kwargs["source"] == "tankutility"
        assert kwargs["tank_level_percent"] == 62.5
        assert kwargs["tank_level_gallons"] == 312.5

    @patch("home_monitor.fetcher.insert_propane_reading")
    @patch("home_monitor.fetcher.get_propane_reading_exists", return_value=True)
    @patch("home_monitor.apis.tankutility.requests.get", side_effect=_api_responses)
    def test_skips_reading_already_stored(self, mock_get, mock_exists, mock_insert):
        fetch_tankutility_data("NY", self.SITE, location_id=3)

        mock_get.assert_called()
        mock_exists.assert_called_once()
        mock_insert.assert_not_called()

    @patch("home_monitor.fetcher.insert_propane_reading")
    @patch("home_monitor.fetcher.get_propane_reading_exists")
    @patch("home_monitor.apis.tankutility.requests.get")
    def test_no_tank_level_skips_lookup_and_insert(self, mock_get, mock_exists, mock_insert):
        no_level = {
            "device": {"capacity": 500, "lastReading": {"time_iso": "2026-01-03T04:23:25Z"}}
        }
        mock_get.side_effect = lambda url, **kw: (
            _response(json_data={"token": "tok"})
            if url.endswith("/getToken")
            else _response(json_data=no_level)
        )

        fetch_tankutility_data("NY", self.SITE, location_id=3)

        mock_exists.assert_not_called()
        mock_insert.assert_not_called()

    @patch("home_monitor.fetcher.insert_propane_reading")
    @patch("home_monitor.fetcher.get_propane_reading_exists", side_effect=RuntimeError("db down"))
    @patch("home_monitor.apis.tankutility.requests.get", side_effect=_api_responses)
    def test_lookup_error_is_logged_not_raised(self, _get, _exists, mock_insert):
        fetch_tankutility_data("NY", self.SITE, location_id=3)

        mock_insert.assert_not_called()

    @patch("home_monitor.fetcher.insert_propane_reading")
    @patch("home_monitor.apis.tankutility.requests.get")
    def test_missing_credentials(self, mock_get, mock_insert):
        with patch.dict(os.environ, {"TANK_UTILITY_EMAIL": "", "TANK_UTILITY_PASSWORD": ""}):
            fetch_tankutility_data("NY", self.SITE, location_id=3)

        mock_get.assert_not_called()
        mock_insert.assert_not_called()
