"""
Tank Utility API client for propane tank monitoring.

API documentation: Based on reverse-engineering from HA integration
https://github.com/SmithAdamL/ha-generac-tank-utility

Note: Tank Utility was acquired by ANOVA and is no longer part of Generac.
The legacy API may eventually be deprecated.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import requests

logger = logging.getLogger(__name__)

# API endpoints
API_BASE = "https://data.tankutility.com/api"
GET_TOKEN_ENDPOINT = f"{API_BASE}/getToken"
DEVICES_ENDPOINT = f"{API_BASE}/devices"
DEVICE_DATA_ENDPOINT = f"{API_BASE}/devices/{{device_id}}"


class TankUtilityError(Exception):
    """Base exception for Tank Utility API errors."""

    pass


class TankUtilityAuthError(TankUtilityError):
    """Raised when authentication fails."""

    pass


class TankUtilityApiClient:
    """Client for Tank Utility API."""

    def __init__(self, email: str, password: str, tz: Optional[str] = None):
        """
        Initialize Tank Utility API client.

        Args:
            email: Tank Utility account email
            password: Tank Utility account password
            tz: Timezone name (e.g., 'America/New_York') for converting UTC timestamps
                to local time. If not provided, timestamps remain in UTC.
        """
        self.email = email
        self.password = password
        self._token: Optional[str] = None

        # Store timezone for local time conversion
        if tz:
            try:
                self.tz: Optional[ZoneInfo] = ZoneInfo(tz)
            except Exception as e:
                logger.warning(f"Invalid timezone '{tz}': {e}. Timestamps will remain in UTC.")
                self.tz = None
        else:
            self.tz = None

    def _get_token(self, force_refresh: bool = False) -> str:
        """
        Get API token using Basic Auth.

        Args:
            force_refresh: Force getting a new token even if cached

        Returns:
            API token string

        Raises:
            TankUtilityAuthError: If authentication fails
            TankUtilityError: If token request fails
        """
        if self._token and not force_refresh:
            return self._token

        try:
            response = requests.get(
                GET_TOKEN_ENDPOINT,
                auth=(self.email, self.password),
                timeout=30,
            )
        except requests.RequestException as e:
            raise TankUtilityError(f"Failed to connect to Tank Utility API: {e}") from e

        if response.status_code == 401:
            raise TankUtilityAuthError("Invalid Tank Utility credentials")

        if response.status_code != 200:
            raise TankUtilityError(
                f"Token request failed with status {response.status_code}: {response.text}"
            )

        try:
            data = response.json()
        except ValueError as e:
            raise TankUtilityError(f"Failed to parse token response: {e}") from e

        token = data.get("token")
        if not token:
            raise TankUtilityError("No token in API response")

        self._token = token
        return self._token

    def get_devices(self) -> List[str]:
        """
        Get list of device IDs for this account.

        Returns:
            List of device ID strings

        Raises:
            TankUtilityError: If request fails
        """
        token = self._get_token()
        url = f"{DEVICES_ENDPOINT}?token={token}"

        try:
            response = requests.get(url, timeout=30)
        except requests.RequestException as e:
            raise TankUtilityError(f"Failed to fetch device list: {e}") from e

        if response.status_code == 401:
            # Token may have expired, try refreshing
            token = self._get_token(force_refresh=True)
            url = f"{DEVICES_ENDPOINT}?token={token}"
            response = requests.get(url, timeout=30)

        if response.status_code != 200:
            raise TankUtilityError(f"Device list request failed with status {response.status_code}")

        try:
            data = response.json()
        except ValueError as e:
            raise TankUtilityError(f"Failed to parse device list response: {e}") from e

        return data.get("devices", [])

    def get_device_data(self, device_id: str) -> Dict[str, Any]:
        """
        Get data for a specific device.

        Args:
            device_id: Tank device ID

        Returns:
            Device data dictionary

        Raises:
            TankUtilityError: If request fails
        """
        token = self._get_token()
        url = f"{DEVICE_DATA_ENDPOINT.format(device_id=device_id)}?token={token}"

        try:
            response = requests.get(url, timeout=30)
        except requests.RequestException as e:
            raise TankUtilityError(f"Failed to fetch device data: {e}") from e

        if response.status_code == 401:
            # Token may have expired, try refreshing
            token = self._get_token(force_refresh=True)
            url = f"{DEVICE_DATA_ENDPOINT.format(device_id=device_id)}?token={token}"
            response = requests.get(url, timeout=30)

        if response.status_code != 200:
            raise TankUtilityError(f"Device data request failed with status {response.status_code}")

        try:
            data = response.json()
        except ValueError as e:
            raise TankUtilityError(f"Failed to parse device data response: {e}") from e

        return data

    def _convert_to_local(self, dt: datetime) -> datetime:
        """
        Convert a UTC datetime to local time if timezone is configured.

        Args:
            dt: UTC datetime

        Returns:
            Datetime in local timezone (naive) if tz is configured,
            otherwise returns the original UTC datetime (naive for consistency).
        """
        if self.tz:
            # Convert to local timezone, then remove tzinfo for storage
            local_dt = dt.astimezone(self.tz)
            return local_dt.replace(tzinfo=None)
        else:
            # No timezone configured, return as naive UTC
            if dt.tzinfo is not None:
                return dt.replace(tzinfo=None)
            return dt

    def fetch_current_data(self, device_id: str) -> Dict[str, Any]:
        """
        Fetch current tank data in normalized format.

        Args:
            device_id: Tank device ID

        Returns:
            Dictionary with normalized tank data including:
            - timestamp: Reading timestamp (local time if tz configured, else UTC)
            - tank_level_percent: Tank fill level (0-100%)
            - tank_level_gallons: Estimated gallons remaining (if capacity known)
            - capacity_gallons: Tank capacity in gallons
            - temperature_f: Temperature in Fahrenheit
            - battery_status: Battery status string (good/low/critical)
            - battery_warn: Battery warning flag
            - battery_crit: Battery critical flag
            - fuel_type: Type of fuel (propane, etc.)
            - device_name: User-defined device name
            - raw_data: Complete API response
        """
        raw_data = self.get_device_data(device_id)
        device_info = raw_data.get("device", {})
        last_reading = device_info.get("lastReading", {})

        # Parse timestamp from lastReading (API returns UTC)
        timestamp = None
        time_iso = last_reading.get("time_iso")
        if time_iso:
            try:
                # Parse ISO format: "2026-01-03T04:23:25.000Z"
                timestamp = datetime.fromisoformat(time_iso.replace("Z", "+00:00"))
            except ValueError:
                pass

        if not timestamp:
            # Fall back to epoch milliseconds
            time_ms = last_reading.get("time")
            if time_ms:
                timestamp = datetime.fromtimestamp(time_ms / 1000, tz=timezone.utc)

        if not timestamp:
            timestamp = datetime.now(timezone.utc)

        # Convert to local time if timezone is configured
        timestamp = self._convert_to_local(timestamp)

        # Extract tank level
        tank_level_percent = last_reading.get("tank")

        # Calculate gallons if we have capacity
        capacity_gallons = device_info.get("capacity")
        tank_level_gallons = None
        if tank_level_percent is not None and capacity_gallons:
            tank_level_gallons = (tank_level_percent / 100.0) * capacity_gallons

        # Extract temperature
        temperature_f = last_reading.get("temperature")

        # Extract battery status - it's in device_info, not lastReading
        battery_status = device_info.get("battery_level", "unknown")
        battery_warn = device_info.get("battery_warn", False)
        battery_crit = device_info.get("battery_crit", False)

        return {
            "timestamp": timestamp,
            "tank_level_percent": tank_level_percent,
            "tank_level_gallons": tank_level_gallons,
            "capacity_gallons": capacity_gallons,
            "temperature_f": temperature_f,
            "battery_status": battery_status,
            "battery_warn": battery_warn,
            "battery_crit": battery_crit,
            "fuel_type": device_info.get("fuel_type"),
            "device_name": device_info.get("name"),
            "orientation": device_info.get("orientation"),
            "average_consumption": device_info.get("average_consumption"),
            "raw_data": raw_data,
        }
