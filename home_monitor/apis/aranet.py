"""
Aranet Cloud API client for Aranet PRO sensors (Aranet4 PRO, Radon Plus PRO, ...).

API reference: https://aranet.cloud/openapi (OpenAPI spec at /api/openapi.json).

All endpoints are read-only GET requests authenticated with an ``ApiKey`` header. One key
gives access to one Aranet Cloud workspace, which can contain several base stations
(e.g. one per home); sensors are mapped to locations by their paired base station.

Endpoints used:
    GET /api/v1/sensors              - sensor metadata (id, user-given name, type, bases)
    GET /api/v1/sensors/types        - sensor type names (e.g. S4V1 -> "Aranet4")
    GET /api/v1/metrics              - metric ids -> names and unit ids -> unit names
    GET /api/v1/measurements/last    - latest measurement per sensor and metric
    GET /api/v1/telemetry/last       - latest battery / RSSI per sensor
    GET /api/v1/measurements/history - measurements for a period (max 7 days, paged)
    GET /api/v1/telemetry/history    - telemetry for a period (paged)

Readings use the units selected in the workspace (e.g. temperature in °F), so values are
normalized to canonical units here (see CANONICAL_UNITS). The original reading is kept in
``raw_data``.
"""

import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

API_HOST = "https://aranet.cloud"
API_BASE = f"{API_HOST}/api/v1"

# Cloudflare in front of aranet.cloud rejects the default python-requests/urllib
# User-Agent (error 1010), so send a generic client name.
USER_AGENT = "home-monitoring/1.0"

# Maximum period the history endpoints serve
HISTORY_MAX_DAYS = 7
# Safety limit when following history `next` page links
HISTORY_MAX_PAGES = 500

# Metric and sensor type catalogs rarely change; cache them in-process (per API key)
CATALOG_CACHE_TTL_SECONDS = 6 * 60 * 60

# Stable metric keys for metric ids observed in Aranet Cloud. Unknown metrics fall back
# to a name-based match, then a slug of the metric name.
METRIC_KEYS_BY_ID = {
    "1": "temperature",
    "2": "humidity",
    "3": "co2",
    "4": "pressure",
    "61": "rssi",
    "62": "battery",
}

# (substring of lowercased metric name, key) - checked in order
METRIC_KEYS_BY_NAME = [
    ("radon", "radon"),
    ("co₂", "co2"),
    ("co2", "co2"),
    ("temperature", "temperature"),
    ("humidity", "humidity"),
    ("atmospheric pressure", "pressure"),
    ("battery", "battery"),
    ("rssi", "rssi"),
]

# Canonical unit stored for each metric key
CANONICAL_UNITS = {
    "temperature": "°F",
    "humidity": "%",
    "co2": "ppm",
    "pressure": "hPa",
    "radon": "Bq/m³",
    "battery": "%",
    "rssi": "dBm",
}

PCI_PER_L_IN_BQ_PER_M3 = 37.0


class AranetError(Exception):
    """Base exception for Aranet Cloud API errors."""

    pass


class AranetAuthError(AranetError):
    """Raised when the API key is missing, invalid, or disabled."""

    pass


def _convert_temperature(value: float, unit: str) -> Optional[float]:
    u = unit.strip().lower()
    if u in ("°f", "f", "degf"):
        return value
    if u in ("°c", "c", "cel", "degc"):
        return value * 9.0 / 5.0 + 32.0
    if u == "k":
        return (value - 273.15) * 9.0 / 5.0 + 32.0
    return None


_PRESSURE_TO_HPA = {
    "hpa": 1.0,
    "mbar": 1.0,
    "pa": 0.01,
    "kpa": 10.0,
    "bar": 1000.0,
    "atm": 1013.25,
    "mmhg": 1.333223684,
    "inhg": 33.86389,
    "psi": 68.94757,
}


def _convert_pressure(value: float, unit: str) -> Optional[float]:
    factor = _PRESSURE_TO_HPA.get(unit.strip().lower())
    return value * factor if factor is not None else None


def _convert_radon(value: float, unit: str) -> Optional[float]:
    u = unit.strip().lower().replace("³", "3")
    if u in ("bq/m3", "bq/m^3"):
        return value
    if u in ("pci/l",):
        return value * PCI_PER_L_IN_BQ_PER_M3
    return None


def _convert_rssi(value: float, unit: str) -> Optional[float]:
    u = unit.strip().lower()
    if u == "dbm":
        return value
    if u == "dbw":
        return value + 30.0
    return None


def _identity_for(*units: str):
    accepted = {u.lower() for u in units}

    def convert(value: float, unit: str) -> Optional[float]:
        return value if unit.strip().lower() in accepted else None

    return convert


_CONVERTERS = {
    "temperature": _convert_temperature,
    "pressure": _convert_pressure,
    "radon": _convert_radon,
    "rssi": _convert_rssi,
    "humidity": _identity_for("%", "%rh"),
    "co2": _identity_for("ppm"),
    "battery": _identity_for("%"),
}


def normalize_value(
    metric_key: str, value: Optional[float], unit_name: Optional[str]
) -> Tuple[Optional[float], Optional[str]]:
    """
    Convert a reading to the canonical unit for its metric.

    Returns (value, unit). Metrics without a canonical unit, or readings in a unit we do not
    know how to convert, are returned unchanged with their original unit name.
    """
    if value is None:
        return None, unit_name
    canonical = CANONICAL_UNITS.get(metric_key)
    converter = _CONVERTERS.get(metric_key)
    if canonical and converter and unit_name is not None:
        converted = converter(float(value), unit_name)
        if converted is not None:
            return converted, canonical
        logger.warning(
            f"[Aranet] Unknown unit '{unit_name}' for metric '{metric_key}'; storing as-is"
        )
    return float(value), unit_name


def metric_key_for(metric_id: str, metric_name: Optional[str]) -> str:
    """Return a stable key (e.g. 'co2', 'radon') for an Aranet metric."""
    if metric_id in METRIC_KEYS_BY_ID:
        return METRIC_KEYS_BY_ID[metric_id]
    name = (metric_name or "").lower()
    for needle, key in METRIC_KEYS_BY_NAME:
        if needle in name:
            return key
    slug = re.sub(r"[^a-z0-9]+", "_", name).strip("_")
    return slug or f"metric_{metric_id}"


def parse_timestamp(value: Optional[str]) -> Optional[datetime]:
    """Parse an Aranet ISO timestamp like '2026-11-09T14:54:21Z' to tz-aware UTC."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def format_timestamp(dt: datetime) -> str:
    """Format a datetime as the UTC ISO string the API expects."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# api_key -> (fetched_at_monotonic, metrics_catalog, sensor_type_names)
_catalog_cache: Dict[str, Tuple[float, Dict[str, Dict[str, Any]], Dict[str, str]]] = {}


def clear_catalog_cache() -> None:
    """Clear cached metric and sensor type catalogs (used by tests)."""
    _catalog_cache.clear()


class AranetApiClient:
    """Client for the Aranet Cloud API."""

    def __init__(self, api_key: str, timeout: int = 30):
        """
        Initialize Aranet Cloud API client.

        Args:
            api_key: Aranet Cloud API key (Settings > API in Aranet Cloud)
            timeout: Request timeout in seconds
        """
        self.api_key = api_key
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {"ApiKey": api_key, "Accept": "application/json", "User-Agent": USER_AGENT}
        )

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    def _handle_api_response(self, response: requests.Response) -> Dict[str, Any]:
        """
        Validate an API response and return its JSON body.

        Raises:
            AranetAuthError: On 401/403 (invalid/disabled key or blocked client)
            AranetError: On any other error
        """
        if response.status_code in (401, 403):
            text = response.text or ""
            if "cloudflare" in text.lower() or "error 1010" in text.lower():
                raise AranetAuthError(
                    f"Aranet Cloud blocked the request at Cloudflare (HTTP {response.status_code}). "
                    "The client User-Agent may be blocked."
                )
            raise AranetAuthError(
                f"Aranet Cloud rejected the API key (HTTP {response.status_code}). "
                "Check ARANET_API_KEY and that the key is enabled in Aranet Cloud "
                "(Settings > API)."
            )
        if response.status_code == 429:
            raise AranetError("Aranet Cloud rate limit exceeded (HTTP 429). Try again later.")
        if response.status_code != 200:
            raise AranetError(
                f"Aranet Cloud request failed with status {response.status_code}: "
                f"{(response.text or '')[:500]}"
            )

        try:
            data = response.json()
        except ValueError as e:
            raise AranetError(f"Failed to parse Aranet Cloud response: {e}") from e

        if not isinstance(data, dict):
            raise AranetError(f"Unexpected Aranet Cloud response type: {type(data).__name__}")

        errors = data.get("error")
        if errors:
            logger.warning(f"[Aranet] API returned warnings/errors: {errors}")

        return data

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """GET a path relative to the API host (e.g. '/api/v1/sensors')."""
        url = path if path.startswith("http") else f"{API_HOST}{path}"
        try:
            response = self.session.get(url, params=params, timeout=self.timeout)
        except requests.RequestException as e:
            raise AranetError(f"Failed to connect to Aranet Cloud: {e}") from e
        return self._handle_api_response(response)

    # ------------------------------------------------------------------
    # Raw endpoints
    # ------------------------------------------------------------------

    def get_sensors(self) -> List[Dict[str, Any]]:
        """Get metadata for all sensors in the workspace."""
        return self._get("/api/v1/sensors").get("sensors") or []

    def get_bases(self) -> List[Dict[str, Any]]:
        """Get all base stations in the workspace."""
        return self._get("/api/v1/bases").get("bases") or []

    def get_metrics(self) -> List[Dict[str, Any]]:
        """Get the metric catalog (ids, names, and units)."""
        return self._get("/api/v1/metrics").get("metrics") or []

    def get_sensor_types(self) -> List[Dict[str, Any]]:
        """Get the sensor type catalog."""
        return self._get("/api/v1/sensors/types").get("sensorTypes") or []

    def get_last_measurements(self) -> List[Dict[str, Any]]:
        """Get the latest measurement for every sensor and metric."""
        return self._get("/api/v1/measurements/last").get("readings") or []

    def get_last_telemetry(self) -> List[Dict[str, Any]]:
        """Get the latest telemetry (battery, RSSI) for every sensor."""
        return self._get("/api/v1/telemetry/last").get("readings") or []

    def _get_history(self, path: str, start: datetime, end: datetime) -> List[Dict[str, Any]]:
        """Get all history readings for a period, following `next` page links."""
        earliest = end - timedelta(days=HISTORY_MAX_DAYS)
        if start < earliest:
            start = earliest
        if start >= end:
            return []

        readings: List[Dict[str, Any]] = []
        data = self._get(path, {"from": format_timestamp(start), "to": format_timestamp(end)})
        pages = 1
        while True:
            readings.extend(data.get("readings") or [])
            next_link = data.get("next")
            if not next_link:
                break
            if pages >= HISTORY_MAX_PAGES:
                logger.warning(f"[Aranet] Stopped paging {path} after {pages} pages")
                break
            data = self._get(next_link)
            pages += 1
        return readings

    def get_measurement_history(self, start: datetime, end: datetime) -> List[Dict[str, Any]]:
        """Get measurements for all sensors between start and end (max 7 days)."""
        return self._get_history("/api/v1/measurements/history", start, end)

    def get_telemetry_history(self, start: datetime, end: datetime) -> List[Dict[str, Any]]:
        """Get telemetry for all sensors between start and end (max 7 days)."""
        return self._get_history("/api/v1/telemetry/history", start, end)

    # ------------------------------------------------------------------
    # Catalogs
    # ------------------------------------------------------------------

    def get_catalogs(self) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, str]]:
        """
        Get the metric catalog and sensor type names, cached in-process.

        Returns:
            (metrics, sensor_type_names) where metrics maps metric id ->
            {"name", "key", "units": {unit_id: unit_name}} and sensor_type_names maps
            sensor type id (e.g. "S4V1") -> name (e.g. "Aranet4").
        """
        cached = _catalog_cache.get(self.api_key)
        if cached and time.monotonic() - cached[0] < CATALOG_CACHE_TTL_SECONDS:
            return cached[1], cached[2]

        metrics: Dict[str, Dict[str, Any]] = {}
        for metric in self.get_metrics():
            metric_id = str(metric.get("id"))
            metrics[metric_id] = {
                "name": metric.get("name"),
                "key": metric_key_for(metric_id, metric.get("name")),
                "units": {str(u.get("id")): u.get("name") for u in metric.get("units") or []},
            }

        try:
            sensor_types = {str(t.get("id")): t.get("name") for t in self.get_sensor_types()}
        except AranetError as e:
            logger.warning(f"[Aranet] Could not load sensor types: {e}")
            sensor_types = {}

        _catalog_cache[self.api_key] = (time.monotonic(), metrics, sensor_types)
        return metrics, sensor_types

    # ------------------------------------------------------------------
    # Normalization
    # ------------------------------------------------------------------

    @staticmethod
    def filter_sensors(
        sensors: Iterable[Dict[str, Any]],
        base_ids: Optional[Iterable[str]] = None,
        sensor_ids: Optional[Iterable[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Select sensors paired with any of base_ids or listed in sensor_ids.

        Sensor ids match either the cloud id (e.g. "4200001") or the device id
        (e.g. "0A1B2"). With neither filter given, all sensors are returned.
        """
        bases = {str(b) for b in base_ids or []}
        ids = {str(s).upper() for s in sensor_ids or []}
        if not bases and not ids:
            return list(sensors)

        selected = []
        for sensor in sensors:
            sensor_bases = {str(b) for b in sensor.get("bases") or []}
            sensor_keys = {str(sensor.get("id")).upper(), str(sensor.get("sensorId")).upper()}
            if sensor_bases & bases or sensor_keys & ids:
                selected.append(sensor)
        return selected

    @staticmethod
    def normalize_sensor(
        sensor: Dict[str, Any], sensor_type_names: Dict[str, str]
    ) -> Dict[str, Any]:
        """Normalize a sensor metadata record."""
        bases = [str(b) for b in sensor.get("bases") or []]
        sensor_type = sensor.get("type")
        return {
            "sensor_id": str(sensor.get("id")),
            "device_id": sensor.get("sensorId"),
            "name": sensor.get("name"),
            "sensor_type": sensor_type,
            "sensor_type_name": sensor_type_names.get(str(sensor_type)),
            "base_id": bases[0] if bases else None,
            "metric_ids": [
                str(s.get("metric")) for s in sensor.get("skills") or [] if s.get("metric")
            ],
            "raw_data": sensor,
        }

    @staticmethod
    def normalize_reading(
        reading: Dict[str, Any], metrics: Dict[str, Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """
        Normalize a measurement or telemetry reading.

        Returns None for readings without a sensor, metric, timestamp, or value.
        """
        sensor_id = reading.get("sensor")
        metric_id = reading.get("metric")
        ts = parse_timestamp(reading.get("time"))
        value = reading.get("value")
        if sensor_id is None or metric_id is None or ts is None or value is None:
            return None

        metric_id = str(metric_id)
        metric = metrics.get(metric_id, {})
        metric_name = metric.get("name")
        metric_key = metric.get("key") or metric_key_for(metric_id, metric_name)
        unit_id = reading.get("unit")
        unit_name = metric.get("units", {}).get(str(unit_id)) if unit_id is not None else None

        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None
        normalized, unit = normalize_value(metric_key, numeric, unit_name)

        probe = reading.get("probe")
        return {
            "sensor_id": str(sensor_id),
            "timestamp": ts,
            "metric_id": metric_id,
            "metric_key": metric_key,
            "metric_name": metric_name,
            "value": normalized,
            "unit": unit,
            "probe": int(probe) if probe is not None else 0,
            "novelty": reading.get("novelty"),
            "raw_data": reading,
        }

    def fetch_site_data(
        self,
        base_ids: Optional[Iterable[str]] = None,
        sensor_ids: Optional[Iterable[str]] = None,
        history_since: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Fetch sensors and readings for one site.

        Args:
            base_ids: Base station ids whose sensors belong to the site
            sensor_ids: Additional sensor ids (cloud id or device id) for the site
            history_since: If set, also fetch measurement/telemetry history from this time
                (capped to the 7 days the API serves) to fill gaps

        Returns:
            Dictionary with:
            - sensors: normalized sensor records for the site
            - readings: normalized readings (measurements + telemetry) for those sensors
        """
        metrics, sensor_types = self.get_catalogs()
        sensors = [
            self.normalize_sensor(s, sensor_types)
            for s in self.filter_sensors(self.get_sensors(), base_ids, sensor_ids)
        ]
        wanted = {s["sensor_id"] for s in sensors}

        raw_readings = self.get_last_measurements() + self.get_last_telemetry()
        if history_since is not None:
            now = datetime.now(timezone.utc)
            raw_readings += self.get_measurement_history(history_since, now)
            raw_readings += self.get_telemetry_history(history_since, now)

        readings = []
        seen = set()
        for raw in raw_readings:
            if str(raw.get("sensor")) not in wanted:
                continue
            reading = self.normalize_reading(raw, metrics)
            if reading is None:
                continue
            key = (
                reading["sensor_id"],
                reading["metric_id"],
                reading["probe"],
                reading["timestamp"],
            )
            if key in seen:
                continue
            seen.add(key)
            readings.append(reading)

        return {"sensors": sensors, "readings": readings}
