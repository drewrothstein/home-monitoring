"""
Management script for adding locations and API configurations.

Note: Enphase tokens are stored in the database globally (account-level, not location-specific).
Other API credentials are stored in environment variables.
"""

import json
import logging
import sys
from typing import Any, Dict, Optional

from home_monitor.database import (
    get_location_api_configs,
    get_locations,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def add_location(
    name: str,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    timezone: Optional[str] = None,
    capacity_kw: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Add a new location to the database.

    Args:
        name: Location name
        latitude: Latitude coordinate (optional)
        longitude: Longitude coordinate (optional)
        timezone: Timezone offset (optional, e.g., "+05:00")
        capacity_kw: Solar capacity in kilowatts (optional but recommended)

    Returns:
        Dictionary with created location data including ID
    """
    from home_monitor.database import get_connection

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO locations (name, latitude, longitude, timezone, capacity_kw)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id, name, latitude, longitude, timezone, capacity_kw, created_at, updated_at
            """,
                (name, latitude, longitude, timezone, capacity_kw),
            )
            row = cur.fetchone()
            location = {
                "id": row[0],
                "name": row[1],
                "latitude": row[2],
                "longitude": row[3],
                "timezone": row[4],
                "capacity_kw": row[5],
                "created_at": row[6],
                "updated_at": row[7],
            }
            logger.info(f"Created location: {name} (ID: {location['id']})")
            return location


def add_api_config(
    location_id: int,
    api_type: str,
    config: dict,
    enabled: bool = True,
) -> Dict[str, Any]:
    """
    Add API configuration for a location.

    Note: This only stores non-sensitive configuration (like IDs).
    Enphase tokens are stored globally in the database, not per-location.
    Other credentials (tokens, API keys) must be set in environment variables.

    Args:
        location_id: Location ID
        api_type: API type ('tesla', 'enphase', 'openweather', 'tempest')
        config: Configuration dictionary containing only non-sensitive data:
            - tesla: {"energy_site_id": "..."} (can add multiple configs per location for multiple Site IDs)
            - enphase: {"system_id": 12345} (optional)
            - openweather: {} (no config needed, API key from env)
            - tempest: {"station_id": 35943}
        enabled: Whether the API config is enabled

    Note: You can add multiple API configs of the same type to a location.
          For example, add multiple Tesla configs with different energy_site_id values
          to handle multiple gateways/powerwall banks at the same location.

    Returns:
        Dictionary with created API config data including ID
    """
    from home_monitor.database import get_connection

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO location_api_configs (location_id, api_type, config, enabled)
                VALUES (%s, %s, %s, %s)
                RETURNING id, location_id, api_type, enabled, config, created_at, updated_at
            """,
                (location_id, api_type, json.dumps(config), enabled),
            )
            row = cur.fetchone()
            api_config = {
                "id": row[0],
                "location_id": row[1],
                "api_type": row[2],
                "enabled": row[3],
                "config": json.loads(row[4]) if isinstance(row[4], str) else row[4],
                "created_at": row[5],
                "updated_at": row[6],
            }
            logger.info(f"Created API config: {api_type} for location ID {location_id}")
            return api_config


def list_locations():
    """List all locations in the database."""
    locations = get_locations()

    if not locations:
        print("No locations found")
        return

    print("\nLocations:")
    print("-" * 80)
    for loc in locations:
        print(
            f"ID: {loc['id']} | Name: {loc['name']} | Lat: {loc['latitude']} | Lon: {loc['longitude']}"
        )
        api_configs = get_location_api_configs(loc["id"], enabled_only=False)
        for api_config in api_configs:
            status = "✓" if api_config["enabled"] else "✗"
            print(f"  {status} {api_config['api_type']}")
    print("-" * 80)
    print(
        "\nNote: Enphase tokens are stored in the database globally. Other API credentials are in environment variables."
    )


if __name__ == "__main__":
    """Example usage from command line."""
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python -m home_monitor.manage list")
        print("  python -m home_monitor.manage add_location <name> <lat> <lon> [timezone]")
        print(
            "  python -m home_monitor.manage add_api_config <location_id> <api_type> <config_json>"
        )
        print("")
        print("Examples:")
        print("  python -m home_monitor.manage add_location 'Home' 37.7749 -122.4194 '-08:00'")
        print(
            '  python -m home_monitor.manage add_api_config 1 tesla \'{"energy_site_id": "12345"}\''
        )
        print("  python -m home_monitor.manage add_api_config 1 enphase '{\"system_id\": 12345}'")
        print("  python -m home_monitor.manage add_api_config 1 tempest '{\"station_id\": 35943}'")
        print("  python -m home_monitor.manage add_api_config 1 openweather '{}'")
        print("")
        print(
            "Note: Enphase tokens are stored in the database globally. Other API credentials are in environment variables."
        )
        print("See README.md for details on setting up credentials.")
        sys.exit(1)

    command = sys.argv[1]

    if command == "list":
        list_locations()
    elif command == "add_location":
        if len(sys.argv) < 5:
            print("Usage: add_location <name> <lat> <lon> [timezone]")
            sys.exit(1)
        name = sys.argv[2]
        lat = float(sys.argv[3])
        lon = float(sys.argv[4])
        timezone = sys.argv[5] if len(sys.argv) > 5 else None
        add_location(name, lat, lon, timezone)
    elif command == "add_api_config":
        if len(sys.argv) < 5:
            print("Usage: add_api_config <location_id> <api_type> <config_json>")
            sys.exit(1)
        location_id = int(sys.argv[2])
        api_type = sys.argv[3]
        config = json.loads(sys.argv[4])
        add_api_config(location_id, api_type, config)
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)
