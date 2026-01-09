"""
Flume API client for water usage data.

API documentation: https://flumetech.readme.io/docs/authentication
"""

import base64
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import requests

logger = logging.getLogger(__name__)


def _decode_jwt_payload(token: str) -> Dict[str, Any]:
    """
    Decode the payload from a JWT token without verification.

    The payload contains user_id, type, iat, and exp fields.

    Args:
        token: JWT access token

    Returns:
        Decoded payload dictionary
    """
    try:
        # JWT is three parts separated by dots: header.payload.signature
        parts = token.split(".")
        if len(parts) != 3:
            raise ValueError("Invalid JWT format")

        # Base64 decode the payload (middle part)
        # Add padding if needed
        payload_b64 = parts[1]
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding

        payload_json = base64.urlsafe_b64decode(payload_b64)
        return json.loads(payload_json)
    except Exception as e:
        logger.warning(f"Failed to decode JWT payload: {e}")
        return {}


def get_tokens(client_id: str, client_secret: str, username: str, password: str) -> Dict[str, Any]:
    """
    Get OAuth access token and refresh token using Flume username/password.

    This uses the OAuth 2 Resource Owner Password Credentials Grant.

    Args:
        client_id: Client ID from Flume API Access settings
        client_secret: Client Secret from Flume API Access settings
        username: Flume account email address
        password: Flume account password

    Returns:
        Dictionary containing access_token, refresh_token, and token data

    Raises:
        requests.HTTPError: If the token request fails
    """
    url = "https://api.flumewater.com/oauth/token"

    headers = {
        "Content-Type": "application/json",
    }

    payload = {
        "grant_type": "password",
        "client_id": client_id,
        "client_secret": client_secret,
        "username": username,
        "password": password,
    }

    response = requests.post(url, headers=headers, json=payload, timeout=30)
    response.raise_for_status()

    result = response.json()

    # Extract token data from response envelope
    if result.get("success") and result.get("data"):
        token_data = result["data"][0] if isinstance(result["data"], list) else result["data"]
        return token_data

    return result


def refresh_access_token(client_id: str, client_secret: str, refresh_token: str) -> Dict[str, Any]:
    """
    Refresh OAuth access token using refresh token.

    Args:
        client_id: Client ID from Flume API Access settings
        client_secret: Client Secret from Flume API Access settings
        refresh_token: Refresh token from previous OAuth flow

    Returns:
        Dictionary containing new access_token, refresh_token, and token data

    Raises:
        requests.HTTPError: If the token refresh fails
    """
    url = "https://api.flumewater.com/oauth/token"

    headers = {
        "Content-Type": "application/json",
    }

    payload = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
    }

    response = requests.post(url, headers=headers, json=payload, timeout=30)
    response.raise_for_status()

    result = response.json()

    # Extract token data from response envelope
    if result.get("success") and result.get("data"):
        token_data = result["data"][0] if isinstance(result["data"], list) else result["data"]
        return token_data

    return result


class FlumeApiClient:
    """Client for Flume Water API."""

    BASE_URL = "https://api.flumewater.com"

    def __init__(
        self,
        access_token: str,
        user_id: Optional[str] = None,
        device_id: Optional[str] = None,
        tz: Optional[str] = None,
    ):
        """
        Initialize Flume API client.

        Args:
            access_token: OAuth 2.0 access token (JWT)
            user_id: User ID (optional, can be extracted from JWT if not provided)
            device_id: Device ID (optional, can be fetched from devices endpoint)
            tz: Timezone name (e.g., 'America/New_York') for calculating local midnight.
                If not provided, defaults to UTC for "today" calculations.
        """
        self.access_token = access_token
        self.device_id = device_id
        self.headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        # Store timezone for local time calculations
        if tz:
            try:
                self.tz = ZoneInfo(tz)
            except Exception as e:
                logger.warning(f"Invalid timezone '{tz}': {e}. Falling back to UTC.")
                self.tz = timezone.utc
        else:
            self.tz = timezone.utc

        # Extract user_id from JWT if not provided
        if user_id:
            self.user_id = user_id
        else:
            jwt_payload = _decode_jwt_payload(access_token)
            self.user_id = str(jwt_payload.get("user_id", ""))
            if not self.user_id:
                raise ValueError(
                    "Could not extract user_id from access token. "
                    "Please provide user_id explicitly."
                )

    def _handle_api_response(self, response: requests.Response) -> None:
        """
        Handle API response and provide helpful error messages for common issues.

        Raises:
            requests.HTTPError: With detailed error message if response is not ok
        """
        if not response.ok:
            error_msg = f"{response.status_code} {response.reason}"

            try:
                error_data = response.json()
                if isinstance(error_data, dict):
                    if "message" in error_data:
                        error_msg += f": {error_data['message']}"
                    elif "detailed" in error_data and error_data["detailed"]:
                        error_msg += f": {error_data['detailed']}"
            except (ValueError, KeyError):
                error_text = response.text[:200] if response.text else ""
                if error_text:
                    error_msg += f" - {error_text}"

            # Add helpful guidance for authentication errors
            if response.status_code in (401, 403):
                error_msg += (
                    "\n\n⚠️  TOKEN EXPIRATION DETECTED"
                    "\n\nYour access token has likely expired."
                    "\nTo fix this:\n"
                    "1. Use the refresh token to get a new access token:"
                    "\n   make flume-refresh REFRESH_TOKEN=your_refresh_token"
                    "\n   OR manually run:"
                    "\n   python scripts/get_flume_token.py --refresh-token YOUR_REFRESH_TOKEN"
                    "\n"
                    "\n2. Tokens are automatically stored in the database"
                    "\n"
                    "\n3. If you don't have a refresh token, re-run the token setup:"
                    "\n   make flume-token"
                )

            raise requests.HTTPError(error_msg, response=response)

    def get_user(self) -> Dict[str, Any]:
        """
        Fetch user information.

        Returns:
            Dictionary containing user data
        """
        url = f"{self.BASE_URL}/users/{self.user_id}"
        response = requests.get(url, headers=self.headers, timeout=30)
        self._handle_api_response(response)
        return response.json()

    def get_devices(self) -> List[Dict[str, Any]]:
        """
        Get list of devices for the authenticated user.

        Returns:
            List of device dictionaries
        """
        url = f"{self.BASE_URL}/users/{self.user_id}/devices"
        response = requests.get(url, headers=self.headers, timeout=30)
        self._handle_api_response(response)

        result = response.json()
        if result.get("success") and result.get("data"):
            return result["data"]
        return []

    def get_device(self, device_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Get a specific device.

        Args:
            device_id: Device ID (uses instance device_id if not provided)

        Returns:
            Dictionary containing device data
        """
        device_id = device_id or self.device_id
        if not device_id:
            raise ValueError("device_id must be provided or set during initialization")

        url = f"{self.BASE_URL}/users/{self.user_id}/devices/{device_id}"
        response = requests.get(url, headers=self.headers, timeout=30)
        self._handle_api_response(response)

        result = response.json()
        if result.get("success") and result.get("data"):
            return result["data"][0] if isinstance(result["data"], list) else result["data"]
        return result

    def get_current_flow_rate(self, device_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Get current flow rate for a device.

        Args:
            device_id: Device ID (uses instance device_id if not provided)

        Returns:
            Dictionary containing current flow rate data
        """
        device_id = device_id or self.device_id
        if not device_id:
            raise ValueError("device_id must be provided or set during initialization")

        url = f"{self.BASE_URL}/users/{self.user_id}/devices/{device_id}/current_flow_rate"
        response = requests.get(url, headers=self.headers, timeout=30)
        self._handle_api_response(response)

        result = response.json()
        if result.get("success") and result.get("data"):
            return result["data"][0] if isinstance(result["data"], list) else result["data"]
        return result

    def query_water_usage(
        self,
        device_id: Optional[str] = None,
        since_datetime: Optional[datetime] = None,
        until_datetime: Optional[datetime] = None,
        bucket: str = "HR",
        operation: Optional[str] = None,
        units: str = "GALLONS",
        group_multiplier: int = 1,
        sort_direction: str = "ASC",
    ) -> List[Dict[str, Any]]:
        """
        Query water usage data for a device.

        Args:
            device_id: Device ID (uses instance device_id if not provided)
            since_datetime: Start datetime for query (required)
            until_datetime: End datetime for query (default: now)
            bucket: Time grouping - MIN, HR, DAY, MON, YR (default: HR)
            operation: Optional aggregation - SUM, AVG, MIN, MAX, CNT
            units: Unit of measurement - GALLONS, LITERS, CUBIC_FEET, CUBIC_METERS
            group_multiplier: Multiplier for bucket grouping (default: 1)
            sort_direction: ASC or DESC (default: ASC)

        Returns:
            List of water usage data points with datetime and value fields
        """
        device_id = device_id or self.device_id
        if not device_id:
            raise ValueError("device_id must be provided or set during initialization")

        if not since_datetime:
            # Default to last 24 hours
            since_datetime = datetime.now(timezone.utc) - timedelta(hours=24)

        url = f"{self.BASE_URL}/users/{self.user_id}/devices/{device_id}/query"

        # Format datetimes without timezone for Flume API
        since_str = since_datetime.strftime("%Y-%m-%d %H:%M:%S")
        until_str = (
            until_datetime.strftime("%Y-%m-%d %H:%M:%S")
            if until_datetime
            else datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        )

        query = {
            "request_id": "current_query",
            "since_datetime": since_str,
            "until_datetime": until_str,
            "bucket": bucket,
            "units": units,
            "group_multiplier": group_multiplier,
            "sort_direction": sort_direction,
        }

        if operation:
            query["operation"] = operation

        payload = {"queries": [query]}

        response = requests.post(url, headers=self.headers, json=payload, timeout=30)
        self._handle_api_response(response)

        result = response.json()
        if result.get("success") and result.get("data"):
            # Extract the data from the response envelope
            data = result["data"]
            if isinstance(data, list) and len(data) > 0:
                query_result = data[0]
                if isinstance(query_result, dict) and "current_query" in query_result:
                    return query_result["current_query"]
        return []

    def fetch_current_data(self) -> Dict[str, Any]:
        """
        Fetch current water usage data.

        Returns:
            Dictionary with normalized water data including:
            - timestamp: Current UTC timestamp
            - flow_rate_gpm: Current flow rate in gallons per minute (if available)
            - usage_today_gallons: Water used today in gallons
            - usage_hour_gallons: Water used in the last hour in gallons
            - raw_data: Raw API response data
        """
        data = {
            "timestamp": datetime.now(timezone.utc),
            "flow_rate_gpm": None,
            "usage_today_gallons": None,
            "usage_hour_gallons": None,
            "raw_data": {},
        }

        # Try to get current flow rate (not always available on all devices/accounts)
        try:
            flow_data = self.get_current_flow_rate()
            data["raw_data"]["current_flow_rate"] = flow_data

            # Extract flow rate (usually in gallons per minute)
            if isinstance(flow_data, dict):
                flow_rate = flow_data.get("value") or flow_data.get("flow_rate")
                if flow_rate is not None:
                    data["flow_rate_gpm"] = float(flow_rate)
        except Exception as e:
            # This endpoint is not available on all Flume accounts/devices
            logger.debug(f"Current flow rate not available: {e}")

        # Get today's usage
        try:
            # Calculate "start of day" in the configured local timezone
            # This ensures daily usage resets at local midnight, not UTC midnight
            now_local = datetime.now(self.tz)
            start_of_day_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)

            daily_usage = self.query_water_usage(
                since_datetime=start_of_day_local,
                until_datetime=now_local,
                bucket="DAY",
                operation="SUM",
                units="GALLONS",
            )
            data["raw_data"]["daily_usage"] = daily_usage

            if daily_usage and len(daily_usage) > 0:
                # Get the total value
                if isinstance(daily_usage[0], dict):
                    value = daily_usage[0].get("value")
                    if value is not None:
                        data["usage_today_gallons"] = float(value)
        except Exception as e:
            logger.warning(f"Failed to fetch daily usage: {e}")

        # Get last hour's usage
        try:
            now_local = datetime.now(self.tz)
            one_hour_ago = now_local - timedelta(hours=1)

            hourly_usage = self.query_water_usage(
                since_datetime=one_hour_ago,
                until_datetime=now_local,
                bucket="HR",
                operation="SUM",
                units="GALLONS",
            )
            data["raw_data"]["hourly_usage"] = hourly_usage

            if hourly_usage and len(hourly_usage) > 0:
                if isinstance(hourly_usage[0], dict):
                    value = hourly_usage[0].get("value")
                    if value is not None:
                        data["usage_hour_gallons"] = float(value)
        except Exception as e:
            logger.warning(f"Failed to fetch hourly usage: {e}")

        return data
