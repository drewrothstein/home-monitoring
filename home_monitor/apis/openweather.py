"""
OpenWeather Solar Irradiance API client.

API documentation: https://openweathermap.org/api/solar-irradiance
"""

from datetime import date, datetime
from datetime import timezone as dt_timezone
from typing import Any, Dict, Optional

import requests


class OpenWeatherApiClient:
    """Client for OpenWeather Solar Irradiance API."""

    BASE_URL = "https://api.openweathermap.org/energy/2.0/solar/interval_data"

    def __init__(self, api_key: str):
        """
        Initialize OpenWeather API client.

        Args:
            api_key: OpenWeather API key
        """
        self.api_key = api_key

    def get_solar_irradiance(
        self,
        latitude: float,
        longitude: float,
        target_date: date,
        interval: str = "1h",
        timezone: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get solar irradiance data for a specific location and date.

        Args:
            latitude: Latitude of the location
            longitude: Longitude of the location
            target_date: Date to get data for (YYYY-MM-DD format)
            interval: Time interval ('15m', '1h', or '1d')
            timezone: Timezone offset in ±XX:XX format (optional)

        Returns:
            Dictionary containing solar irradiance data
        """
        params = {
            "lat": latitude,
            "lon": longitude,
            "date": target_date.strftime("%Y-%m-%d"),
            "interval": interval,
            "appid": self.api_key,
        }

        if timezone:
            params["tz"] = timezone

        response = requests.get(self.BASE_URL, params=params, timeout=30)

        # Provide detailed error information for debugging
        if not response.ok:
            error_msg = f"{response.status_code} {response.reason}"
            try:
                error_data = response.json()
                if isinstance(error_data, dict):
                    error_code = error_data.get("code", response.status_code)
                    error_message = error_data.get("message", response.reason)
                    error_params = error_data.get("parameters", [])
                    error_msg = f"{error_code}: {error_message}"
                    if error_params:
                        error_msg += f" (parameters: {', '.join(error_params)})"
            except (ValueError, KeyError):
                # If response is not JSON, use the text content
                error_text = response.text[:200]  # Limit to first 200 chars
                if error_text:
                    error_msg += f" - {error_text}"

            # Add helpful guidance for 401 errors
            if response.status_code == 401:
                error_msg += (
                    "\n\nNote: 401 Unauthorized typically means your API key doesn't have access "
                    "to the Solar Irradiance API. This API requires a separate subscription. "
                    "Please verify:\n"
                    "1. You have subscribed to the Solar Irradiance API (not just set up billing)\n"
                    "2. The subscription is active on your OpenWeather account\n"
                    "3. The API key you're using has access to this product\n"
                    "See: https://openweathermap.org/api/solar-irradiance#how"
                )

            raise requests.exceptions.HTTPError(f"{error_msg} for url: {response.url}")

        return response.json()

    def fetch_current_data(
        self, latitude: float, longitude: float, timezone: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Fetch current day's solar irradiance data.

        Args:
            latitude: Latitude of the location
            longitude: Longitude of the location
            timezone: Timezone offset in ±XX:XX format (optional)

        Returns:
            Dictionary with normalized irradiance data
        """
        today = date.today()
        response = self.get_solar_irradiance(
            latitude, longitude, today, interval="1h", timezone=timezone
        )

        # Get the current hour's data (if available)
        intervals = response.get("intervals", [])
        current_hour_data = None
        current_hour = datetime.now().hour

        for interval in intervals:
            start_time = interval.get("start", "")
            hour = int(start_time.split(":")[0]) if ":" in start_time else None
            if hour == current_hour:
                current_hour_data = interval
                break

        # If no current hour found, use the most recent interval
        if not current_hour_data and intervals:
            current_hour_data = intervals[-1]

        # Normalize to our schema
        data = {
            "timestamp": datetime.now(dt_timezone.utc),
            "ghi_clear_sky": None,
            "ghi_cloudy_sky": None,
            "dni_clear_sky": None,
            "dni_cloudy_sky": None,
            "dhi_clear_sky": None,
            "dhi_cloudy_sky": None,
            "raw_data": response,
        }

        if current_hour_data:
            avg_irradiance = current_hour_data.get("avg_irradiance", {})

            clear_sky = avg_irradiance.get("clear_sky", {})
            cloudy_sky = avg_irradiance.get("cloudy_sky", {})

            data["ghi_clear_sky"] = clear_sky.get("ghi")
            data["ghi_cloudy_sky"] = cloudy_sky.get("ghi")
            data["dni_clear_sky"] = clear_sky.get("dni")
            data["dni_cloudy_sky"] = cloudy_sky.get("dni")
            data["dhi_clear_sky"] = clear_sky.get("dhi")
            data["dhi_cloudy_sky"] = cloudy_sky.get("dhi")

        return data
