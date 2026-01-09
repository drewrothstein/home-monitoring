"""
Tempest Weather API client for local weather station data.

API documentation: https://apidocs.tempestwx.com/
"""

from datetime import date, datetime, timezone
from typing import Any, Dict, Optional

import requests


class TempestApiClient:
    """Client for Tempest Weather API."""

    BASE_URL = "https://swd.weatherflow.com/swd/rest"

    def __init__(self, token: str, station_id: int):
        """
        Initialize Tempest API client.

        Args:
            token: Personal access token for Tempest API
            station_id: Station ID number
        """
        self.token = token
        self.station_id = station_id
        # Tempest API uses token in Authorization header
        # Format may vary - adjust if authentication fails
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def get_station_stats(
        self,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> Dict[str, Any]:
        """
        Get statistics for a station.

        The stats_day array contains aggregated data where:
        - Index 15: Solar radiation (W/m²)
        - Index 16: Solar radiation (high) (W/m²)
        - Index 17: Solar radiation (low) (W/m²)

        Other indices include temperature, humidity, pressure, wind, etc.

        Args:
            start_date: Start date for statistics (optional)
            end_date: End date for statistics (optional)

        Returns:
            Dictionary containing station statistics
        """
        url = f"{self.BASE_URL}/stats/station/{self.station_id}"
        params = {}
        if start_date:
            params["start_date"] = start_date.strftime("%Y-%m-%d")
        if end_date:
            params["end_date"] = end_date.strftime("%Y-%m-%d")

        response = requests.get(url, headers=self.headers, params=params, timeout=30)
        response.raise_for_status()
        return response.json()

    def get_station_observations(self) -> Dict[str, Any]:
        """
        Get current/latest observations from the station.

        Returns:
            Dictionary containing current observation data
        """
        url = f"{self.BASE_URL}/observations/station/{self.station_id}"
        response = requests.get(url, headers=self.headers, timeout=30)
        response.raise_for_status()
        return response.json()

    def fetch_current_data(self) -> Dict[str, Any]:
        """
        Fetch current solar irradiance data from the weather station.

        Returns:
            Dictionary with normalized irradiance data
        """
        # Try to get current observations first (more recent data)
        try:
            observations = self.get_station_observations()
            obs_data = observations.get("obs", [])

            if obs_data:
                # Get the most recent observation
                latest_obs = obs_data[0] if isinstance(obs_data, list) else obs_data

                # Normalize to our schema
                # Tempest provides actual measured solar radiation (not clear/cloudy sky models)
                data = {
                    "timestamp": datetime.now(timezone.utc),
                    "ghi_clear_sky": None,  # Tempest measures actual conditions, not clear sky model
                    "ghi_cloudy_sky": None,
                    "dni_clear_sky": None,
                    "dni_cloudy_sky": None,
                    "dhi_clear_sky": None,
                    "dhi_cloudy_sky": None,
                    "raw_data": observations,
                }

                # Extract solar radiation from observation
                # Field name may vary - common names: solar_radiation, irradiance, solar_rad
                if "solar_radiation" in latest_obs:
                    data["ghi_cloudy_sky"] = latest_obs["solar_radiation"]  # Actual measured GHI
                elif "irradiance" in latest_obs:
                    data["ghi_cloudy_sky"] = latest_obs["irradiance"]
                elif "solar_rad" in latest_obs:
                    data["ghi_cloudy_sky"] = latest_obs["solar_rad"]

                # If we have data, return it
                if data["ghi_cloudy_sky"] is not None:
                    return data
        except Exception:
            # Fall back to stats if observations fail
            pass

        # Fall back to stats endpoint (daily aggregated data)
        today = date.today()
        stats = self.get_station_stats(start_date=today, end_date=today)

        # Normalize to our schema
        data = {
            "timestamp": datetime.now(timezone.utc),
            "ghi_clear_sky": None,
            "ghi_cloudy_sky": None,
            "dni_clear_sky": None,
            "dni_cloudy_sky": None,
            "dhi_clear_sky": None,
            "dhi_cloudy_sky": None,
            "raw_data": stats,
        }

        # Extract from stats_day array if present
        # stats_day array indices: 15 = solar radiation, 16 = high, 17 = low
        stats_days = stats.get("stats_day", [])
        if stats_days and len(stats_days) > 0:
            # Get today's stats (first entry if single day, or find matching day)
            today_stats = stats_days[0] if isinstance(stats_days[0], list) else stats_days

            if isinstance(today_stats, list) and len(today_stats) > 15:
                # Index 15 is average solar radiation
                solar_rad = today_stats[15]
                if solar_rad is not None:
                    data["ghi_cloudy_sky"] = solar_rad  # Actual measured GHI

            # Check for dictionary format as well
            if isinstance(today_stats, dict):
                if "solar_radiation" in today_stats:
                    data["ghi_cloudy_sky"] = today_stats["solar_radiation"]

        return data
