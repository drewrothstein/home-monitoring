"""
iAqualink Pool API client for pool and spa monitoring data.

This client interfaces with the iAqualink/Zodiac pool control system API
to fetch pool temperatures, pump status, heater status, etc.

API endpoints:
- Login: https://prod.zodiac-io.com/users/v1/login
- Devices: https://r-api.iaqualink.net/devices.json
- Session: https://p-api.iaqualink.net/v1/mobile/session.json
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)

# iAqualink API endpoints
LOGIN_URL = "https://prod.zodiac-io.com"
DEVICES_URL = "https://r-api.iaqualink.net"
SESSION_URL = "https://p-api.iaqualink.net"

# API key (public, embedded in mobile app)
API_KEY = "EOOEMOW4YR6QNB07"


class IAqualinkApiError(Exception):
    """Custom exception for iAqualink API errors."""

    def __init__(self, message: str, status_code: Optional[int] = None, response_text: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text


class IAqualinkApiClient:
    """Client for iAqualink Pool API."""

    def __init__(
        self,
        email: str,
        password: str,
        device_name: Optional[str] = None,
        serial_number: Optional[str] = None,
    ):
        """
        Initialize iAqualink API client.

        Args:
            email: iAqualink account email
            password: iAqualink account password
            device_name: Optional device name to filter by (e.g., "Pool Controller")
            serial_number: Optional serial number to use directly (skips device lookup)
        """
        self.email = email
        self.password = password
        self.device_name = device_name
        self.serial_number = serial_number
        self.tokens: Dict[str, Any] = {}
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Content-Type": "application/json",
                "User-Agent": "okhttp/3.14.7",
            }
        )

    def _login(self) -> Dict[str, Any]:
        """
        Login to iAqualink API and get authentication tokens.

        Returns:
            Dictionary containing authentication tokens
        """
        url = f"{LOGIN_URL}/users/v1/login"
        payload = {
            "email": self.email,
            "password": self.password,
        }

        try:
            response = self._session.post(url, json=payload, timeout=30)
            response.raise_for_status()
            self.tokens = response.json()
            logger.debug(f"[iAqualink] Login successful for {self.email}")
            return self.tokens
        except requests.exceptions.HTTPError as e:
            raise IAqualinkApiError(
                f"Login failed: {e}",
                status_code=e.response.status_code if e.response else None,
                response_text=e.response.text if e.response else "",
            ) from e
        except requests.exceptions.RequestException as e:
            raise IAqualinkApiError(f"Login request failed: {e}") from e

    def _ensure_authenticated(self) -> None:
        """Ensure we have valid authentication tokens."""
        if not self.tokens or not self.tokens.get("authentication_token"):
            self._login()

    def get_devices(self) -> list:
        """
        Get list of devices associated with the account.

        Returns:
            List of device dictionaries
        """
        self._ensure_authenticated()

        url = f"{DEVICES_URL}/devices.json"
        params = {
            "api_key": API_KEY,
            "authentication_token": self.tokens.get("authentication_token"),
            "user_id": self.tokens.get("id"),
            "timestamp": int(datetime.now().timestamp() * 1000),
        }

        try:
            response = self._session.get(url, params=params, timeout=30)
            response.raise_for_status()
            devices = response.json()
            logger.debug(f"[iAqualink] Found {len(devices)} device(s)")
            return devices
        except requests.exceptions.HTTPError as e:
            raise IAqualinkApiError(
                f"Failed to get devices: {e}",
                status_code=e.response.status_code if e.response else None,
                response_text=e.response.text if e.response else "",
            ) from e
        except requests.exceptions.RequestException as e:
            raise IAqualinkApiError(f"Get devices request failed: {e}") from e

    def get_device_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """
        Get a specific device by name.

        Args:
            name: Device name to search for

        Returns:
            Device dictionary or None if not found
        """
        devices = self.get_devices()
        for device in devices:
            if device.get("name") == name:
                return device
        return None

    def get_device_by_serial(self, serial_number: str) -> Optional[Dict[str, Any]]:
        """
        Get a specific device by serial number.

        Args:
            serial_number: Device serial number

        Returns:
            Device dictionary or None if not found
        """
        devices = self.get_devices()
        for device in devices:
            if device.get("serial_number") == serial_number:
                return device
        return None

    def get_device_stats(self, serial_number: str) -> Dict[str, Any]:
        """
        Get current stats for a device.

        Args:
            serial_number: Device serial number

        Returns:
            Dictionary containing device stats (home_screen data)
        """
        self._ensure_authenticated()

        url = f"{SESSION_URL}/v1/mobile/session.json"
        payload = {
            "actionID": "command",
            "command": "get_home",
            "serial": serial_number,
            "sessionID": self.tokens.get("session_id"),
        }

        try:
            response = self._session.post(url, json=payload, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            raise IAqualinkApiError(
                f"Failed to get device stats: {e}",
                status_code=e.response.status_code if e.response else None,
                response_text=e.response.text if e.response else "",
            ) from e
        except requests.exceptions.RequestException as e:
            raise IAqualinkApiError(f"Get device stats request failed: {e}") from e

    def _parse_temperature(self, value: Any) -> Optional[int]:
        """
        Parse temperature value, returning None for invalid values.

        Args:
            value: Temperature value (could be int, str, or special value)

        Returns:
            Integer temperature or None if invalid
        """
        if value is None:
            return None
        try:
            temp = int(value)
            # -99 or similar sentinel values indicate no reading
            return temp if temp > -50 else None
        except (ValueError, TypeError):
            return None

    def _parse_pump_status(self, value: Any) -> bool:
        """
        Parse pump/heater status value.

        Args:
            value: Status value (could be int, str, or bool)

        Returns:
            Boolean indicating if device is on
        """
        return value == 1 or value == "1" or value is True

    def fetch_current_data(self) -> Dict[str, Any]:
        """
        Fetch current pool data.

        Returns:
            Dictionary with normalized pool data including:
            - timestamp: Current UTC timestamp
            - pool_temp: Pool temperature (°F)
            - spa_temp: Spa temperature (°F)
            - air_temp: Air temperature (°F)
            - pool_set_point: Pool heater set point (°F)
            - spa_set_point: Spa heater set point (°F)
            - pool_pump: Pool pump status (bool)
            - spa_pump: Spa pump status (bool)
            - pool_heater: Pool heater status (bool)
            - spa_heater: Spa heater status (bool)
            - raw_data: Raw API response
        """
        # Get device serial number
        serial_number = self.serial_number
        if not serial_number:
            if self.device_name:
                device = self.get_device_by_name(self.device_name)
                if not device:
                    raise IAqualinkApiError(f"Device '{self.device_name}' not found")
                serial_number = device.get("serial_number")
            else:
                # Get first device
                devices = self.get_devices()
                if not devices:
                    raise IAqualinkApiError("No devices found in account")
                serial_number = devices[0].get("serial_number")

        if not serial_number:
            raise IAqualinkApiError("No serial number available")

        # Get device stats
        device_stats = self.get_device_stats(serial_number)
        home_screen = device_stats.get("home_screen", [])

        # Extract data from home_screen array
        # Each item is a dict with a single key-value pair
        stats: Dict[str, Any] = {}
        keys_of_interest = [
            "pool_temp",
            "air_temp",
            "spa_temp",
            "pool_set_point",
            "spa_set_point",
            "spa_pump",
            "pool_pump",
            "spa_heater",
            "pool_heater",
        ]

        for item in home_screen:
            if isinstance(item, dict):
                for key in keys_of_interest:
                    if key in item:
                        stats[key] = item[key]

        # Normalize to our schema
        data = {
            "timestamp": datetime.now(timezone.utc),
            "pool_temp": self._parse_temperature(stats.get("pool_temp")),
            "spa_temp": self._parse_temperature(stats.get("spa_temp")),
            "air_temp": self._parse_temperature(stats.get("air_temp")),
            "pool_set_point": self._parse_temperature(stats.get("pool_set_point")),
            "spa_set_point": self._parse_temperature(stats.get("spa_set_point")),
            "pool_pump": self._parse_pump_status(stats.get("pool_pump")),
            "spa_pump": self._parse_pump_status(stats.get("spa_pump")),
            "pool_heater": self._parse_pump_status(stats.get("pool_heater")),
            "spa_heater": self._parse_pump_status(stats.get("spa_heater")),
            "raw_data": device_stats,
        }

        return data

    def list_devices(self) -> list:
        """
        List all devices with their basic info.

        Returns:
            List of device info dictionaries
        """
        devices = self.get_devices()
        return [
            {
                "name": d.get("name"),
                "serial_number": d.get("serial_number"),
                "device_type": d.get("device_type"),
                "owner_name": d.get("owner_name"),
            }
            for d in devices
        ]
