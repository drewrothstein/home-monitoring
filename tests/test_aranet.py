"""Unit tests for the Aranet Cloud integration."""

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from home_monitor.apis import aranet
from home_monitor.apis.aranet import (
    AranetApiClient,
    AranetAuthError,
    AranetError,
    metric_key_for,
    normalize_value,
    parse_timestamp,
)
from home_monitor.config import get_aranet_api_key, get_aranet_backfill_max_days
from home_monitor.fetcher import fetch_aranet_data
from home_monitor.site_config import validate_site_config

BASE_NY = "358151000001"
BASE_FL = "358151000002"

# Shapes mirror real Aranet Cloud responses (ids are made up)
SENSORS = {
    "sensors": [
        {
            "id": "4200001",
            "sensorId": "0A1B2",
            "name": "NY - Living Room",
            "type": "S4V1",
            "skills": [{"metric": m, "active": True} for m in ("1", "2", "3", "4", "61", "62")],
            "bases": [BASE_NY],
            "pairing": [{"base": BASE_NY, "paired": "2022-08-18T23:16:19Z"}],
        },
        {
            "id": "4200002",
            "sensorId": "0C3D4",
            "name": "FL - Bedroom",
            "type": "S4V1",
            "skills": [{"metric": "3", "active": True}],
            "bases": [BASE_FL],
        },
    ]
}

METRICS = {
    "metrics": [
        {
            "id": "1",
            "name": "Temperature",
            "units": [{"id": "1", "name": "°C"}, {"id": "101", "name": "°F"}],
        },
        {"id": "2", "name": "Humidity", "units": [{"id": "2", "name": "%"}]},
        {"id": "3", "name": "CO₂", "units": [{"id": "3", "name": "ppm"}]},
        {
            "id": "4",
            "name": "Atmospheric Pressure",
            "units": [{"id": "104", "name": "hPa"}, {"id": "4", "name": "Pa"}],
        },
        {"id": "61", "name": "RSSI", "units": [{"id": "11", "name": "dBm"}]},
        {"id": "62", "name": "Battery voltage", "units": [{"id": "132", "name": "%"}]},
        {"id": "90", "name": "Radon concentration", "units": [{"id": "200", "name": "pCi/L"}]},
    ]
}

SENSOR_TYPES = {
    "sensorTypes": [{"id": "S4V1", "name": "Aranet4"}, {"id": "S4V6", "name": "Radon sensor"}]
}

T1 = "2026-11-09T14:54:21Z"
T0 = "2026-11-09T14:44:21Z"

LAST = {
    "readings": [
        {
            "metric": "1",
            "sensor": "4200001",
            "unit": "101",
            "time": T1,
            "value": 66.7,
            "novelty": "new",
        },
        {
            "metric": "3",
            "sensor": "4200001",
            "unit": "3",
            "time": T1,
            "value": 551,
            "novelty": "new",
        },
        {
            "metric": "4",
            "sensor": "4200001",
            "unit": "104",
            "time": T1,
            "value": 1001.1,
            "novelty": "new",
        },
        {
            "metric": "3",
            "sensor": "4200002",
            "unit": "3",
            "time": T1,
            "value": 700,
            "novelty": "new",
        },
    ]
}

TELEMETRY = {
    "readings": [
        {
            "metric": "62",
            "sensor": "4200001",
            "unit": "132",
            "time": T1,
            "value": 25,
            "novelty": "new",
        },
        {
            "metric": "61",
            "sensor": "4200001",
            "unit": "11",
            "time": T1,
            "value": -66,
            "novelty": "new",
        },
    ]
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


def _router(history_pages=None, telemetry_history=None):
    """Fake AranetApiClient._get keyed by path."""
    history_pages = history_pages or [{"readings": []}]
    calls = []

    def fake_get(path, params=None):
        calls.append((path, params))
        if path == "/api/v1/sensors":
            return SENSORS
        if path == "/api/v1/metrics":
            return METRICS
        if path == "/api/v1/sensors/types":
            return SENSOR_TYPES
        if path == "/api/v1/measurements/last":
            return LAST
        if path == "/api/v1/telemetry/last":
            return TELEMETRY
        if path.startswith("/api/v1/measurements/history"):
            if "next=" in path:
                return history_pages[int(path.rsplit("=", 1)[1])]
            return history_pages[0]
        if path.startswith("/api/v1/telemetry/history"):
            return telemetry_history or {"readings": []}
        raise AssertionError(f"unexpected path {path}")

    return fake_get, calls


@pytest.fixture(autouse=True)
def _clear_cache():
    aranet.clear_catalog_cache()
    yield
    aranet.clear_catalog_cache()


class TestNormalization:
    @pytest.mark.parametrize(
        "key,value,unit,expected",
        [
            ("temperature", 66.7, "°F", (66.7, "°F")),
            ("temperature", 20.0, "°C", (68.0, "°F")),
            ("temperature", 20.0, "Cel", (68.0, "°F")),
            ("temperature", 293.15, "K", (68.0, "°F")),
            ("pressure", 100110.0, "Pa", (1001.1, "hPa")),
            ("pressure", 1001.1, "hPa", (1001.1, "hPa")),
            ("radon", 4.0, "pCi/L", (148.0, "Bq/m³")),
            ("radon", 50.0, "Bq/m³", (50.0, "Bq/m³")),
            ("rssi", -96.0, "dBW", (-66.0, "dBm")),
            ("co2", 551.0, "ppm", (551.0, "ppm")),
            ("humidity", 42.0, "%RH", (42.0, "%")),
            ("vapour_pressure_deficit", 1.2, "kPa", (1.2, "kPa")),
        ],
    )
    def test_normalize_value(self, key, value, unit, expected):
        result, result_unit = normalize_value(key, value, unit)
        assert result == pytest.approx(expected[0])
        assert result_unit == expected[1]

    def test_unknown_unit_kept_as_is(self):
        assert normalize_value("co2", 0.05, "/") == (0.05, "/")

    def test_metric_key_for(self):
        assert metric_key_for("3", "CO₂") == "co2"
        assert metric_key_for("90", "Radon concentration") == "radon"
        assert metric_key_for("28", "Vapour-Pressure Deficit") == "vapour_pressure_deficit"
        assert metric_key_for("99", "Atmospheric Pressure") == "pressure"
        assert metric_key_for("29", "Daylight Integral") == "daylight_integral"
        assert metric_key_for("77", None) == "metric_77"

    def test_parse_timestamp(self):
        assert parse_timestamp(T1) == datetime(2026, 11, 9, 14, 54, 21, tzinfo=timezone.utc)
        assert parse_timestamp("bad") is None
        assert parse_timestamp(None) is None

    def test_normalize_reading_skips_incomplete(self):
        metrics = {"3": {"name": "CO₂", "key": "co2", "units": {"3": "ppm"}}}
        assert AranetApiClient.normalize_reading({"metric": "3", "sensor": "1"}, metrics) is None
        reading = AranetApiClient.normalize_reading(LAST["readings"][1], metrics)
        assert reading["metric_key"] == "co2"
        assert reading["value"] == 551.0
        assert reading["unit"] == "ppm"
        assert reading["probe"] == 0


class TestFilterSensors:
    def test_by_base(self):
        selected = AranetApiClient.filter_sensors(SENSORS["sensors"], base_ids=[BASE_NY])
        assert [s["id"] for s in selected] == ["4200001"]

    def test_by_sensor_id_or_device_id(self):
        sensors = SENSORS["sensors"]
        assert len(AranetApiClient.filter_sensors(sensors, sensor_ids=["4200002"])) == 1
        assert len(AranetApiClient.filter_sensors(sensors, sensor_ids=["0a1b2"])) == 1

    def test_no_filter_returns_all(self):
        assert len(AranetApiClient.filter_sensors(SENSORS["sensors"])) == 2


class TestAranetApiClient:
    def test_fetch_site_data_filters_and_normalizes(self):
        client = AranetApiClient("k")
        fake_get, _ = _router()
        with patch.object(client, "_get", side_effect=fake_get):
            data = client.fetch_site_data(base_ids=[BASE_NY])

        assert [s["name"] for s in data["sensors"]] == ["NY - Living Room"]
        assert data["sensors"][0]["sensor_type_name"] == "Aranet4"
        assert data["sensors"][0]["base_id"] == BASE_NY
        by_key = {r["metric_key"]: r for r in data["readings"]}
        assert set(by_key) == {"temperature", "co2", "pressure", "battery", "rssi"}
        assert by_key["temperature"]["unit"] == "°F"
        assert by_key["battery"]["value"] == 25.0
        assert all(r["sensor_id"] == "4200001" for r in data["readings"])

    def test_history_pages_and_dedupes(self):
        page0 = {
            "readings": [
                {"metric": "3", "sensor": "4200001", "unit": "3", "time": T0, "value": 540},
                # Duplicate of the latest reading
                {"metric": "3", "sensor": "4200001", "unit": "3", "time": T1, "value": 551},
            ],
            "next": "/api/v1/measurements/history?from=x&next=1",
        }
        page1 = {
            "readings": [{"metric": "2", "sensor": "4200001", "unit": "2", "time": T0, "value": 41}]
        }
        client = AranetApiClient("k")
        fake_get, calls = _router(history_pages=[page0, page1])
        with patch.object(client, "_get", side_effect=fake_get):
            data = client.fetch_site_data(
                base_ids=[BASE_NY], history_since=datetime.now(timezone.utc) - timedelta(days=30)
            )

        co2 = [r for r in data["readings"] if r["metric_key"] == "co2"]
        assert len(co2) == 2
        assert any(r["metric_key"] == "humidity" for r in data["readings"])
        history_calls = [c for c in calls if c[0] == "/api/v1/measurements/history"]
        # Window is capped to the 7 days the API serves
        start = parse_timestamp(history_calls[0][1]["from"])
        assert datetime.now(timezone.utc) - start <= timedelta(days=7, minutes=1)

    def test_catalogs_are_cached(self):
        client = AranetApiClient("k")
        fake_get, calls = _router()
        with patch.object(client, "_get", side_effect=fake_get):
            client.fetch_site_data(base_ids=[BASE_NY])
            client.fetch_site_data(base_ids=[BASE_FL])
        assert sum(1 for c in calls if c[0] == "/api/v1/metrics") == 1

    def test_sends_api_key_and_user_agent(self):
        client = AranetApiClient("secret")
        assert client.session.headers["ApiKey"] == "secret"
        assert client.session.headers["User-Agent"] == aranet.USER_AGENT

    @pytest.mark.parametrize("status", [401, 403])
    def test_auth_errors(self, status):
        client = AranetApiClient("k")
        with patch.object(client.session, "get", return_value=_response(status, text="denied")):
            with pytest.raises(AranetAuthError, match="ARANET_API_KEY"):
                client.get_sensors()

    def test_cloudflare_block(self):
        client = AranetApiClient("k")
        resp = _response(403, text='{"title":"Error 1010: Access denied","cloudflare_error":true}')
        with patch.object(client.session, "get", return_value=resp):
            with pytest.raises(AranetAuthError, match="Cloudflare"):
                client.get_sensors()

    @pytest.mark.parametrize(
        "resp",
        [
            _response(429),
            _response(500, text="boom"),
            _response(200, json_data=ValueError("bad json")),
            _response(200, json_data=[1, 2]),
        ],
    )
    def test_other_errors(self, resp):
        client = AranetApiClient("k")
        with patch.object(client.session, "get", return_value=resp):
            with pytest.raises(AranetError):
                client.get_sensors()


class TestConfig:
    @patch.dict(os.environ, {"ARANET_API_KEY": "global"}, clear=True)
    def test_global_key(self):
        assert get_aranet_api_key("NY") == "global"

    @patch.dict(os.environ, {"ARANET_API_KEY": "global", "ARANET_API_KEY_FL": "fl"}, clear=True)
    def test_location_key(self):
        assert get_aranet_api_key("FL") == "fl"

    @pytest.mark.parametrize("raw,expected", [(None, 7), ("3", 3), ("30", 7), ("-1", 0), ("x", 7)])
    def test_backfill_days(self, raw, expected):
        env = {} if raw is None else {"ARANET_BACKFILL_MAX_DAYS": raw}
        with patch.dict(os.environ, env, clear=True):
            assert get_aranet_backfill_max_days() == expected


class TestSiteConfig:
    BASE = {"capacity_kw": 10, "location": {"latitude": 1.0, "longitude": 2.0}}

    def test_valid(self):
        validate_site_config(
            "NY", {**self.BASE, "aranet": {"base_ids": [BASE_NY], "sensor_ids": ["0A1B2"]}}
        )

    def test_not_dict(self):
        with pytest.raises(ValueError, match="'aranet' must be a dictionary"):
            validate_site_config("NY", {**self.BASE, "aranet": [BASE_NY]})

    def test_base_ids_not_list(self):
        with pytest.raises(ValueError, match="aranet.base_ids"):
            validate_site_config("NY", {**self.BASE, "aranet": {"base_ids": BASE_NY}})


SITE_CONFIG = {"aranet": {"base_ids": [BASE_NY]}}


@patch.dict(os.environ, {"ARANET_API_KEY": "k"}, clear=True)
class TestFetchAranetData:
    def _run(self, last_time, mock_fetch):
        with (
            patch("home_monitor.fetcher.get_last_aranet_reading_time", return_value=last_time),
            patch("home_monitor.fetcher.upsert_aranet_sensors") as mock_upsert,
            patch("home_monitor.fetcher.insert_aranet_readings", return_value=3) as mock_insert,
            patch.object(AranetApiClient, "fetch_site_data", mock_fetch),
        ):
            fetch_aranet_data("NY", SITE_CONFIG, 1)
        return mock_upsert, mock_insert

    def _fetch_result(self):
        sensor = {"sensor_id": "4200001", "name": "NY - Living Room"}
        return MagicMock(return_value={"sensors": [sensor], "readings": [{"sensor_id": "4200001"}]})

    def test_recent_data_fetches_latest_only(self):
        mock_fetch = self._fetch_result()
        mock_upsert, mock_insert = self._run(
            datetime.now(timezone.utc) - timedelta(minutes=5), mock_fetch
        )
        assert mock_fetch.call_args.kwargs["history_since"] is None
        assert mock_fetch.call_args.kwargs["base_ids"] == [BASE_NY]
        mock_upsert.assert_called_once()
        assert mock_insert.call_args.args[2] == {"4200001": "NY - Living Room"}

    def test_gap_backfills_from_last_reading(self):
        last = datetime.now(timezone.utc) - timedelta(hours=3)
        mock_fetch = self._fetch_result()
        self._run(last, mock_fetch)
        assert mock_fetch.call_args.kwargs["history_since"] == last

    def test_first_run_backfills_max_days(self):
        mock_fetch = self._fetch_result()
        self._run(None, mock_fetch)
        since = mock_fetch.call_args.kwargs["history_since"]
        assert abs(datetime.now(timezone.utc) - since - timedelta(days=7)) < timedelta(minutes=1)

    @patch.dict(os.environ, {"ARANET_API_KEY": "k", "ARANET_BACKFILL_MAX_DAYS": "0"})
    def test_backfill_disabled(self):
        mock_fetch = self._fetch_result()
        self._run(None, mock_fetch)
        assert mock_fetch.call_args.kwargs["history_since"] is None

    def test_missing_key_skips(self):
        mock_fetch = self._fetch_result()
        with patch.dict(os.environ, {}, clear=True):
            self._run(None, mock_fetch)
        mock_fetch.assert_not_called()

    def test_missing_filters_skips(self):
        mock_fetch = self._fetch_result()
        with patch.object(AranetApiClient, "fetch_site_data", mock_fetch):
            fetch_aranet_data("NY", {"aranet": {}}, 1)
        mock_fetch.assert_not_called()

    def test_api_error_is_logged_not_raised(self):
        mock_fetch = MagicMock(side_effect=AranetAuthError("bad key"))
        _, mock_insert = self._run(None, mock_fetch)
        mock_insert.assert_not_called()
