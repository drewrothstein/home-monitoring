"""
Enphase IQ Gateway Local API client.

This module provides access to local Enphase IQ Gateway APIs, which are accessed
directly on the local network via HTTPS. These APIs provide richer data than the
cloud APIs, including consumption data, detailed meter readings, and grid data.

Token generation: https://enphase.com/download/iq-gateway-local-apis-or-ui-access-using-token
- Owner tokens are valid for 1 year
- Installer tokens are valid for 12 hours
- Tokens can be refreshed programmatically via Enlighten

API Documentation (unofficial):
https://github.com/Matthew1471/Enphase-API/blob/main/Documentation/IQ%20Gateway%20API/README.adoc
"""

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

import requests
import urllib3

logger = logging.getLogger(__name__)

# Disable SSL warnings for local gateway connections (self-signed certs)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Token refresh threshold - refresh if token expires within this many days
TOKEN_REFRESH_THRESHOLD_DAYS = 30


class EnlightenSession:
    """
    Manages authenticated session with Enlighten for fetching gateway tokens.

    Enlighten uses session-based authentication (not OAuth). This class handles
    login and maintains the session cookies for subsequent requests.
    """

    LOGIN_URL = "https://enlighten.enphaseenergy.com/login/login.json"
    TOKEN_URL = "https://enlighten.enphaseenergy.com/entrez-auth-token"

    def __init__(self, username: Optional[str] = None, password: Optional[str] = None):
        """
        Initialize Enlighten session.

        Args:
            username: Enlighten email (defaults to ENPHASE_ENLIGHTEN_USERNAME env var)
            password: Enlighten password (defaults to ENPHASE_ENLIGHTEN_PASSWORD env var)
        """
        self.username = username or os.getenv("ENPHASE_ENLIGHTEN_USERNAME")
        self.password = password or os.getenv("ENPHASE_ENLIGHTEN_PASSWORD")
        self.session = requests.Session()
        self._authenticated = False

    def login(self) -> bool:
        """
        Authenticate with Enlighten.

        Returns:
            True if login successful, False otherwise
        """
        if not self.username or not self.password:
            logger.warning(
                "Enlighten credentials not configured. Set ENPHASE_ENLIGHTEN_USERNAME "
                "and ENPHASE_ENLIGHTEN_PASSWORD environment variables."
            )
            return False

        try:
            # First, get the login page to get any CSRF tokens
            login_page = self.session.get(
                "https://enlighten.enphaseenergy.com/login",
                timeout=30,
            )
            login_page.raise_for_status()

            # Extract CSRF token if present (Rails authenticity_token)
            csrf_token = None
            match = re.search(r'name="authenticity_token"[^>]*value="([^"]+)"', login_page.text)
            if match:
                csrf_token = match.group(1)

            # Build login payload
            payload = {
                "user[email]": self.username,
                "user[password]": self.password,
            }
            if csrf_token:
                payload["authenticity_token"] = csrf_token

            # Perform login
            response = self.session.post(
                self.LOGIN_URL,
                data=payload,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
                timeout=30,
            )

            # Check if login was successful
            if response.status_code == 200:
                data = response.json()
                if data.get("success") or data.get("session_id"):
                    self._authenticated = True
                    logger.info("Successfully authenticated with Enlighten")
                    return True
                else:
                    logger.error(f"Enlighten login failed: {data.get('message', 'Unknown error')}")
                    return False
            else:
                logger.error(f"Enlighten login failed with status {response.status_code}")
                return False

        except Exception as e:
            logger.error(f"Error authenticating with Enlighten: {e}")
            return False

    def get_gateway_token(self, gateway_serial: str) -> Optional[Dict[str, Any]]:
        """
        Fetch a gateway token from Enlighten.

        Args:
            gateway_serial: Serial number of the IQ Gateway

        Returns:
            Dictionary with 'token' and 'expires_at' (datetime), or None on failure
        """
        if not self._authenticated:
            if not self.login():
                return None

        try:
            response = self.session.get(
                f"{self.TOKEN_URL}?serial_num={gateway_serial}",
                headers={"Accept": "application/json"},
                timeout=30,
            )
            response.raise_for_status()

            data = response.json()
            token = data.get("token")
            expires_at_unix = data.get("expires_at")

            if not token:
                logger.error(f"No token in Enlighten response for gateway {gateway_serial}")
                return None

            # Convert Unix timestamp to datetime
            expires_at = None
            if expires_at_unix:
                expires_at = datetime.fromtimestamp(expires_at_unix, tz=timezone.utc)

            return {
                "token": token,
                "expires_at": expires_at,
            }

        except requests.HTTPError as e:
            if e.response.status_code == 401:
                # Session expired, try re-authenticating
                self._authenticated = False
                logger.info("Enlighten session expired, re-authenticating...")
                if self.login():
                    return self.get_gateway_token(gateway_serial)
            logger.error(f"Error fetching gateway token: {e}")
            return None
        except Exception as e:
            logger.error(f"Error fetching gateway token: {e}")
            return None


def get_enlighten_credentials() -> Tuple[Optional[str], Optional[str]]:
    """
    Get Enlighten credentials from environment variables.

    Returns:
        Tuple of (username, password)
    """
    username = os.getenv("ENPHASE_ENLIGHTEN_USERNAME")
    password = os.getenv("ENPHASE_ENLIGHTEN_PASSWORD")
    return (username, password)


def refresh_gateway_token(gateway_serial: str, gateway_host: str) -> Optional[Dict[str, Any]]:
    """
    Refresh a gateway token using Enlighten credentials.

    This function fetches a new token from Enlighten and stores it in the database.

    Args:
        gateway_serial: Serial number of the gateway
        gateway_host: IP address/hostname of the gateway

    Returns:
        Dictionary with new token info, or None on failure
    """
    session = EnlightenSession()
    token_data = session.get_gateway_token(gateway_serial)

    if not token_data:
        return None

    # Store in database
    try:
        from home_monitor.database import upsert_enphase_gateway_token

        upsert_enphase_gateway_token(
            gateway_serial=gateway_serial,
            gateway_host=gateway_host,
            token=token_data["token"],
            token_expires_at=token_data["expires_at"],
        )
        logger.info(f"Refreshed and stored token for gateway {gateway_serial}")
    except Exception as e:
        logger.error(f"Failed to store refreshed token: {e}")

    return token_data


def check_and_refresh_token(
    gateway_serial: str,
    gateway_host: str,
    current_token: str,
    expires_at: Optional[datetime],
    threshold_days: int = TOKEN_REFRESH_THRESHOLD_DAYS,
) -> Tuple[str, Optional[datetime]]:
    """
    Check if a token needs refresh and refresh it if necessary.

    Args:
        gateway_serial: Serial number of the gateway
        gateway_host: IP address/hostname of the gateway
        current_token: Current gateway token
        expires_at: Current token expiration time
        threshold_days: Refresh if token expires within this many days

    Returns:
        Tuple of (token, expires_at) - may be new or existing token
    """
    # If no expiration set, assume token is valid
    if expires_at is None:
        return current_token, expires_at

    # Check if token expires within threshold
    now = datetime.now(timezone.utc)
    threshold = now + timedelta(days=threshold_days)

    if expires_at > threshold:
        # Token is still valid
        return current_token, expires_at

    # Token needs refresh
    logger.info(f"Gateway {gateway_serial} token expires {expires_at.isoformat()}, refreshing...")

    token_data = refresh_gateway_token(gateway_serial, gateway_host)

    if token_data:
        return token_data["token"], token_data["expires_at"]
    else:
        logger.warning(f"Failed to refresh token for {gateway_serial}, using existing token")
        return current_token, expires_at


class EnphaseLocalClient:
    """
    Client for Enphase IQ Gateway Local APIs.

    Connects directly to the gateway on the local network via HTTPS.
    The gateway uses a self-signed certificate, so SSL verification is disabled.
    """

    def __init__(
        self,
        gateway_host: str,
        token: str,
        gateway_serial: Optional[str] = None,
        timeout: int = 10,
    ):
        """
        Initialize the Enphase Local API client.

        Args:
            gateway_host: IP address or hostname of the gateway (e.g., "192.168.1.100")
            token: Gateway access token (valid for 1 year for system owners)
            gateway_serial: Serial number of the gateway (used for token refresh)
            timeout: Request timeout in seconds
        """
        self.gateway_host = gateway_host
        self.token = token
        self.gateway_serial = gateway_serial
        self.timeout = timeout
        self.base_url = f"https://{gateway_host}"
        self.session = requests.Session()
        self.session.verify = False  # Gateway uses self-signed cert
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            }
        )

    def _request(self, method: str, endpoint: str, **kwargs) -> Dict[str, Any]:
        """
        Make a request to the gateway API.

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint (e.g., "/api/v1/production")
            **kwargs: Additional arguments passed to requests

        Returns:
            JSON response as dictionary

        Raises:
            requests.HTTPError: If the request fails
        """
        url = f"{self.base_url}{endpoint}"
        kwargs.setdefault("timeout", self.timeout)

        try:
            response = self.session.request(method, url, **kwargs)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.SSLError as e:
            logger.error(f"SSL error connecting to gateway {self.gateway_host}: {e}")
            raise
        except requests.exceptions.ConnectionError as e:
            logger.error(f"Connection error to gateway {self.gateway_host}: {e}")
            raise
        except requests.exceptions.Timeout as e:
            logger.error(f"Timeout connecting to gateway {self.gateway_host}: {e}")
            raise

    def get_production(self) -> Dict[str, Any]:
        """
        Get current production data.

        Returns production info including watt-hours produced today/lifetime,
        current power in watts, etc.

        Endpoint: GET /api/v1/production
        """
        return self._request("GET", "/api/v1/production")

    def get_production_json(self) -> Dict[str, Any]:
        """
        Get detailed production and consumption data.

        This endpoint often returns more complete data than /api/v1/production,
        including consumption data with whToday (energy consumed today).

        Returns format like:
        {
          "production": [
            {"type": "inverters", "wNow": 1823, "whToday": 13114, ...},
            {"type": "eim", "wNow": 1800, "whToday": 13000, ...}
          ],
          "consumption": [
            {"measurementType": "total-consumption", "wNow": 817, "whToday": 5432, ...},
            {"measurementType": "net-consumption", "wNow": 100, "whToday": 1000, ...}
          ],
          "storage": [...]  // if batteries present
        }

        Endpoint: GET /production.json
        """
        return self._request("GET", "/production.json")

    def get_production_inverters(self) -> list:
        """
        Get per-inverter production data.

        Returns list of inverters with their serial numbers and last report time.

        Endpoint: GET /api/v1/production/inverters
        """
        return self._request("GET", "/api/v1/production/inverters")

    def get_info(self) -> Dict[str, Any]:
        """
        Get gateway system information.

        Returns device serial, software version, timezone, etc.

        Endpoint: GET /info
        """
        return self._request("GET", "/info")

    def get_home(self) -> Dict[str, Any]:
        """
        Get comprehensive system status including all devices.

        Endpoint: GET /home
        """
        return self._request("GET", "/home")

    def get_inventory(self) -> list:
        """
        Get list of all provisioned devices.

        Returns list of devices with their serial numbers, part numbers, etc.

        Endpoint: GET /inventory
        """
        return self._request("GET", "/inventory")

    def get_meters(self) -> list:
        """
        Get meter configuration and status.

        Returns list of meters (production, net-consumption, total-consumption).

        Endpoint: GET /ivp/meters
        """
        return self._request("GET", "/ivp/meters")

    def get_meter_readings(self) -> list:
        """
        Get detailed meter readings.

        Returns comprehensive readings including voltage, current, power,
        energy, power factor, frequency for all phases.

        Endpoint: GET /ivp/meters/readings
        """
        return self._request("GET", "/ivp/meters/readings")

    def get_consumption_report(self) -> Dict[str, Any]:
        """
        Get power consumption report.

        Returns net consumption data updated every 5 minutes with:
        - Current power (W)
        - Apparent power (VA)
        - Reactive power (VAr)
        - Cumulative energy (Wh)
        - Voltage, current, power factor, frequency

        Endpoint: GET /ivp/meters/reports/consumption
        """
        return self._request("GET", "/ivp/meters/reports/consumption")

    def get_grid_reading(self) -> Dict[str, Any]:
        """
        Get grid readings at the point of connection.

        Returns voltage, current, frequency, active & reactive power
        at the grid connection point per phase.

        Endpoint: GET /ivp/meters/gridReading
        """
        return self._request("GET", "/ivp/meters/gridReading")

    def get_livedata_status(self) -> Dict[str, Any]:
        """
        Get live data status including meter readings.

        Endpoint: GET /ivp/livedata/status
        """
        return self._request("GET", "/ivp/livedata/status")

    def get_ensemble_inventory(self) -> list:
        """
        Get battery/ensemble inventory (for systems with Enphase batteries).

        Endpoint: GET /ivp/ensemble/inventory
        """
        try:
            return self._request("GET", "/ivp/ensemble/inventory")
        except requests.HTTPError as e:
            if e.response.status_code == 404:
                # No battery system installed
                return []
            raise

    def get_ensemble_status(self) -> Dict[str, Any]:
        """
        Get battery/ensemble status (for systems with Enphase batteries).

        Endpoint: GET /ivp/ensemble/status
        """
        try:
            return self._request("GET", "/ivp/ensemble/status")
        except requests.HTTPError as e:
            if e.response.status_code == 404:
                # No battery system installed
                return {}
            raise

    def fetch_current_data(self) -> Dict[str, Any]:
        """
        Fetch all current data from the gateway.

        This is the main method to call for the fetcher service.
        It gathers data from multiple endpoints and normalizes it.

        Returns:
            Dictionary with normalized data including:
            - timestamp
            - power_produced (W)
            - power_consumed (W)
            - power_net (W) - negative = export, positive = import
            - grid_voltage_l1, grid_voltage_l2 (V)
            - grid_frequency (Hz)
            - energy_produced_today_wh
            - energy_consumed_today_wh
            - raw_data (all raw responses)
        """
        timestamp = datetime.now(timezone.utc)
        raw_data = {}
        data = {
            "timestamp": timestamp,
            "power_produced": None,
            "power_consumed": None,
            "power_net": None,
            "grid_voltage_l1": None,
            "grid_voltage_l2": None,
            "grid_frequency": None,
            "energy_produced_today_wh": None,
            "energy_consumed_today_wh": None,
            "energy_lifetime_wh": None,
            "raw_data": raw_data,
        }

        # Get production data from simple endpoint
        try:
            production = self.get_production()
            raw_data["production"] = production

            # Extract production power from simple format
            # {"wattsNow": 1823, "wattHoursToday": 13114, ...}
            if isinstance(production, dict) and "wattsNow" in production:
                data["power_produced"] = production.get("wattsNow")
                data["energy_produced_today_wh"] = production.get("wattHoursToday")
                data["energy_lifetime_wh"] = production.get("wattHoursLifetime")

        except Exception as e:
            logger.warning(f"Failed to get production data from gateway: {e}")

        # Get detailed production.json which includes consumption with whToday
        # This endpoint returns both production AND consumption data
        try:
            production_json = self.get_production_json()
            raw_data["production_json"] = production_json

            if isinstance(production_json, dict):
                # Parse production array
                # [{"type": "inverters", "wNow": 1823, "whToday": 13114, ...}]
                production_array = production_json.get("production", [])
                if isinstance(production_array, list):
                    for item in production_array:
                        if isinstance(item, dict):
                            # Prefer "eim" (energy independence meter) for whole-system
                            # Fall back to "inverters" if eim not available
                            item_type = item.get("type", "")
                            if item_type == "eim":
                                if data["power_produced"] is None:
                                    data["power_produced"] = item.get("wNow")
                                if data["energy_produced_today_wh"] is None:
                                    data["energy_produced_today_wh"] = item.get("whToday")
                                if data["energy_lifetime_wh"] is None:
                                    data["energy_lifetime_wh"] = item.get("whLifetime")
                            elif item_type == "inverters" and data["power_produced"] is None:
                                data["power_produced"] = item.get("wNow")
                                if data["energy_produced_today_wh"] is None:
                                    data["energy_produced_today_wh"] = item.get("whToday")
                                if data["energy_lifetime_wh"] is None:
                                    data["energy_lifetime_wh"] = item.get("whLifetime")

                # Parse consumption array - THIS HAS whToday for consumption!
                # [{"measurementType": "total-consumption", "wNow": 817, "whToday": 5432, ...}]
                consumption_array = production_json.get("consumption", [])
                if isinstance(consumption_array, list):
                    for item in consumption_array:
                        if isinstance(item, dict):
                            measurement_type = item.get("measurementType", "")
                            if measurement_type == "total-consumption":
                                if data["power_consumed"] is None:
                                    data["power_consumed"] = item.get("wNow")
                                if data["energy_consumed_today_wh"] is None:
                                    wh_today = item.get("whToday")
                                    wh_lifetime = item.get("whLifetime")
                                    # Sanity check: some gateways return lifetime as today
                                    # (activeCount=0 or misconfigured consumption meter)
                                    # If whToday == whLifetime or > 200 kWh, it's invalid
                                    if wh_today is not None and wh_lifetime is not None:
                                        if wh_today == wh_lifetime:
                                            logger.warning(
                                                f"Gateway consumption whToday equals whLifetime "
                                                f"({wh_today}), likely misconfigured - skipping"
                                            )
                                        elif wh_today > 200000:  # 200 kWh/day is unreasonable
                                            logger.warning(
                                                f"Gateway consumption whToday={wh_today} Wh "
                                                f"seems too high (>200 kWh) - skipping"
                                            )
                                        else:
                                            data["energy_consumed_today_wh"] = wh_today
                                    elif wh_today is not None and wh_today <= 200000:
                                        data["energy_consumed_today_wh"] = wh_today
                            elif measurement_type == "net-consumption":
                                if data["power_net"] is None:
                                    data["power_net"] = item.get("wNow")

        except Exception as e:
            logger.debug(f"production.json endpoint not available: {e}")

        # Get consumption report for more detailed consumption data
        try:
            consumption_report = self.get_consumption_report()
            raw_data["consumption_report"] = consumption_report

            # Handle both formats: list of reports or single report dict
            reports = []
            if isinstance(consumption_report, list):
                reports = consumption_report
            elif isinstance(consumption_report, dict):
                reports = [consumption_report]

            # Process each report
            for report in reports:
                if not isinstance(report, dict):
                    continue

                report_type = report.get("reportType", "")
                cumulative = report.get("cumulative", {})
                lines = report.get("lines", [])

                if report_type == "total-consumption" or (not report_type and cumulative):
                    if isinstance(cumulative, dict):
                        # Update power consumed if we didn't get it from production endpoint
                        if data["power_consumed"] is None:
                            data["power_consumed"] = cumulative.get("currW")

                        # Note: whDlvdCum is CUMULATIVE (lifetime) energy, not today's
                        # Today's consumption comes from the production endpoint's consumption array
                        # Do NOT use whDlvdCum as a fallback for energy_consumed_today_wh

                        # Get frequency
                        if data["grid_frequency"] is None:
                            data["grid_frequency"] = cumulative.get("freqHz")

                    # Get per-phase voltage from lines array
                    if isinstance(lines, list) and len(lines) >= 2:
                        if isinstance(lines[0], dict) and data["grid_voltage_l1"] is None:
                            data["grid_voltage_l1"] = lines[0].get("rmsVoltage")
                        if isinstance(lines[1], dict) and data["grid_voltage_l2"] is None:
                            data["grid_voltage_l2"] = lines[1].get("rmsVoltage")

                elif report_type == "net-consumption":
                    if isinstance(cumulative, dict) and data["power_net"] is None:
                        data["power_net"] = cumulative.get("currW")

        except Exception as e:
            logger.warning(f"Failed to get consumption report from gateway: {e}")

        # Get grid reading for additional grid info
        try:
            grid_reading = self.get_grid_reading()
            raw_data["grid_reading"] = grid_reading

            # Handle both formats: list of readings or single reading dict
            readings = []
            if isinstance(grid_reading, list):
                readings = grid_reading
            elif isinstance(grid_reading, dict):
                readings = [grid_reading]

            for reading in readings:
                if not isinstance(reading, dict):
                    continue

                channels = reading.get("channels", [])
                if isinstance(channels, list):
                    for channel in channels:
                        if isinstance(channel, dict):
                            phase = channel.get("phase")
                            if phase == "L1" and data["grid_voltage_l1"] is None:
                                data["grid_voltage_l1"] = channel.get("voltage")
                            elif phase == "L2" and data["grid_voltage_l2"] is None:
                                data["grid_voltage_l2"] = channel.get("voltage")

                            # Grid frequency (should be same across phases)
                            if data["grid_frequency"] is None:
                                data["grid_frequency"] = channel.get("freq")

        except Exception as e:
            logger.warning(f"Failed to get grid reading from gateway: {e}")

        # Get meter readings for comprehensive data
        try:
            meter_readings = self.get_meter_readings()
            raw_data["meter_readings"] = meter_readings
        except Exception as e:
            logger.warning(f"Failed to get meter readings from gateway: {e}")

        # Calculate net power if we have production and consumption
        if (
            data["power_net"] is None
            and data["power_produced"] is not None
            and data["power_consumed"] is not None
        ):
            # Net = consumption - production (positive = importing, negative = exporting)
            data["power_net"] = data["power_consumed"] - data["power_produced"]

        return data

    def check_connection(self) -> bool:
        """
        Check if the gateway is reachable and the token is valid.

        Returns:
            True if connection is successful, False otherwise
        """
        try:
            self.get_info()
            return True
        except Exception as e:
            logger.debug(f"Gateway connection check failed: {e}")
            return False
