"""
Tesla Energy API client for battery and gateway data via Teslemetry.

Teslemetry provides a simplified interface to the Tesla Fleet API.
API documentation: https://teslemetry.com/docs
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)


class TeslaApiError(Exception):
    """Custom exception for Tesla API errors."""

    def __init__(self, message: str, status_code: int | None = None, response_text: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text


class TeslaApiClient:
    """Client for Tesla Energy API via Teslemetry."""

    BASE_URL = "https://api.teslemetry.com/api/1/energy_sites"

    def __init__(self, access_token: str, energy_site_id: str):
        """
        Initialize Tesla API client via Teslemetry.

        Args:
            access_token: Teslemetry API access token (obtained from Teslemetry console)
            energy_site_id: Energy site ID (gateway ID)
        """
        self.access_token = access_token
        self.energy_site_id = energy_site_id
        self.headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

    def _parse_response(self, response: requests.Response, endpoint: str) -> Dict[str, Any]:
        """
        Safely parse JSON response with detailed error handling.

        Args:
            response: The requests Response object
            endpoint: Name of the endpoint for error messages

        Returns:
            Parsed JSON as dictionary

        Raises:
            TeslaApiError: If response cannot be parsed as JSON
        """
        response.raise_for_status()

        # Check for empty response
        if not response.text or not response.text.strip():
            raise TeslaApiError(
                f"Tesla API returned empty response for {endpoint}",
                status_code=response.status_code,
                response_text="",
            )

        try:
            return response.json()
        except requests.exceptions.JSONDecodeError as e:
            # Log the actual response for debugging
            logger.error(
                f"Tesla API returned invalid JSON for {endpoint}. "
                f"Status: {response.status_code}, Response: {response.text[:500]!r}"
            )
            raise TeslaApiError(
                f"Tesla API returned invalid JSON for {endpoint}: {e}",
                status_code=response.status_code,
                response_text=response.text[:500],
            ) from e

    def get_site_status(self) -> Dict[str, Any]:
        """
        Get current site status.

        Returns:
            Dictionary containing site status data
        """
        url = f"{self.BASE_URL}/{self.energy_site_id}/status"
        response = requests.get(url, headers=self.headers, timeout=30)
        return self._parse_response(response, "site_status")

    def get_live_status(self) -> Dict[str, Any]:
        """
        Get live status including power flow.

        Returns:
            Dictionary containing live status data including power flows
        """
        url = f"{self.BASE_URL}/{self.energy_site_id}/live_status"
        response = requests.get(url, headers=self.headers, timeout=30)
        return self._parse_response(response, "live_status")

    def get_power_history(
        self, start_date: Optional[datetime] = None, end_date: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """
        Get power history data.

        Args:
            start_date: Start date for history (optional)
            end_date: End date for history (optional)

        Returns:
            Dictionary containing power history data
        """
        url = f"{self.BASE_URL}/{self.energy_site_id}/history"
        params = {}
        if start_date:
            params["start_date"] = start_date.isoformat()
        if end_date:
            params["end_date"] = end_date.isoformat()

        response = requests.get(url, headers=self.headers, params=params, timeout=30)
        return self._parse_response(response, "power_history")

    def get_site_info(self) -> Dict[str, Any]:
        """
        Get site information including battery bank details.

        Returns:
            Dictionary containing site information including batteries array
        """
        url = f"{self.BASE_URL}/{self.energy_site_id}/site_info"
        response = requests.get(url, headers=self.headers, timeout=30)
        return self._parse_response(response, "site_info")

    def fetch_current_data(self) -> Dict[str, Any]:
        """
        Fetch current power and battery data.

        Returns:
            Dictionary with normalized power and battery data.
            For sites with multiple batteries, includes a 'batteries' list
            with individual battery data.
        """
        live_status = self.get_live_status()

        # Extract relevant data from the response
        # Note: Actual response structure may vary, adjust based on real API responses
        response_data = live_status.get("response", {})

        # Normalize to our schema
        data = {
            "timestamp": datetime.now(timezone.utc),
            "power_produced": None,
            "power_consumed": None,
            "power_exported": None,
            "power_imported": None,
            "battery_energy": None,
            "battery_power": None,
            "battery_soc": None,
            "batteries": [],  # List of individual battery data
            "raw_data": live_status,
        }

        # Extract power data (adjust field names based on actual API response)
        # These are placeholder field names - adjust based on actual Tesla API response structure
        if "solar_power" in response_data:
            data["power_produced"] = response_data["solar_power"]
        if "load_power" in response_data:
            data["power_consumed"] = response_data["load_power"]
        if "grid_power" in response_data:
            grid_power = response_data["grid_power"]
            if grid_power > 0:
                data["power_imported"] = grid_power
            else:
                data["power_exported"] = abs(grid_power)

        # Extract battery data - handle both single battery and battery array
        batteries = response_data.get("batteries", [])
        if batteries and isinstance(batteries, list):
            # Multiple batteries - extract individual battery data
            for idx, battery in enumerate(batteries):
                battery_data = {
                    "index": battery.get("index", idx),
                    "percentage_charged": battery.get("percentage_charged"),
                    "energy_left": battery.get("energy_left"),
                    "battery_power": battery.get("battery_power"),
                }
                data["batteries"].append(battery_data)
        else:
            # Single battery or legacy format - extract aggregate data
            if "battery_power" in response_data:
                battery_power = response_data["battery_power"]
                if battery_power > 0:
                    data["battery_power"] = battery_power  # Charging
                else:
                    data["battery_power"] = abs(battery_power)  # Discharging
            if "percentage_charged" in response_data:
                data["battery_soc"] = response_data["percentage_charged"]
            if "energy_left" in response_data:
                data["battery_energy"] = response_data["energy_left"]

        return data
