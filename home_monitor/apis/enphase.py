"""
Enphase API client for inverter and solar panel data.

API documentation: https://developer-v4.enphase.com/docs.html
"""

import base64
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import requests


def get_access_token_from_password(
    client_id: str, client_secret: str, username: str, password: str
) -> Dict[str, Any]:
    """
    Get OAuth access token and refresh token using Enlighten username/password.

    This method works for Partner applications. For Developer applications (Watt/Kilowatt/Megawatt),
    you need to use the OAuth authorization flow instead.

    Args:
        client_id: Client ID from Enphase Developer Portal
        client_secret: Client Secret from Enphase Developer Portal
        username: Enlighten email address
        password: Enlighten password

    Returns:
        Dictionary containing access_token, refresh_token, expires_in, and other OAuth data

    Raises:
        requests.HTTPError: If the token request fails
    """
    url = "https://api.enphaseenergy.com/oauth/token"

    # Base64 encode client_id:client_secret for Basic Auth
    credentials = f"{client_id}:{client_secret}"
    encoded_credentials = base64.b64encode(credentials.encode()).decode()

    headers = {
        "Authorization": f"Basic {encoded_credentials}",
    }

    params = {
        "grant_type": "password",
        "username": username,
        "password": password,
    }

    response = requests.post(url, headers=headers, params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def refresh_access_token(client_id: str, client_secret: str, refresh_token: str) -> Dict[str, Any]:
    """
    Refresh OAuth access token using refresh token.

    Args:
        client_id: Client ID from Enphase Developer Portal
        client_secret: Client Secret from Enphase Developer Portal
        refresh_token: Refresh token from previous OAuth flow

    Returns:
        Dictionary containing new access_token, refresh_token, expires_in, and other OAuth data

    Raises:
        requests.HTTPError: If the token refresh fails
    """
    url = "https://api.enphaseenergy.com/oauth/token"

    # Base64 encode client_id:client_secret for Basic Auth
    credentials = f"{client_id}:{client_secret}"
    encoded_credentials = base64.b64encode(credentials.encode()).decode()

    headers = {
        "Authorization": f"Basic {encoded_credentials}",
    }

    # OAuth 2.0 requires POST body with form-encoded data, not query parameters
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }

    response = requests.post(url, headers=headers, data=data, timeout=30)
    response.raise_for_status()
    return response.json()


def get_authorization_url(
    client_id: str, redirect_uri: Optional[str] = None, state: Optional[str] = None
) -> str:
    """
    Generate the authorization URL for OAuth flow (Developer applications).

    The user must visit this URL and authorize the application. After authorization,
    they will be redirected to redirect_uri with an authorization code that can be
    exchanged for access_token and refresh_token.

    Args:
        client_id: Client ID from Enphase Developer Portal
        redirect_uri: Redirect URI (default: https://api.enphaseenergy.com/oauth/redirect_uri)
        state: Optional state parameter for additional security

    Returns:
        Authorization URL string
    """
    redirect_uri = redirect_uri or "https://api.enphaseenergy.com/oauth/redirect_uri"

    url = "https://api.enphaseenergy.com/oauth/authorize"
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
    }

    if state:
        params["state"] = state

    query_string = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{url}?{query_string}"


def exchange_authorization_code(
    client_id: str, client_secret: str, authorization_code: str, redirect_uri: Optional[str] = None
) -> Dict[str, Any]:
    """
    Exchange authorization code for access token and refresh token (Developer applications).

    Args:
        client_id: Client ID from Enphase Developer Portal
        client_secret: Client Secret from Enphase Developer Portal
        authorization_code: Authorization code from OAuth redirect
        redirect_uri: Redirect URI used in the authorization URL (must match exactly)

    Returns:
        Dictionary containing access_token, refresh_token, expires_in, and other OAuth data

    Raises:
        requests.HTTPError: If the token exchange fails
    """
    url = "https://api.enphaseenergy.com/oauth/token"

    # Base64 encode client_id:client_secret for Basic Auth
    credentials = f"{client_id}:{client_secret}"
    encoded_credentials = base64.b64encode(credentials.encode()).decode()

    headers = {
        "Authorization": f"Basic {encoded_credentials}",
    }

    # Default redirect_uri to match the one used in get_authorization_url
    redirect_uri = redirect_uri or "https://api.enphaseenergy.com/oauth/redirect_uri"

    # OAuth 2.0 requires POST body with form-encoded data, not query parameters
    data = {
        "grant_type": "authorization_code",
        "code": authorization_code,
        "redirect_uri": redirect_uri,
    }

    response = requests.post(url, headers=headers, data=data, timeout=30)
    if not response.ok:
        # Provide more detailed error information
        error_msg = f"{response.status_code} {response.reason}"
        try:
            error_detail = response.json()
            if "error_description" in error_detail:
                error_msg += f": {error_detail['error_description']}"
            elif "error" in error_detail:
                error_msg += f" - {error_detail['error']}"
            # Include full response for debugging
            error_msg += f"\nFull response: {error_detail}"
        except (ValueError, KeyError):
            error_msg += f"\nResponse body: {response.text}"
        # Create a custom exception with the detailed message
        raise requests.HTTPError(error_msg, response=response)
    return response.json()


class EnphaseApiClient:
    """Client for Enphase Energy API v4."""

    BASE_URL = "https://api.enphaseenergy.com/api/v4"

    def __init__(self, access_token: str, api_key: str, system_id: Optional[int] = None):
        """
        Initialize Enphase API client.

        Args:
            access_token: OAuth 2.0 access token
            api_key: API key for the application (passed as query parameter)
            system_id: System ID (optional, can be fetched from systems endpoint)
        """
        self.access_token = access_token
        self.api_key = api_key
        self.system_id = system_id
        self.headers = {
            "Authorization": f"Bearer {access_token}",
        }

    def _handle_api_response(self, response: requests.Response) -> None:
        """
        Handle API response and provide helpful error messages for common issues.

        Raises:
            requests.HTTPError: With detailed error message if response is not ok
        """
        if not response.ok:
            error_msg = f"{response.status_code} {response.reason}"

            # Try to get error details from response
            try:
                error_data = response.json()
                if isinstance(error_data, dict):
                    if "error_description" in error_data:
                        error_msg += f": {error_data['error_description']}"
                    elif "error" in error_data:
                        error_msg += f" - {error_data['error']}"
                    elif "message" in error_data:
                        error_msg += f": {error_data['message']}"
                    # Include full error data for 422 errors to help debug
                    if response.status_code == 422:
                        error_msg += f"\nFull error response: {error_data}"
            except (ValueError, KeyError):
                error_text = response.text[:200] if response.text else ""
                if error_text:
                    error_msg += f" - {error_text}"

            # Add helpful guidance for authentication errors (likely token expiration)
            if response.status_code in (401, 403):
                error_msg += (
                    "\n\n⚠️  TOKEN EXPIRATION DETECTED"
                    "\n\nYour access token has likely expired (tokens expire after ~24 hours / 86400 seconds)."
                    "\nTo fix this:\n"
                    "1. Use the refresh token to get a new access token:"
                    "\n   make enphase-refresh REFRESH_TOKEN=your_refresh_token"
                    "\n   OR manually run:"
                    "\n   python scripts/get_enphase_token.py --refresh-token YOUR_REFRESH_TOKEN"
                    "\n"
                    "\n2. Tokens are automatically stored in the database (no .env file update needed)"
                    "\n"
                    "\n3. If you don't have a refresh token, re-run the authorization flow:"
                    "\n   make enphase-authorize"
                    "\n   (then visit the URL, authorize, and exchange the new code)"
                )

            raise requests.HTTPError(error_msg, response=response)

    def get_systems(self) -> Dict[str, Any]:
        """
        Get list of systems for the authenticated user.

        Returns:
            Dictionary containing systems list
        """
        url = f"{self.BASE_URL}/systems"
        params = {"key": self.api_key}
        response = requests.get(url, headers=self.headers, params=params, timeout=30)
        self._handle_api_response(response)
        return response.json()

    def get_system_summary(self, system_id: Optional[int] = None) -> Dict[str, Any]:
        """
        Get system summary including current power production.

        Args:
            system_id: System ID (uses instance system_id if not provided)

        Returns:
            Dictionary containing system summary
        """
        system_id = system_id or self.system_id
        if not system_id:
            raise ValueError("system_id must be provided or set during initialization")

        url = f"{self.BASE_URL}/systems/{system_id}/summary"
        params = {"key": self.api_key}
        response = requests.get(url, headers=self.headers, params=params, timeout=30)
        self._handle_api_response(response)
        return response.json()

    def get_stats(
        self,
        system_id: Optional[int] = None,
        start_at: Optional[datetime] = None,
        end_at: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Get statistics for a system.

        Args:
            system_id: System ID (uses instance system_id if not provided)
            start_at: Start datetime for statistics
            end_at: End datetime for statistics

        Returns:
            Dictionary containing statistics
        """
        system_id = system_id or self.system_id
        if not system_id:
            raise ValueError("system_id must be provided or set during initialization")

        url = f"{self.BASE_URL}/systems/{system_id}/stats"
        params = {"key": self.api_key}
        if start_at:
            params["start_at"] = int(start_at.timestamp())
        if end_at:
            params["end_at"] = int(end_at.timestamp())

        response = requests.get(url, headers=self.headers, params=params, timeout=30)
        self._handle_api_response(response)
        return response.json()

    def get_energy_import_telemetry(
        self,
        system_id: Optional[int] = None,
        start_at: Optional[datetime] = None,
        end_at: Optional[datetime] = None,
        granularity: str = "15mins",
    ) -> Dict[str, Any]:
        """
        Get energy import telemetry data for a system.

        Args:
            system_id: System ID (uses instance system_id if not provided)
            start_at: Start datetime for telemetry (must be within 2 years, duration cannot exceed 1 week)
            end_at: End datetime for telemetry (optional, defaults to now if not provided)
            granularity: Data granularity (default: "15mins" for 15-minute intervals; valid values: "5mins", "15mins", "day", "week")

        Returns:
            Dictionary containing energy import telemetry data

        Raises:
            ValueError: If system_id is not provided
        """
        system_id = system_id or self.system_id
        if not system_id:
            raise ValueError("system_id must be provided or set during initialization")

        if not start_at:
            raise ValueError("start_at is required for energy import telemetry")

        url = f"{self.BASE_URL}/systems/{system_id}/energy_import_telemetry"
        params = {
            "key": self.api_key,
            "start_at": int(start_at.timestamp()),
            "granularity": granularity,
        }

        # Add end_at if provided, otherwise API will default to current time
        if end_at:
            params["end_at"] = int(end_at.timestamp())

        response = requests.get(url, headers=self.headers, params=params, timeout=30)
        self._handle_api_response(response)
        return response.json()

    def get_energy_export_telemetry(
        self,
        system_id: Optional[int] = None,
        start_at: Optional[datetime] = None,
        end_at: Optional[datetime] = None,
        granularity: str = "15mins",
    ) -> Dict[str, Any]:
        """
        Get energy export telemetry data for a system.

        Args:
            system_id: System ID (uses instance system_id if not provided)
            start_at: Start datetime for telemetry (must be within 2 years, duration cannot exceed 1 week)
            end_at: End datetime for telemetry (optional, defaults to now if not provided)
            granularity: Data granularity (default: "15mins" for 15-minute intervals; valid values: "5mins", "15mins", "day", "week")

        Returns:
            Dictionary containing energy export telemetry data

        Raises:
            ValueError: If system_id is not provided
        """
        system_id = system_id or self.system_id
        if not system_id:
            raise ValueError("system_id must be provided or set during initialization")

        if not start_at:
            raise ValueError("start_at is required for energy export telemetry")

        url = f"{self.BASE_URL}/systems/{system_id}/energy_export_telemetry"
        params = {
            "key": self.api_key,
            "start_at": int(start_at.timestamp()),
            "granularity": granularity,
        }

        # Add end_at if provided, otherwise API will default to current time
        if end_at:
            params["end_at"] = int(end_at.timestamp())

        response = requests.get(url, headers=self.headers, params=params, timeout=30)
        self._handle_api_response(response)
        return response.json()

    def fetch_current_data(self, include_energy_telemetry: bool = False) -> Dict[str, Any]:
        """
        Fetch current power production data.

        Args:
            include_energy_telemetry: If True, also fetch recent energy import/export telemetry

        Returns:
            Dictionary with normalized power data
        """
        import logging
        from datetime import timedelta

        logger = logging.getLogger(__name__)

        summary = self.get_system_summary()

        # Normalize to our schema
        data = {
            "timestamp": datetime.now(timezone.utc),
            "power_produced": None,
            "power_consumed": None,
            "power_exported": None,
            "power_imported": None,
            "energy_imported_kwh": None,
            "energy_exported_kwh": None,
            "raw_data": summary,
        }

        # Extract power data from summary response
        # Enphase API v4 summary endpoint structure:
        # - production: array of objects with wNow (current power in watts)
        # - consumption: array of objects with wNow (current consumption in watts)
        # - grid_status: "Active" or "Inactive"

        # Check for production data in nested structure (most common)
        production = summary.get("production", [])
        if production:
            if isinstance(production, list):
                # Sum up all production values from array
                total_production = sum(
                    p.get("wNow", 0) if isinstance(p, dict) else 0 for p in production
                )
                if total_production:
                    data["power_produced"] = total_production
            elif isinstance(production, dict):
                # Handle case where production is a single object
                w_now = production.get("wNow")
                if w_now is not None:
                    data["power_produced"] = w_now

        # Check for consumption data
        consumption = summary.get("consumption", [])
        if consumption:
            if isinstance(consumption, list):
                total_consumption = sum(
                    c.get("wNow", 0) if isinstance(c, dict) else 0 for c in consumption
                )
                if total_consumption:
                    data["power_consumed"] = total_consumption
            elif isinstance(consumption, dict):
                w_now = consumption.get("wNow")
                if w_now is not None:
                    data["power_consumed"] = w_now

        # Fallback: Check for direct fields (less common in v4 API)
        if data["power_produced"] is None:
            if "current_power" in summary:
                data["power_produced"] = summary["current_power"]
            elif "power_production" in summary:
                data["power_produced"] = summary["power_production"]

        # Fetch energy import/export telemetry if requested
        if include_energy_telemetry:
            try:
                # Fetch last 24 hours of energy telemetry
                # API allows up to 1 week, but we'll fetch 24 hours to get recent data
                end_at = datetime.now(timezone.utc)
                start_at = end_at - timedelta(hours=24)

                # Fetch energy import telemetry
                try:
                    import_telemetry = self.get_energy_import_telemetry(
                        start_at=start_at, end_at=end_at
                    )
                    if "raw_data" not in data:
                        data["raw_data"] = {}
                    if not isinstance(data["raw_data"], dict):
                        data["raw_data"] = {"summary": data["raw_data"]}
                    data["raw_data"]["energy_import_telemetry"] = import_telemetry

                    # Extract the most recent energy import value if available
                    # Handle different response structures
                    intervals = []
                    if isinstance(import_telemetry, dict):
                        # Response is a dict with intervals key
                        intervals = import_telemetry.get("intervals", [])
                        # Also check for "items" which might contain the data
                        if not intervals:
                            items = import_telemetry.get("items", [])
                            if items and isinstance(items, list) and len(items) > 0:
                                # items might be a list of devices, each with intervals
                                for item in items:
                                    if isinstance(item, dict):
                                        item_intervals = item.get("intervals", [])
                                        if item_intervals:
                                            intervals.extend(item_intervals)
                    elif isinstance(import_telemetry, list):
                        # Response might be a list of intervals directly
                        intervals = import_telemetry

                    # Flatten nested interval structures (intervals can be list of lists)
                    flattened_intervals = []
                    for interval in intervals:
                        if isinstance(interval, list):
                            # Nested list structure: [[{...}]]
                            flattened_intervals.extend(interval)
                        elif isinstance(interval, dict):
                            # Direct dict structure: [{...}]
                            flattened_intervals.append(interval)

                    if flattened_intervals and len(flattened_intervals) > 0:
                        # Get the last interval (most recent)
                        latest_interval = flattened_intervals[-1]
                        if isinstance(latest_interval, dict):
                            # Energy is typically in Wh (watt-hours), convert to kWh
                            # Try different possible field names from the API
                            energy_import_wh = (
                                latest_interval.get("wh_imported")
                                or latest_interval.get("wh_del")
                                or latest_interval.get("whDel")
                                or latest_interval.get("energy")
                                or 0
                            )
                            if energy_import_wh:
                                # Convert Wh to kWh
                                data["energy_imported_kwh"] = energy_import_wh / 1000.0
                except Exception as e:
                    # Log more details for 422 errors to help debug
                    error_str = str(e)
                    if "422" in error_str:
                        logger.warning(
                            f"Failed to fetch energy import telemetry (422): {e}. "
                            f"This may indicate the system doesn't have import/export meters configured, "
                            f"or the API parameters need adjustment. start_at={start_at}, end_at={end_at}"
                        )
                    else:
                        logger.warning(f"Failed to fetch energy import telemetry: {e}")

                # Fetch energy export telemetry
                try:
                    export_telemetry = self.get_energy_export_telemetry(
                        start_at=start_at, end_at=end_at
                    )
                    if "raw_data" not in data:
                        data["raw_data"] = {}
                    if not isinstance(data["raw_data"], dict):
                        data["raw_data"] = {"summary": data["raw_data"]}
                    data["raw_data"]["energy_export_telemetry"] = export_telemetry

                    # Extract the most recent energy export value if available
                    # Handle different response structures
                    intervals = []
                    if isinstance(export_telemetry, dict):
                        # Response is a dict with intervals key
                        intervals = export_telemetry.get("intervals", [])
                        # Also check for "items" which might contain the data
                        if not intervals:
                            items = export_telemetry.get("items", [])
                            if items and isinstance(items, list) and len(items) > 0:
                                # items might be a list of devices, each with intervals
                                for item in items:
                                    if isinstance(item, dict):
                                        item_intervals = item.get("intervals", [])
                                        if item_intervals:
                                            intervals.extend(item_intervals)
                    elif isinstance(export_telemetry, list):
                        # Response might be a list of intervals directly
                        intervals = export_telemetry

                    # Flatten nested interval structures (intervals can be list of lists)
                    flattened_intervals = []
                    for interval in intervals:
                        if isinstance(interval, list):
                            # Nested list structure: [[{...}]]
                            flattened_intervals.extend(interval)
                        elif isinstance(interval, dict):
                            # Direct dict structure: [{...}]
                            flattened_intervals.append(interval)

                    if flattened_intervals and len(flattened_intervals) > 0:
                        # Get the last interval (most recent)
                        latest_interval = flattened_intervals[-1]
                        if isinstance(latest_interval, dict):
                            # Energy is typically in Wh (watt-hours), convert to kWh
                            # Try different possible field names from the API
                            energy_export_wh = (
                                latest_interval.get("wh_exported")
                                or latest_interval.get("wh_del")
                                or latest_interval.get("whDel")
                                or latest_interval.get("energy")
                                or 0
                            )
                            if energy_export_wh:
                                # Convert Wh to kWh
                                data["energy_exported_kwh"] = energy_export_wh / 1000.0
                except Exception as e:
                    # Log more details for 422 errors to help debug
                    error_str = str(e)
                    if "422" in error_str:
                        logger.warning(
                            f"Failed to fetch energy export telemetry (422): {e}. "
                            f"This may indicate the system doesn't have import/export meters configured, "
                            f"or the API parameters need adjustment. start_at={start_at}, end_at={end_at}"
                        )
                    else:
                        logger.warning(f"Failed to fetch energy export telemetry: {e}")

            except Exception as e:
                logger.warning(f"Failed to fetch energy telemetry: {e}")

        # Log if we couldn't extract power data (for debugging)
        if data["power_produced"] is None and data["power_consumed"] is None:
            logger.warning(
                f"Could not extract power data from Enphase API response. "
                f"Summary keys: {list(summary.keys())}. "
                f"Production type: {type(summary.get('production'))}, "
                f"Consumption type: {type(summary.get('consumption'))}"
            )

        return data
