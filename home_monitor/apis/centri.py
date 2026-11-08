"""
Centri Connect (MyPropane) API client for propane tank monitoring.

API reference: Based on the aiocentriconnect library used by the Home Assistant
CentriConnect/MyPropane integration (https://pypi.org/project/aiocentriconnect/).
That library is async (aiohttp); this client is a small synchronous equivalent
using requests to match the rest of this project.

The API is a single GET request with credentials in the URL: user ID, device ID,
and a per-device auth code (all shown in the MyPropane app / Centri portal):

    GET https://api.centriconnect.com/centriconnect/{user_id}/device/{device_id}/all-data
        ?device_auth={device_auth}

The tank monitor only posts new data a few times per day, and the response includes
``NextPostTimeIso`` so callers can avoid polling between posts.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

import requests

logger = logging.getLogger(__name__)

API_BASE = "https://api.centriconnect.com/centriconnect"
ALL_DATA_ENDPOINT = f"{API_BASE}/{{user_id}}/device/{{device_id}}/all-data"

LITERS_PER_GALLON = 3.785411784

# Battery level estimate (matches aiocentriconnect): <=3.5V is 0%, >=4.0V is 100%
BATTERY_EMPTY_VOLTS = 3.5
BATTERY_FULL_VOLTS = 4.0
# Status thresholds applied to the estimated battery percentage
BATTERY_LOW_PERCENT = 20.0
BATTERY_CRITICAL_PERCENT = 5.0


class CentriError(Exception):
    """Base exception for Centri Connect API errors."""

    pass


class CentriAuthError(CentriError):
    """Raised when the user ID / device ID / device auth combination is rejected."""

    pass


class CentriApiClient:
    """Client for the Centri Connect (MyPropane) API."""

    def __init__(
        self,
        user_id: str,
        device_id: str,
        device_auth: str,
        tz: Optional[str] = None,
        timeout: int = 30,
    ):
        """
        Initialize Centri Connect API client.

        Args:
            user_id: Centri account user ID (UUID)
            device_id: Tank monitor device ID (UUID)
            device_auth: Per-device auth code
            tz: Timezone name (e.g., 'America/New_York') for converting UTC timestamps
                to local time. If not provided, timestamps remain in UTC.
            timeout: Request timeout in seconds
        """
        self.user_id = user_id
        self.device_id = device_id
        self.device_auth = device_auth
        self.timeout = timeout

        if tz:
            try:
                self.tz: Optional[ZoneInfo] = ZoneInfo(tz)
            except Exception as e:
                logger.warning(f"Invalid timezone '{tz}': {e}. Timestamps will remain in UTC.")
                self.tz = None
        else:
            self.tz = None

    def _handle_api_response(self, response: requests.Response) -> Dict[str, Any]:
        """
        Validate an API response and return this device's data dictionary.

        Raises:
            CentriAuthError: On 401/403 or a "device not found" response
            CentriError: On any other error
        """
        if response.status_code in (401, 403):
            raise CentriAuthError(
                f"Centri API rejected credentials (HTTP {response.status_code}). "
                "Check CENTRI_USER_ID, CENTRI_DEVICE_AUTH, and centri.device_id."
            )
        if response.status_code == 404:
            raise CentriAuthError(
                "Centri device not found (HTTP 404). Check CENTRI_USER_ID, "
                "CENTRI_DEVICE_AUTH, and centri.device_id."
            )
        if response.status_code == 429:
            raise CentriError("Centri API rate limit exceeded (HTTP 429). Try again later.")
        if response.status_code != 200:
            raise CentriError(
                f"Centri API request failed with status {response.status_code}: "
                f"{response.text[:500]}"
            )

        try:
            data = response.json()
        except ValueError as e:
            raise CentriError(f"Failed to parse Centri API response: {e}") from e

        if not data:
            raise CentriError("Centri API returned an empty response")

        if isinstance(data, dict) and "error" in data:
            error = data.get("error")
            message = data.get("message", "")
            if error == "NotFound":
                raise CentriAuthError(
                    f"Centri device not found: {message}. Check CENTRI_USER_ID, "
                    "CENTRI_DEVICE_AUTH, and centri.device_id."
                )
            raise CentriError(f"Centri API error: {error} {message}".strip())

        if not isinstance(data, dict) or self.device_id not in data:
            keys = list(data.keys()) if isinstance(data, dict) else type(data).__name__
            raise CentriError(f"Centri API response missing device {self.device_id}: {keys}")

        return data[self.device_id]

    def get_device_data(self) -> Dict[str, Any]:
        """
        Get the raw data dictionary for the configured device.

        Returns:
            Device data dictionary (e.g. TankLevel, TankSize, BatteryVolts, ...)
        """
        url = ALL_DATA_ENDPOINT.format(user_id=self.user_id, device_id=self.device_id)
        try:
            response = requests.get(
                url, params={"device_auth": self.device_auth}, timeout=self.timeout
            )
        except requests.RequestException as e:
            raise CentriError(f"Failed to connect to Centri API: {e}") from e

        return self._handle_api_response(response)

    @staticmethod
    def parse_timestamp(value: Optional[str]) -> Optional[datetime]:
        """Parse a Centri UTC timestamp like '2026-02-27 22:00:31.000' (tz-aware UTC)."""
        if not value:
            return None
        try:
            dt = datetime.fromisoformat(value.strip().replace(" ", "T").replace("Z", ""))
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def _convert_to_local(self, dt: datetime) -> datetime:
        """Convert a UTC datetime to naive local time (or naive UTC if no tz configured)."""
        if self.tz:
            return dt.astimezone(self.tz).replace(tzinfo=None)
        if dt.tzinfo is not None:
            return dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt

    @staticmethod
    def _to_float(value: Any) -> Optional[float]:
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def battery_percent(volts: Optional[float]) -> Optional[float]:
        """Estimate battery percentage from voltage."""
        if volts is None:
            return None
        span = BATTERY_FULL_VOLTS - BATTERY_EMPTY_VOLTS
        return min(1.0, max((volts - BATTERY_EMPTY_VOLTS) / span, 0.0)) * 100

    def fetch_current_data(self) -> Dict[str, Any]:
        """
        Fetch current tank data in normalized format (same shape as TankUtilityApiClient).

        Returns:
            Dictionary with:
            - timestamp: Last post time (local time if tz configured, else UTC; naive)
            - next_post_time: Next expected post time (tz-aware UTC) or None
            - tank_level_percent, tank_level_gallons, capacity_gallons
            - temperature_f
            - battery_status ("good"/"low"/"critical"/"unknown"), battery_warn, battery_crit
            - battery_volts, battery_percent, solar_volts, lte_signal_dbm
            - fuel_type, device_name, alert_status
            - raw_data: The device's API data dictionary
        """
        raw_data = self.get_device_data()

        last_post = self.parse_timestamp(raw_data.get("LastPostTimeIso"))
        timestamp = self._convert_to_local(last_post or datetime.now(timezone.utc))

        tank_level_percent = self._to_float(raw_data.get("TankLevel"))

        capacity = self._to_float(raw_data.get("TankSize"))
        unit = str(raw_data.get("TankSizeUnit") or "Gallons").lower()
        if capacity is not None and unit.startswith("lit"):
            capacity = capacity / LITERS_PER_GALLON
        capacity_gallons = capacity or None

        tank_level_gallons = None
        if tank_level_percent is not None and capacity_gallons:
            tank_level_gallons = (tank_level_percent / 100.0) * capacity_gallons

        battery_volts = self._to_float(raw_data.get("BatteryVolts"))
        battery_pct = self.battery_percent(battery_volts)
        if battery_pct is None:
            battery_status = "unknown"
        elif battery_pct <= BATTERY_CRITICAL_PERCENT:
            battery_status = "critical"
        elif battery_pct <= BATTERY_LOW_PERCENT:
            battery_status = "low"
        else:
            battery_status = "good"

        return {
            "timestamp": timestamp,
            "next_post_time": self.parse_timestamp(raw_data.get("NextPostTimeIso")),
            "tank_level_percent": tank_level_percent,
            "tank_level_gallons": tank_level_gallons,
            "capacity_gallons": capacity_gallons,
            "temperature_f": self._to_float(raw_data.get("DeviceTempFahrenheit")),
            "battery_status": battery_status,
            "battery_warn": battery_status in ("low", "critical"),
            "battery_crit": battery_status == "critical",
            "battery_volts": battery_volts,
            "battery_percent": battery_pct,
            "solar_volts": self._to_float(raw_data.get("SolarVolts")),
            "lte_signal_dbm": self._to_float(raw_data.get("SignalQualLTE")),
            "fuel_type": "propane",
            "device_name": raw_data.get("DeviceName"),
            "alert_status": raw_data.get("AlertStatus"),
            "raw_data": raw_data,
        }
