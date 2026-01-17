"""
Site configuration management using JSON file.

Site configurations define locations with human-readable names (e.g., NY, FL)
and specify which APIs are configured for each location.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def get_sites_config_path() -> Path:
    """
    Get the path to the sites.json configuration file.

    Looks for sites.json in:
    1. Path specified by SITES_CONFIG_PATH environment variable
    2. Current working directory
    3. Project root (parent of home_monitor package)

    Returns:
        Path to sites.json file
    """
    # Check environment variable first
    env_path = os.getenv("SITES_CONFIG_PATH")
    if env_path:
        path = Path(env_path)
        if path.exists():
            return path
        raise FileNotFoundError(f"Sites config file not found at SITES_CONFIG_PATH: {env_path}")

    # Check current working directory and project root
    for path in [Path.cwd() / "sites.json", Path(__file__).parent.parent / "sites.json"]:
        if path.exists():
            return path

    # Return project root path (will raise error when trying to read)
    return Path(__file__).parent.parent / "sites.json"


def load_sites_config() -> Dict[str, Any]:
    """
    Load site configuration from JSON file.

    Returns:
        Dictionary containing site configurations

    Raises:
        FileNotFoundError: If sites.json file is not found
        json.JSONDecodeError: If JSON file is invalid
        ValueError: If configuration structure is invalid
    """
    config_path = get_sites_config_path()

    if not config_path.exists():
        raise FileNotFoundError(
            f"Sites configuration file not found: {config_path}\n"
            f"Create a sites.json file in the project root or set SITES_CONFIG_PATH environment variable."
        )

    with open(config_path, "r") as f:
        config = json.load(f)

    # Validate structure
    if "sites" not in config:
        raise ValueError("Configuration must have a 'sites' key")

    if not isinstance(config["sites"], dict):
        raise ValueError("'sites' must be a dictionary")

    # Validate each site configuration
    for site_name, site_config in config["sites"].items():
        validate_site_config(site_name, site_config)

    return config


def validate_site_config(site_name: str, site_config: Dict[str, Any]) -> None:
    """
    Validate a single site configuration.

    All API integrations are optional - only capacity_kw is truly required.
    Each integration will be skipped if not configured.

    Args:
        site_name: Name of the site (e.g., "NY", "FL")
        site_config: Site configuration dictionary

    Raises:
        ValueError: If configuration is invalid
    """
    if not isinstance(site_config, dict):
        raise ValueError(f"Site '{site_name}': configuration must be a dictionary")

    # Capacity is required (for solar capacity reference in dashboards)
    if "capacity_kw" not in site_config:
        raise ValueError(f"Site '{site_name}': 'capacity_kw' is required")
    if not isinstance(site_config["capacity_kw"], (int, float)):
        raise ValueError(f"Site '{site_name}': 'capacity_kw' must be a number")
    if site_config["capacity_kw"] < 0:
        raise ValueError(f"Site '{site_name}': 'capacity_kw' must be non-negative")

    # Timezone is optional (used for APIs that need local time calculations)
    if "timezone" in site_config:
        if not isinstance(site_config["timezone"], str):
            raise ValueError(
                f"Site '{site_name}': 'timezone' must be a string (e.g., 'America/New_York')"
            )

    # Location coordinates are required (used for database location entry)
    # Can come from openweather config or a dedicated location block
    has_coordinates = False
    if "openweather" in site_config:
        ow = site_config["openweather"]
        if isinstance(ow, dict) and "latitude" in ow and "longitude" in ow:
            has_coordinates = True
    if "location" in site_config:
        loc = site_config["location"]
        if isinstance(loc, dict) and "latitude" in loc and "longitude" in loc:
            has_coordinates = True
    if not has_coordinates:
        raise ValueError(
            f"Site '{site_name}': location coordinates required. "
            "Add 'openweather' with latitude/longitude, or add a 'location' block."
        )

    # Tempest is optional - validate if present
    if "tempest" in site_config:
        if not isinstance(site_config["tempest"], dict):
            raise ValueError(f"Site '{site_name}': 'tempest' must be a dictionary")
        if "station_id" not in site_config["tempest"]:
            raise ValueError(
                f"Site '{site_name}': 'tempest.station_id' is required when tempest is configured"
            )
        if not isinstance(site_config["tempest"]["station_id"], (int, str)):
            raise ValueError(
                f"Site '{site_name}': 'tempest.station_id' must be an integer or string"
            )

    # OpenWeather is optional - validate if present
    if "openweather" in site_config:
        if not isinstance(site_config["openweather"], dict):
            raise ValueError(f"Site '{site_name}': 'openweather' must be a dictionary")
        if "latitude" not in site_config["openweather"]:
            raise ValueError(
                f"Site '{site_name}': 'openweather.latitude' is required when openweather is configured"
            )
        if "longitude" not in site_config["openweather"]:
            raise ValueError(
                f"Site '{site_name}': 'openweather.longitude' is required when openweather is configured"
            )
        if not isinstance(site_config["openweather"]["latitude"], (int, float)):
            raise ValueError(f"Site '{site_name}': 'openweather.latitude' must be a number")
        if not isinstance(site_config["openweather"]["longitude"], (int, float)):
            raise ValueError(f"Site '{site_name}': 'openweather.longitude' must be a number")

    # Enphase is optional
    if "enphase" in site_config:
        if not isinstance(site_config["enphase"], dict):
            raise ValueError(f"Site '{site_name}': 'enphase' must be a dictionary")
        if "site_id" in site_config["enphase"]:
            if not isinstance(site_config["enphase"]["site_id"], (int, str)):
                raise ValueError(
                    f"Site '{site_name}': 'enphase.site_id' must be an integer or string"
                )

    # Tesla is optional
    if "tesla" in site_config:
        if not isinstance(site_config["tesla"], dict):
            raise ValueError(f"Site '{site_name}': 'tesla' must be a dictionary")
        if "site_ids" in site_config["tesla"]:
            if not isinstance(site_config["tesla"]["site_ids"], list):
                raise ValueError(f"Site '{site_name}': 'tesla.site_ids' must be a list")
            if len(site_config["tesla"]["site_ids"]) == 0:
                raise ValueError(f"Site '{site_name}': 'tesla.site_ids' cannot be empty")
            for site_id in site_config["tesla"]["site_ids"]:
                if not isinstance(site_id, (int, str)):
                    raise ValueError(
                        f"Site '{site_name}': 'tesla.site_ids' must contain integers or strings"
                    )

    # Flume is optional
    if "flume" in site_config:
        if not isinstance(site_config["flume"], dict):
            raise ValueError(f"Site '{site_name}': 'flume' must be a dictionary")
        if "device_id" in site_config["flume"]:
            if not isinstance(site_config["flume"]["device_id"], (int, str)):
                raise ValueError(
                    f"Site '{site_name}': 'flume.device_id' must be an integer or string"
                )

    # Rachio is optional
    if "rachio" in site_config:
        if not isinstance(site_config["rachio"], dict):
            raise ValueError(f"Site '{site_name}': 'rachio' must be a dictionary")
        if "device_id" in site_config["rachio"]:
            if not isinstance(site_config["rachio"]["device_id"], (int, str)):
                raise ValueError(
                    f"Site '{site_name}': 'rachio.device_id' must be an integer or string"
                )

    # Span is optional
    if "span" in site_config:
        if not isinstance(site_config["span"], dict):
            raise ValueError(f"Site '{site_name}': 'span' must be a dictionary")
        if "panels" in site_config["span"]:
            panels = site_config["span"]["panels"]
            if not isinstance(panels, list):
                raise ValueError(f"Site '{site_name}': 'span.panels' must be a list")
            for i, panel in enumerate(panels):
                if not isinstance(panel, dict):
                    raise ValueError(f"Site '{site_name}': 'span.panels[{i}]' must be a dictionary")
                if "host" not in panel:
                    raise ValueError(f"Site '{site_name}': 'span.panels[{i}].host' is required")
                if not isinstance(panel["host"], str):
                    raise ValueError(
                        f"Site '{site_name}': 'span.panels[{i}].host' must be a string"
                    )
                if "name" in panel and not isinstance(panel["name"], str):
                    raise ValueError(
                        f"Site '{site_name}': 'span.panels[{i}].name' must be a string"
                    )


def get_sites() -> Dict[str, Dict[str, Any]]:
    """
    Get all site configurations.

    Returns:
        Dictionary mapping site names to their configurations
    """
    config = load_sites_config()
    return config.get("sites", {})


def get_site(site_name: str) -> Optional[Dict[str, Any]]:
    """
    Get configuration for a specific site.

    Args:
        site_name: Name of the site (e.g., "NY", "FL")

    Returns:
        Site configuration dictionary, or None if not found
    """
    sites = get_sites()
    return sites.get(site_name)


def get_site_location_id(site_name: str) -> Optional[int]:
    """
    Get database location_id for a site name.

    This function looks up the site in the database by name and returns its ID.
    If the location doesn't exist in the database, it returns None.

    Args:
        site_name: Name of the site (e.g., "NY", "FL")

    Returns:
        Location ID from database, or None if not found
    """
    try:
        from home_monitor.database import get_locations

        locations = get_locations()
        for location in locations:
            if location["name"] == site_name:
                return location["id"]
        return None
    except Exception as e:
        logger.warning(f"Could not look up location_id for site '{site_name}': {e}")
        return None


def ensure_site_in_database(site_name: str) -> int:
    """
    Ensure a site exists in the database, creating it if necessary.

    This function creates or updates a location in the database based on the
    site configuration. It uses the OpenWeather coordinates for the location.

    Args:
        site_name: Name of the site (e.g., "NY", "FL")

    Returns:
        Location ID from database

    Raises:
        ValueError: If site configuration is not found
        Exception: If database operation fails
    """
    site_config = get_site(site_name)
    if not site_config:
        raise ValueError(f"Site '{site_name}' not found in configuration")

    try:
        from home_monitor.database import get_connection, get_locations

        # Get coordinates from OpenWeather config or location block
        latitude = None
        longitude = None
        if "openweather" in site_config:
            openweather_config = site_config["openweather"]
            latitude = openweather_config.get("latitude")
            longitude = openweather_config.get("longitude")
        if (latitude is None or longitude is None) and "location" in site_config:
            location_config = site_config["location"]
            latitude = location_config.get("latitude", latitude)
            longitude = location_config.get("longitude", longitude)
        if latitude is None or longitude is None:
            raise ValueError(
                f"Site '{site_name}': latitude and longitude required. "
                "Add 'openweather' or 'location' block with coordinates."
            )

        # Get capacity (required)
        capacity_kw = site_config.get("capacity_kw")
        if capacity_kw is None:
            raise ValueError(f"Site '{site_name}': 'capacity_kw' is required")

        # Check if location already exists
        locations = get_locations()
        for location in locations:
            if location["name"] == site_name:
                # Update coordinates and capacity if they've changed
                needs_update = False
                update_fields = []
                update_values = []

                if (
                    abs(location.get("latitude", 0) - latitude) > 0.0001
                    or abs(location.get("longitude", 0) - longitude) > 0.0001
                ):
                    needs_update = True
                    update_fields.append("latitude = %s")
                    update_fields.append("longitude = %s")
                    update_values.extend([latitude, longitude])

                existing_capacity = location.get("capacity_kw")
                if existing_capacity is None or abs(existing_capacity - capacity_kw) > 0.0001:
                    needs_update = True
                    update_fields.append("capacity_kw = %s")
                    update_values.append(capacity_kw)

                if needs_update:
                    update_fields.append("updated_at = NOW()")
                    update_values.append(location["id"])
                    with get_connection() as conn:
                        with conn.cursor() as cur:
                            cur.execute(
                                f"""
                                UPDATE locations
                                SET {', '.join(update_fields)}
                                WHERE id = %s
                                """,
                                tuple(update_values),
                            )
                return location["id"]

        # Create new location
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO locations (name, latitude, longitude, capacity_kw)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id
                    """,
                    (site_name, latitude, longitude, capacity_kw),
                )
                location_id = cur.fetchone()[0]
                logger.info(f"Created location '{site_name}' in database (ID: {location_id})")
                return location_id

    except Exception as e:
        logger.error(f"Failed to ensure site '{site_name}' in database: {e}")
        raise
