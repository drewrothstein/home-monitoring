"""Unit tests for the Centri Connect (MyPropane) integration."""

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from home_monitor.apis.centri import CentriApiClient, CentriAuthError, CentriError
from home_monitor.config import get_centri_credentials
from home_monitor.fetcher import fetch_centri_data
from home_monitor.site_config import validate_site_config

USER_ID = "12345678-9012-3456-7a89-b012345cde6f"
DEVICE_ID = "123a4b5c-678d-9e0f-a123-4b567c8d901e"
DEVICE_AUTH = "123456"

DEVICE_DATA = {
    "AlertStatus": "No Alert",
    "Altitude": 123.456,
    "BatteryVolts": 3.95,
    "DeviceID": DEVICE_ID,
    "DeviceName": "My Tank",
    "DeviceTempCelsius": 17.0,
    "DeviceTempFahrenheit": 63.0,
    "LastPostTimeIso": "2026-02-27 22:00:31.000",
    "Latitude": 40.7128,
    "Longitude": -74.0060,
    "NextPostTimeIso": "2026-02-28 10:00:00.000",
    "SignalQualLTE": -107.0,
    "SolarVolts": 2.46,
    "TankLevel": 75.0,
    "TankSize": 1000,
    "TankSizeUnit": "Gallons",
    "VersionHW": "4.1",
    "VersionLTE": "1.1.2",
}


def _response(status=200, json_data=None, text=""):
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    if isinstance(json_data, Exception):
        resp.json.side_effect = json_data
    else:
        resp.json.return_value = json_data
    return resp


def _client(tz=None):
    return CentriApiClient(USER_ID, DEVICE_ID, DEVICE_AUTH, tz=tz)


class TestCentriApiClient:
    """Tests for CentriApiClient."""

    @patch("home_monitor.apis.centri.requests.get")
    def test_fetch_current_data_normalizes(self, mock_get):
        mock_get.return_value = _response(json_data={DEVICE_ID: dict(DEVICE_DATA)})

        data = _client(tz="America/New_York").fetch_current_data()

        url = mock_get.call_args[0][0]
        assert url == (
            f"https://api.centriconnect.com/centriconnect/{USER_ID}/device/{DEVICE_ID}/all-data"
        )
        assert mock_get.call_args[1]["params"] == {"device_auth": DEVICE_AUTH}

        # 22:00:31 UTC -> 17:00:31 EST, stored naive
        assert data["timestamp"] == datetime(2026, 2, 27, 17, 0, 31)
        assert data["next_post_time"] == datetime(2026, 2, 28, 10, 0, tzinfo=timezone.utc)
        assert data["tank_level_percent"] == 75.0
        assert data["capacity_gallons"] == 1000.0
        assert data["tank_level_gallons"] == 750.0
        assert data["temperature_f"] == 63.0
        assert data["battery_percent"] == pytest.approx(90.0)
        assert data["battery_status"] == "good"
        assert data["battery_warn"] is False
        assert data["battery_crit"] is False
        assert data["fuel_type"] == "propane"
        assert data["raw_data"]["DeviceName"] == "My Tank"

    @patch("home_monitor.apis.centri.requests.get")
    def test_liters_converted_to_gallons(self, mock_get):
        raw = dict(DEVICE_DATA, TankSize=1000, TankSizeUnit="Liters", TankLevel=50.0)
        mock_get.return_value = _response(json_data={DEVICE_ID: raw})

        data = _client().fetch_current_data()

        assert data["capacity_gallons"] == pytest.approx(264.172, rel=1e-4)
        assert data["tank_level_gallons"] == pytest.approx(132.086, rel=1e-4)
        # No tz -> naive UTC
        assert data["timestamp"] == datetime(2026, 2, 27, 22, 0, 31)

    @pytest.mark.parametrize(
        "volts,status", [(3.58, "low"), (3.52, "critical"), (3.0, "critical"), (None, "unknown")]
    )
    @patch("home_monitor.apis.centri.requests.get")
    def test_battery_status(self, mock_get, volts, status):
        raw = dict(DEVICE_DATA, BatteryVolts=volts)
        mock_get.return_value = _response(json_data={DEVICE_ID: raw})

        assert _client().fetch_current_data()["battery_status"] == status

    @pytest.mark.parametrize("status", [401, 403, 404])
    @patch("home_monitor.apis.centri.requests.get")
    def test_auth_errors(self, mock_get, status):
        mock_get.return_value = _response(status=status)
        with pytest.raises(CentriAuthError):
            _client().get_device_data()

    @patch("home_monitor.apis.centri.requests.get")
    def test_not_found_body(self, mock_get):
        mock_get.return_value = _response(
            json_data={"error": "NotFound", "message": "Device does not exist."}
        )
        with pytest.raises(CentriAuthError, match="Device does not exist"):
            _client().get_device_data()

    @pytest.mark.parametrize(
        "resp",
        [
            _response(status=429),
            _response(status=500, text="boom"),
            _response(json_data=ValueError("bad json")),
            _response(json_data=None),
            _response(json_data={"other-device": {}}),
        ],
    )
    @patch("home_monitor.apis.centri.requests.get")
    def test_other_errors(self, mock_get, resp):
        mock_get.return_value = resp
        with pytest.raises(CentriError):
            _client().get_device_data()

    def test_parse_timestamp(self):
        assert CentriApiClient.parse_timestamp(None) is None
        assert CentriApiClient.parse_timestamp("garbage") is None
        assert CentriApiClient.parse_timestamp("2026-02-27T22:00:31Z") == datetime(
            2026, 2, 27, 22, 0, 31, tzinfo=timezone.utc
        )


class TestGetCentriCredentials:
    """Tests for get_centri_credentials."""

    @patch.dict(os.environ, {"CENTRI_USER_ID": "u", "CENTRI_DEVICE_AUTH": "a"}, clear=True)
    def test_global(self):
        assert get_centri_credentials("NY") == ("u", "a")

    @patch.dict(
        os.environ,
        {"CENTRI_USER_ID": "u", "CENTRI_DEVICE_AUTH": "a", "CENTRI_DEVICE_AUTH_NEW_YORK": "ny"},
        clear=True,
    )
    def test_location_override(self):
        assert get_centri_credentials("New York") == ("u", "ny")

    @patch.dict(os.environ, {}, clear=True)
    def test_missing(self):
        assert get_centri_credentials() == (None, None)


class TestCentriSiteConfig:
    """Tests for centri sites.json validation."""

    BASE = {"capacity_kw": 10, "location": {"latitude": 1.0, "longitude": 2.0}}

    def test_valid(self):
        validate_site_config("NY", {**self.BASE, "centri": {"device_id": DEVICE_ID}})

    def test_not_dict(self):
        with pytest.raises(ValueError, match="'centri' must be a dictionary"):
            validate_site_config("NY", {**self.BASE, "centri": DEVICE_ID})

    def test_device_id_not_string(self):
        with pytest.raises(ValueError, match="centri.device_id"):
            validate_site_config("NY", {**self.BASE, "centri": {"device_id": 123}})


@patch.dict(os.environ, {"CENTRI_USER_ID": USER_ID, "CENTRI_DEVICE_AUTH": DEVICE_AUTH})
class TestFetchCentriData:
    """Tests for fetch_centri_data."""

    SITE = {"timezone": "America/New_York", "centri": {"device_id": DEVICE_ID}}

    @patch("home_monitor.fetcher.insert_propane_reading")
    @patch("home_monitor.fetcher.get_propane_reading_exists", return_value=False)
    @patch("home_monitor.fetcher.get_latest_propane_reading", return_value=None)
    @patch("home_monitor.apis.centri.requests.get")
    def test_inserts_new_reading(self, mock_get, _latest, _exists, mock_insert):
        mock_get.return_value = _response(json_data={DEVICE_ID: dict(DEVICE_DATA)})

        fetch_centri_data("NY", self.SITE, location_id=7)

        mock_insert.assert_called_once()
        kwargs = mock_insert.call_args[1]
        assert kwargs["location_id"] == 7
        assert kwargs["device_id"] == DEVICE_ID
        assert kwargs["source"] == "centri"
        assert kwargs["tank_level_gallons"] == 750.0
        assert kwargs["raw_data"]["TankLevel"] == 75.0

    @patch("home_monitor.fetcher.insert_propane_reading")
    @patch("home_monitor.fetcher.get_latest_propane_reading")
    @patch("home_monitor.apis.centri.requests.get")
    def test_skips_before_next_post(self, mock_get, mock_latest, mock_insert):
        next_post = datetime.now(timezone.utc) + timedelta(hours=3)
        mock_latest.return_value = {
            "raw_data": {"NextPostTimeIso": next_post.strftime("%Y-%m-%d %H:%M:%S.000")}
        }

        fetch_centri_data("NY", self.SITE, location_id=7)

        mock_get.assert_not_called()
        mock_insert.assert_not_called()

    @patch("home_monitor.fetcher.insert_propane_reading")
    @patch("home_monitor.fetcher.get_propane_reading_exists", return_value=True)
    @patch("home_monitor.fetcher.get_latest_propane_reading")
    @patch("home_monitor.apis.centri.requests.get")
    def test_fetches_after_next_post_but_dedupes(self, mock_get, mock_latest, _exists, mock_insert):
        mock_latest.return_value = {"raw_data": {"NextPostTimeIso": "2020-01-01 00:00:00.000"}}
        mock_get.return_value = _response(json_data={DEVICE_ID: dict(DEVICE_DATA)})

        fetch_centri_data("NY", self.SITE, location_id=7)

        mock_get.assert_called_once()
        mock_insert.assert_not_called()

    @patch("home_monitor.fetcher.insert_propane_reading")
    @patch("home_monitor.apis.centri.requests.get")
    def test_missing_credentials(self, mock_get, mock_insert):
        with patch.dict(os.environ, {"CENTRI_USER_ID": "", "CENTRI_DEVICE_AUTH": ""}):
            fetch_centri_data("NY", self.SITE, location_id=7)
        mock_get.assert_not_called()
        mock_insert.assert_not_called()

    @patch("home_monitor.fetcher.insert_propane_reading")
    @patch("home_monitor.fetcher.get_latest_propane_reading", return_value=None)
    @patch("home_monitor.apis.centri.requests.get")
    def test_api_error_is_logged_not_raised(self, mock_get, _latest, mock_insert):
        mock_get.return_value = _response(status=500, text="boom")

        fetch_centri_data("NY", self.SITE, location_id=7)

        mock_insert.assert_not_called()
