"""
Rachio API client for sprinkler/irrigation data.

API documentation: https://rachio.readme.io/reference/getting-started
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)


class RachioApiClient:
    """Client for Rachio Sprinkler API."""

    BASE_URL = "https://api.rach.io/1/public"

    def __init__(self, api_key: str, device_id: Optional[str] = None):
        """
        Initialize Rachio API client.

        Args:
            api_key: Rachio API key (obtained from https://app.rach.io/login)
            device_id: Optional device ID (can be fetched automatically if not provided)
        """
        self.api_key = api_key
        self.device_id = device_id
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def _handle_api_response(self, response: requests.Response) -> None:
        """
        Handle API response and provide helpful error messages.

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
                    elif "error" in error_data:
                        error_msg += f": {error_data['error']}"
            except (ValueError, KeyError):
                error_text = response.text[:200] if response.text else ""
                if error_text:
                    error_msg += f" - {error_text}"

            if response.status_code in (401, 403):
                error_msg += (
                    "\n\n⚠️  AUTHENTICATION ERROR"
                    "\n\nYour Rachio API key may be invalid or expired."
                    "\nTo get your API key:"
                    "\n1. Log in at https://app.rach.io/"
                    "\n2. Go to Account Settings"
                    "\n3. Click 'Get API Key'"
                    "\n4. Set RACHIO_API_KEY in your .env file"
                )

            raise requests.HTTPError(error_msg, response=response)

    def get_person_info(self) -> Dict[str, Any]:
        """
        Get information about the authenticated user.

        Returns:
            Dictionary containing user info including 'id' field
        """
        url = f"{self.BASE_URL}/person/info"
        response = requests.get(url, headers=self.headers, timeout=30)
        self._handle_api_response(response)
        return response.json()

    def get_person(self, person_id: str) -> Dict[str, Any]:
        """
        Get detailed person info including devices.

        Args:
            person_id: Person ID from get_person_info()

        Returns:
            Dictionary containing person details including 'devices' array
        """
        url = f"{self.BASE_URL}/person/{person_id}"
        response = requests.get(url, headers=self.headers, timeout=30)
        self._handle_api_response(response)
        return response.json()

    def get_device(self, device_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Get device details.

        Args:
            device_id: Device ID (uses instance device_id if not provided)

        Returns:
            Dictionary containing device details including zones
        """
        device_id = device_id or self.device_id
        if not device_id:
            raise ValueError("device_id must be provided or set during initialization")

        url = f"{self.BASE_URL}/device/{device_id}"
        response = requests.get(url, headers=self.headers, timeout=30)
        self._handle_api_response(response)
        return response.json()

    def get_devices(self) -> List[Dict[str, Any]]:
        """
        Get all devices for the authenticated user.

        Returns:
            List of device dictionaries
        """
        # First get person info to get person_id
        person_info = self.get_person_info()
        person_id = person_info.get("id")
        if not person_id:
            raise ValueError("Could not get person ID from API")

        # Get full person details including devices
        person = self.get_person(person_id)
        return person.get("devices", [])

    def get_device_events(
        self,
        device_id: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get device events (including watering events) for a time range.

        Args:
            device_id: Device ID (uses instance device_id if not provided)
            start_time: Start of time range (default: 24 hours ago)
            end_time: End of time range (default: now)

        Returns:
            List of event dictionaries
        """
        device_id = device_id or self.device_id
        if not device_id:
            raise ValueError("device_id must be provided or set during initialization")

        # Default to last 24 hours
        if not end_time:
            end_time = datetime.now(timezone.utc)
        if not start_time:
            start_time = end_time - timedelta(hours=24)

        # Rachio API uses milliseconds since epoch
        start_ms = int(start_time.timestamp() * 1000)
        end_ms = int(end_time.timestamp() * 1000)

        url = f"{self.BASE_URL}/device/{device_id}/event"
        params = {
            "startTime": start_ms,
            "endTime": end_ms,
        }

        response = requests.get(url, headers=self.headers, params=params, timeout=30)
        self._handle_api_response(response)
        return response.json()

    def get_current_schedule(self, device_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Get the current schedule running on the device.

        Args:
            device_id: Device ID (uses instance device_id if not provided)

        Returns:
            Dictionary containing current schedule info (empty if nothing running)
        """
        device_id = device_id or self.device_id
        if not device_id:
            raise ValueError("device_id must be provided or set during initialization")

        url = f"{self.BASE_URL}/device/{device_id}/current_schedule"
        response = requests.get(url, headers=self.headers, timeout=30)
        self._handle_api_response(response)
        return response.json()

    def get_zone(self, zone_id: str) -> Dict[str, Any]:
        """
        Get zone details.

        Args:
            zone_id: Zone ID

        Returns:
            Dictionary containing zone details
        """
        url = f"{self.BASE_URL}/zone/{zone_id}"
        response = requests.get(url, headers=self.headers, timeout=30)
        self._handle_api_response(response)
        return response.json()

    def _parse_zone_number_from_summary(
        self, summary: str, zone_name_map: Dict[str, int]
    ) -> Optional[int]:
        """
        Extract zone number from event summary.

        Summaries look like:
        - "Left Side: Zone 1 began watering at 07:40 AM (EST)."
        - "Backyard Rotors: Z... completed watering at 07:30 AM (EST) for 20 minutes."
        - " Front (new): Zone... began watering at 07:40 AM (EST)."
        - "Soaking Left Side: Zone 1 for 10 minutes..."

        Args:
            summary: The event summary text
            zone_name_map: Dict mapping zone name to zone number

        Returns the zone number as an integer.
        """
        import re

        # First try to match complete "Zone N" pattern
        match = re.search(r"Zone (\d+)", summary)
        if match:
            return int(match.group(1))

        # If no zone number found (truncated summary), try matching by zone name
        # Zone names may start with "Soaking " prefix for cycle events
        search_text = summary
        if search_text.startswith("Soaking "):
            search_text = search_text[8:]  # Remove "Soaking " prefix

        # Try to match the beginning of the summary against known zone names
        for zone_name, zone_number in zone_name_map.items():
            # Check if summary starts with zone name or a truncated version
            # Zone names like "Backyard Rotors: Zone 2" might appear as "Backyard Rotors: Z..."
            if search_text.startswith(zone_name):
                return zone_number
            # Also check if the zone name (without "Zone N" suffix) matches
            # E.g., "Backyard Rotors: Z..." should match "Backyard Rotors: Zone 2"
            name_parts = zone_name.rsplit(": Zone ", 1)
            if len(name_parts) == 2:
                base_name = name_parts[0]
                # Check if summary starts with base_name followed by ": Z"
                if search_text.startswith(f"{base_name}: Z"):
                    return zone_number

        return None

    def _parse_duration_from_summary(self, summary: str) -> Optional[int]:
        """
        Extract duration in seconds from event summary.

        Summaries look like:
        - "... completed watering at 07:50 AM (EST) for 10 minutes."
        - "... completed watering at 07:30 AM (EST) for 20 minutes."

        Returns duration in seconds, or None if not found.
        """
        import re

        match = re.search(r"for (\d+) minutes?", summary)
        if match:
            return int(match.group(1)) * 60

        return None

    def fetch_watering_events(
        self,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """
        Fetch watering events and normalize them into sprinkler run records.

        This parses the device events to extract watering runs with start/end times.

        Args:
            start_time: Start of time range (default: 24 hours ago)
            end_time: End of time range (default: now)

        Returns:
            List of normalized watering run records with:
            - zone_id: Zone ID that was watered
            - zone_name: Name of the zone
            - zone_number: Zone number (1-16)
            - start_time: When watering started
            - end_time: When watering ended
            - duration_seconds: Duration in seconds
            - schedule_type: Type of schedule (MANUAL, AUTOMATIC, etc.)
            - raw_data: Raw event data
        """
        events = self.get_device_events(start_time=start_time, end_time=end_time)

        # Build zone maps from device info
        # zone_map: Maps zone number to zone info
        # zone_name_map: Maps zone name to zone number (for parsing truncated summaries)
        zone_map: Dict[int, Dict[str, Any]] = {}
        zone_name_map: Dict[str, int] = {}
        try:
            device = self.get_device()
            for zone in device.get("zones", []):
                zone_id = zone.get("id")
                zone_name = zone.get("name", "").strip()
                zone_number = zone.get("zoneNumber")
                if zone_id and zone_number:
                    zone_map[zone_number] = {
                        "zone_id": zone_id,
                        "zone_name": zone_name,
                        "zone_number": zone_number,
                    }
                    if zone_name:
                        zone_name_map[zone_name] = zone_number
        except Exception as e:
            logger.warning(f"Failed to get device zones for mapping: {e}")

        # Sort events chronologically (oldest first) for proper start/end matching
        # API returns events in reverse chronological order
        events.sort(key=lambda e: e.get("eventDate", 0))

        # Extract watering runs from events
        # Events have type="ZONE_STATUS" and subType="ZONE_STARTED"/"ZONE_COMPLETED"
        watering_runs = []
        zone_starts: Dict[int, Dict[str, Any]] = {}  # Track zone start events by zone number

        for event in events:
            # Use subType for the actual event action (type is just the category)
            event_type = event.get("type", "")
            sub_type = event.get("subType", "")
            timestamp_ms = event.get("eventDate") or event.get("createDate")
            summary = event.get("summary", "")

            if not timestamp_ms:
                continue

            # Only process ZONE_STATUS events
            if event_type != "ZONE_STATUS":
                continue

            timestamp = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)

            # Parse zone number from summary since events don't include zoneId
            zone_number = self._parse_zone_number_from_summary(summary, zone_name_map)
            if not zone_number:
                continue

            # Look up zone info from our map
            zone_info = zone_map.get(zone_number, {})
            zone_id = zone_info.get("zone_id", f"zone-{zone_number}")
            zone_name = zone_info.get("zone_name", f"Zone {zone_number}")

            # Handle zone start events
            if sub_type == "ZONE_STARTED":
                zone_starts[zone_number] = {
                    "start_time": timestamp,
                    "zone_id": zone_id,
                    "zone_name": zone_name,
                    "zone_number": zone_number,
                    "schedule_type": event.get("scheduleType", "UNKNOWN"),
                    "raw_start_event": event,
                }

            # Handle zone completion/stop events
            elif sub_type in ("ZONE_COMPLETED", "ZONE_STOPPED"):
                start_info = zone_starts.pop(zone_number, None)

                # Try to get duration from summary
                duration_seconds = self._parse_duration_from_summary(summary)

                if start_info:
                    # We have matching start event
                    if duration_seconds is None:
                        # Calculate from timestamps
                        duration_seconds = int(
                            (timestamp - start_info["start_time"]).total_seconds()
                        )

                    watering_runs.append(
                        {
                            "zone_id": start_info.get("zone_id", zone_id),
                            "zone_name": start_info.get("zone_name", zone_name),
                            "zone_number": start_info.get("zone_number", zone_number),
                            "start_time": start_info["start_time"],
                            "end_time": timestamp,
                            "duration_seconds": duration_seconds,
                            "schedule_type": start_info.get("schedule_type", "UNKNOWN"),
                            "raw_data": {
                                "start_event": start_info.get("raw_start_event"),
                                "end_event": event,
                            },
                        }
                    )
                elif duration_seconds:
                    # No matching start event but we have duration - create run from end event
                    # Calculate approximate start time from duration
                    approx_start = timestamp - timedelta(seconds=duration_seconds)
                    watering_runs.append(
                        {
                            "zone_id": zone_id,
                            "zone_name": zone_name,
                            "zone_number": zone_number,
                            "start_time": approx_start,
                            "end_time": timestamp,
                            "duration_seconds": duration_seconds,
                            "schedule_type": "UNKNOWN",
                            "raw_data": {
                                "start_event": None,
                                "end_event": event,
                            },
                        }
                    )

        # Sort by start time
        watering_runs.sort(key=lambda x: x["start_time"])

        return watering_runs

    def is_currently_watering(self) -> Dict[str, Any]:
        """
        Check if the sprinkler is currently running.

        Returns:
            Dictionary with:
            - is_running: bool indicating if watering is in progress
            - current_zone: zone info if running, None otherwise
            - schedule_type: type of schedule running
            - raw_data: raw API response
        """
        try:
            current = self.get_current_schedule()

            # Check if there's an active schedule
            is_running = bool(current and current.get("status") == "PROCESSING")

            result = {
                "is_running": is_running,
                "current_zone": None,
                "schedule_type": None,
                "raw_data": current,
            }

            if is_running:
                # Extract current zone info
                zones = current.get("zoneData", [])
                for zone in zones:
                    if zone.get("running"):
                        result["current_zone"] = {
                            "zone_id": zone.get("zoneId"),
                            "zone_name": (zone.get("zoneName") or "").strip(),
                            "zone_number": zone.get("zoneNumber"),
                            "started_at": (
                                datetime.fromtimestamp(
                                    zone.get("startDate", 0) / 1000, tz=timezone.utc
                                )
                                if zone.get("startDate")
                                else None
                            ),
                        }
                        break

                result["schedule_type"] = current.get("type", "UNKNOWN")

            return result
        except Exception as e:
            logger.warning(f"Failed to check current watering status: {e}")
            return {
                "is_running": False,
                "current_zone": None,
                "schedule_type": None,
                "raw_data": None,
            }
