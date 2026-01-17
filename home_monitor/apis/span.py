"""
Span Power Panel Local API client.

This module provides access to Span Panel APIs, which are accessed
directly on the local network via HTTP. These APIs provide circuit-level
power monitoring and control for smart electrical panels.

API Documentation (unofficial):
https://gist.github.com/hyun007/c689fbed10424b558f140c54851659e3

Token generation requires pressing the door sensor button 3x within a 15-minute
window, then calling POST /api/v1/auth/register with a name/description.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)


class SpanPanelClient:
    """
    Client for Span Power Panel Local APIs.

    Connects directly to the panel on the local network via HTTP.
    Most endpoints require a Bearer token obtained through physical panel interaction.
    """

    def __init__(
        self,
        panel_host: str,
        token: Optional[str] = None,
        panel_name: Optional[str] = None,
        timeout: int = 10,
    ):
        """
        Initialize the Span Panel API client.

        Args:
            panel_host: IP address or hostname of the panel (e.g., "192.168.1.200")
            token: Panel access token (required for protected endpoints)
            panel_name: Human-readable name for the panel (for logging)
            timeout: Request timeout in seconds
        """
        self.panel_host = panel_host
        self.token = token
        self.panel_name = panel_name or panel_host
        self.timeout = timeout
        self.base_url = f"http://{panel_host}"
        self.session = requests.Session()
        if token:
            self.session.headers.update(
                {
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                }
            )
        else:
            self.session.headers.update({"Accept": "application/json"})

    def _request(
        self, method: str, endpoint: str, auth_required: bool = True, **kwargs
    ) -> Dict[str, Any]:
        """
        Make a request to the panel API.

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint (e.g., "/api/v1/status")
            auth_required: Whether this endpoint requires authentication
            **kwargs: Additional arguments passed to requests

        Returns:
            JSON response as dictionary

        Raises:
            requests.HTTPError: If the request fails
        """
        url = f"{self.base_url}{endpoint}"
        kwargs.setdefault("timeout", self.timeout)

        if auth_required and not self.token:
            raise ValueError(
                f"Token required for {endpoint}. "
                f"Run 'make span-register HOST={self.panel_host}' to register a client."
            )

        try:
            response = self.session.request(method, url, **kwargs)
            self._handle_api_response(response)
            return response.json()
        except requests.exceptions.ConnectionError as e:
            logger.error(f"Connection error to panel {self.panel_name} at {self.panel_host}: {e}")
            raise
        except requests.exceptions.Timeout as e:
            logger.error(f"Timeout connecting to panel {self.panel_name} at {self.panel_host}: {e}")
            raise

    def _handle_api_response(self, response: requests.Response) -> None:
        """
        Handle API response and provide helpful error messages.

        Args:
            response: The response object from requests

        Raises:
            requests.HTTPError: If the response indicates an error
        """
        if response.status_code == 401:
            raise requests.HTTPError(
                f"401 Unauthorized: Token is invalid or expired for panel {self.panel_name}. "
                f"Run 'make span-register HOST={self.panel_host}' to register a new client.",
                response=response,
            )
        elif response.status_code == 403:
            raise requests.HTTPError(
                f"403 Forbidden: Access denied for panel {self.panel_name}. "
                f"Ensure the panel is unlocked (press door button 3x) and re-register.",
                response=response,
            )
        response.raise_for_status()

    def get_status(self) -> Dict[str, Any]:
        """
        Get panel status (no auth required).

        Returns firmware version, serial number, door state, network status, etc.

        Endpoint: GET /api/v1/status
        """
        return self._request("GET", "/api/v1/status", auth_required=False)

    def get_panel(self) -> Dict[str, Any]:
        """
        Get panel power data (auth required).

        Returns main relay state, grid power, per-branch power readings.

        Endpoint: GET /api/v1/panel
        """
        return self._request("GET", "/api/v1/panel", auth_required=True)

    def get_circuits(self) -> Dict[str, Any]:
        """
        Get circuit details (auth required).

        Returns circuit names, power, energy, relay state, priority for each circuit.

        Endpoint: GET /api/v1/circuits
        """
        return self._request("GET", "/api/v1/circuits", auth_required=True)

    def get_storage_soe(self) -> Optional[Dict[str, Any]]:
        """
        Get battery state of energy (auth required).

        Returns battery percentage if a battery/solar system is connected.

        Endpoint: GET /api/v1/storage/soe
        """
        try:
            return self._request("GET", "/api/v1/storage/soe", auth_required=True)
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                # No battery connected
                return None
            raise

    def fetch_current_data(self) -> Dict[str, Any]:
        """
        Fetch current panel-level data.

        This is the main method to call for the fetcher service.
        It gathers data from status, panel, and storage endpoints.

        Returns:
            Dictionary with normalized panel data including:
            - timestamp
            - panel_serial
            - instant_grid_power_w
            - feedthrough_power_w
            - main_relay_state, dsm_grid_state, dsm_state, current_run_config
            - door_state, firmware_version, uptime_seconds
            - eth0_link, wlan_link, wwan_link
            - battery_soe_percent (if available)
            - raw_data (all raw responses)
        """
        timestamp = datetime.now(timezone.utc)
        raw_data = {}
        data = {
            "timestamp": timestamp,
            "panel_serial": None,
            "instant_grid_power_w": None,
            "feedthrough_power_w": None,
            "main_relay_state": None,
            "dsm_grid_state": None,
            "dsm_state": None,
            "current_run_config": None,
            "door_state": None,
            "firmware_version": None,
            "uptime_seconds": None,
            "eth0_link": None,
            "wlan_link": None,
            "wwan_link": None,
            "battery_soe_percent": None,
            "raw_data": raw_data,
        }

        # Get status (no auth required)
        try:
            status = self.get_status()
            raw_data["status"] = status

            # Extract software info
            software = status.get("software", {})
            data["firmware_version"] = software.get("firmwareVersion")

            # Extract system info
            system = status.get("system", {})
            data["panel_serial"] = system.get("serial")
            data["door_state"] = system.get("doorState")
            data["uptime_seconds"] = system.get("uptime")

            # Extract network info
            network = status.get("network", {})
            data["eth0_link"] = network.get("eth0Link")
            data["wlan_link"] = network.get("wlanLink")
            data["wwan_link"] = network.get("wwanLink")

        except Exception as e:
            logger.warning(f"Failed to get status from panel {self.panel_name}: {e}")

        # Get panel data (auth required)
        try:
            panel = self.get_panel()
            raw_data["panel"] = panel

            data["instant_grid_power_w"] = panel.get("instantGridPowerW")
            data["feedthrough_power_w"] = panel.get("feedthroughPowerW")
            data["main_relay_state"] = panel.get("mainRelayState")
            data["dsm_grid_state"] = panel.get("dsmGridState")
            data["dsm_state"] = panel.get("dsmState")
            data["current_run_config"] = panel.get("currentRunConfig")

        except Exception as e:
            logger.warning(f"Failed to get panel data from {self.panel_name}: {e}")

        # Get battery SOE if available
        try:
            storage = self.get_storage_soe()
            if storage:
                raw_data["storage"] = storage
                soe = storage.get("soe", {})
                data["battery_soe_percent"] = soe.get("percentage")
        except Exception as e:
            logger.debug(f"Failed to get storage SOE from panel {self.panel_name}: {e}")

        return data

    def fetch_circuit_data(self) -> List[Dict[str, Any]]:
        """
        Fetch circuit-level data.

        Returns a list of circuit readings with normalized data.

        Returns:
            List of dictionaries, each containing:
            - circuit_id: Unique circuit identifier
            - circuit_name: User-defined name for the circuit
            - tabs: List of breaker positions (e.g., [1, 3] for a 240V circuit)
            - instant_power_w: Current power draw in watts
            - import_energy_wh: Cumulative energy consumed (from consumedEnergyWh)
            - export_energy_wh: Cumulative energy produced (from producedEnergyWh)
            - relay_state: CLOSED or OPEN
            - priority: NON_ESSENTIAL, NICE_TO_HAVE, MUST_HAVE
            - is_user_controllable: Can user toggle this circuit
            - is_sheddable: Can be shed during load management
            - is_never_backup: Excluded from backup power
            - raw_data: Full API response for this circuit
        """
        circuits_response = self.get_circuits()
        # API returns "circuits" key (not "spaces" as some docs suggest)
        circuits_data = circuits_response.get("circuits", {})
        if not circuits_data:
            # Fall back to "spaces" for compatibility
            circuits_data = circuits_response.get("spaces", {})

        circuits = []
        for circuit_id, circuit_data in circuits_data.items():
            circuit = {
                "circuit_id": circuit_id,
                "circuit_name": circuit_data.get("name"),
                "tabs": circuit_data.get("tabs", []),  # List of breaker positions
                "instant_power_w": circuit_data.get("instantPowerW"),
                # API uses consumedEnergyWh/producedEnergyWh (not import/export)
                "import_energy_wh": circuit_data.get("consumedEnergyWh"),
                "export_energy_wh": circuit_data.get("producedEnergyWh"),
                "relay_state": circuit_data.get("relayState"),
                "priority": circuit_data.get("priority"),
                # API uses camelCase for boolean fields
                "is_user_controllable": circuit_data.get("isUserControllable"),
                "is_sheddable": circuit_data.get("isSheddable"),
                "is_never_backup": circuit_data.get("isNeverBackup"),
                "raw_data": circuit_data,
            }
            circuits.append(circuit)

        return circuits

    def check_connection(self) -> bool:
        """
        Check if the panel is reachable.

        Returns:
            True if connection is successful, False otherwise
        """
        try:
            self.get_status()
            return True
        except Exception as e:
            logger.debug(f"Panel connection check failed: {e}")
            return False

    @staticmethod
    def register_client(
        panel_host: str,
        client_name: str = "home-monitor",
        client_description: str = "Home Monitor Data Collection",
        timeout: int = 10,
    ) -> Dict[str, Any]:
        """
        Register a new client with the panel (requires panel to be unlocked).

        The panel must be "unlocked" by pressing the door sensor button 3x
        within 15 seconds. The unlock window lasts for 15 minutes.

        Args:
            panel_host: IP address or hostname of the panel
            client_name: Unique name for this client
            client_description: Description of the client

        Returns:
            Dictionary with 'accessToken' and 'tokenType'

        Raises:
            requests.HTTPError: If registration fails (panel not unlocked, etc.)
        """
        url = f"http://{panel_host}/api/v1/auth/register"
        payload = {
            "name": client_name,
            "description": client_description,
        }

        try:
            response = requests.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                timeout=timeout,
            )

            if response.status_code == 200:
                return response.json()
            elif response.status_code == 401 or response.status_code == 403:
                error_detail = response.json().get("detail", "Unknown error")
                raise requests.HTTPError(
                    f"Registration failed: {error_detail}. "
                    f"Ensure the panel is unlocked (press door button 3x within 15 seconds).",
                    response=response,
                )
            else:
                response.raise_for_status()

        except requests.exceptions.ConnectionError as e:
            raise requests.HTTPError(
                f"Cannot connect to panel at {panel_host}. "
                f"Verify the IP address is correct and the panel is on the network: {e}"
            )

        return {}
