"""
Test script for testing individual API services independently.

This script allows you to test each service (Tesla, Enphase, OpenWeather, Tempest, Flume, Rachio, TankUtility)
independently to review data and debug issues without running the full fetch cycle.

Usage:
    python scripts/test_service.py <service_name> [--location <name>] [--save-to-db] [--hide-raw] [--lat <lat>] [--lon <lon>] [--station-id <id>] [--energy-site-id <id>] [--system-id <id>] [--device-id <id>]

Examples:
    python scripts/test_service.py tesla
    python scripts/test_service.py tesla --energy-site-id <energy_site_id>  # Test without database
    python scripts/test_service.py enphase --location "Home"
    python scripts/test_service.py enphase --system-id <system_id>  # Test without database
    python scripts/test_service.py openweather --save-to-db
    python scripts/test_service.py openweather --lat 37.7749 --lon -122.4194  # Test without database
    python scripts/test_service.py tempest --station-id 35943  # Test without database
    python scripts/test_service.py tempest --hide-raw
    python scripts/test_service.py flume --location "FL"
    python scripts/test_service.py flume --device-id <device_id>  # Test without database
    python scripts/test_service.py rachio --location "FL"
    python scripts/test_service.py rachio --device-id <device_id>  # Test without database
    python scripts/test_service.py tankutility --location "FL"
    python scripts/test_service.py tankutility --device-id <device_id>  # Test without database
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

# Add parent directory to path so we can import home_monitor
script_dir = Path(__file__).parent
project_root = script_dir.parent
sys.path.insert(0, str(project_root))

from home_monitor.apis.enphase import EnphaseApiClient  # noqa: E402
from home_monitor.apis.flume import FlumeApiClient  # noqa: E402
from home_monitor.apis.iaqualink import IAqualinkApiClient  # noqa: E402
from home_monitor.apis.openweather import OpenWeatherApiClient  # noqa: E402
from home_monitor.apis.rachio import RachioApiClient  # noqa: E402
from home_monitor.apis.tankutility import TankUtilityApiClient  # noqa: E402
from home_monitor.apis.tempest import TempestApiClient  # noqa: E402
from home_monitor.apis.tesla import TeslaApiClient  # noqa: E402
from home_monitor.config import (  # noqa: E402
    get_enphase_credentials,
    get_flume_credentials,
    get_iaqualink_credentials,
    get_openweather_api_key,
    get_rachio_credentials,
    get_tankutility_credentials,
    get_tempest_credentials,
    get_tesla_credentials,
)
from home_monitor.database import (  # noqa: E402
    insert_battery_reading,
    insert_irradiance_reading,
    insert_pool_reading,
    insert_power_reading,
    insert_propane_reading,
    insert_sprinkler_run,
    insert_water_reading,
)
from home_monitor.site_config import (  # noqa: E402
    ensure_site_in_database,
    get_site,
    get_sites,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def print_section(title: str):
    """Print a formatted section header."""
    print("\n" + "=" * 80)
    print(f"  {title}")
    print("=" * 80)


def print_data(title: str, data: Any, indent: int = 0):
    """Print data in a formatted way."""
    prefix = "  " * indent
    if data is None:
        print(f"{prefix}{title}: None")
    elif isinstance(data, (int, float)):
        print(f"{prefix}{title}: {data}")
    elif isinstance(data, str):
        print(f"{prefix}{title}: {data}")
    elif isinstance(data, dict):
        print(f"{prefix}{title}:")
        for key, value in data.items():
            print_data(key, value, indent + 1)
    elif isinstance(data, list):
        print(f"{prefix}{title}: [{len(data)} items]")
        for i, item in enumerate(data[:5]):  # Show first 5 items
            print_data(f"[{i}]", item, indent + 1)
        if len(data) > 5:
            print(f"{prefix}  ... and {len(data) - 5} more items")
    else:
        print(f"{prefix}{title}: {data}")


def test_tesla(
    location: Dict[str, Any], api_config: Dict[str, Any], save: bool = False
) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """Test Tesla API service. Returns (success, data)."""
    print_section("Testing Tesla API")

    # Get credentials
    api_key, _ = get_tesla_credentials()
    if not api_key:
        print("❌ ERROR: Teslemetry API key not found in environment variables")
        print("   Set TESLEMETRY_API_KEY in your .env file")
        return False, None

    # Get config
    config = api_config.get("config", {})
    if isinstance(config, str):
        config = json.loads(config)

    energy_site_id = config.get("energy_site_id") or config.get("gateway_id")
    if not energy_site_id:
        print("❌ ERROR: Tesla energy_site_id not configured")
        print(
            '   Add it via: python -m home_monitor.manage add_api_config <location_id> tesla \'{"energy_site_id": "..."}\''
        )
        return False, None

    print(f"Location: {location['name']}")
    print(f"Energy Site ID: {energy_site_id}")

    try:
        # Fetch data
        print("\n📡 Fetching data from Teslemetry API...")
        client = TeslaApiClient(access_token=api_key, energy_site_id=energy_site_id)
        data = client.fetch_current_data()

        # Display normalized data
        print_section("Normalized Data")
        print_data("Timestamp", data.get("timestamp"))
        print("\nPower Data:")
        print_data("  Power Produced (W)", data.get("power_produced"))
        print_data("  Power Consumed (W)", data.get("power_consumed"))
        print_data("  Power Exported (W)", data.get("power_exported"))
        print_data("  Power Imported (W)", data.get("power_imported"))
        print("\nBattery Data:")
        print_data("  Battery Energy (Wh)", data.get("battery_energy"))
        print_data("  Battery Power (W)", data.get("battery_power"))
        print_data("  Battery SOC (%)", data.get("battery_soc"))

        # Save to database if requested
        if save:
            print_section("Saving to Database")
            power_reading_id = None
            battery_reading_id = None

            if data.get("power_produced") is not None or data.get("power_consumed") is not None:
                power_reading_id = insert_power_reading(
                    location_id=location["id"],
                    timestamp=data["timestamp"],
                    power_produced=data.get("power_produced"),
                    power_consumed=data.get("power_consumed"),
                    power_exported=data.get("power_exported"),
                    power_imported=data.get("power_imported"),
                    source="tesla",
                    raw_data=data.get("raw_data"),
                )
                print(f"✓ Saved power reading (ID: {power_reading_id})")

            if data.get("battery_energy") is not None or data.get("battery_soc") is not None:
                battery_reading_id = insert_battery_reading(
                    location_id=location["id"],
                    timestamp=data["timestamp"],
                    energy_charged=data.get("battery_energy"),
                    state_of_charge=data.get("battery_soc"),
                    power_charging=(
                        data.get("battery_power") if data.get("battery_power", 0) > 0 else None
                    ),
                    power_discharging=(
                        abs(data.get("battery_power")) if data.get("battery_power", 0) < 0 else None
                    ),
                    source="tesla",
                    raw_data=data.get("raw_data"),
                )
                print(f"✓ Saved battery reading (ID: {battery_reading_id})")

            if not power_reading_id and not battery_reading_id:
                print("⚠ No data to save (all values are None)")

        return True, data

    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        logger.exception("Error testing Tesla API")
        return False, None


def test_enphase(
    location: Dict[str, Any], api_config: Dict[str, Any], save: bool = False
) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """Test Enphase API service. Returns (success, data)."""
    print_section("Testing Enphase API")

    # Get credentials
    access_token, api_key = get_enphase_credentials()
    if not access_token or not api_key:
        print("❌ ERROR: Enphase credentials not found")
        if not access_token:
            print(
                "   Access token not found in database. Run 'make enphase-exchange' to set up tokens."
            )
        if not api_key:
            print("   Set ENPHASE_API_KEY in your .env file")
        return False, None

    # Get config
    config = api_config.get("config", {})
    if isinstance(config, str):
        config = json.loads(config)

    system_id = config.get("system_id")
    if system_id:
        print(f"Location: {location['name']}")
        print(f"System ID: {system_id}")
    else:
        print(f"Location: {location['name']}")
        print("System ID: Not configured (will try to fetch from API)")

    try:
        # Fetch data
        print("\n📡 Fetching data from Enphase API...")
        client = EnphaseApiClient(
            access_token=access_token,
            api_key=api_key,
            system_id=system_id,
        )
        data = client.fetch_current_data()

        # Display normalized data
        print_section("Normalized Data")
        print_data("Timestamp", data.get("timestamp"))
        print("\nPower Data:")
        print_data("  Power Produced (W)", data.get("power_produced"))
        print_data("  Power Consumed (W)", data.get("power_consumed"))
        print_data("  Power Exported (W)", data.get("power_exported"))
        print_data("  Power Imported (W)", data.get("power_imported"))
        print("\nEnergy Data:")
        print_data("  Energy Imported (kWh)", data.get("energy_imported_kwh"))
        print_data("  Energy Exported (kWh)", data.get("energy_exported_kwh"))

        # Save to database if requested
        if save:
            print_section("Saving to Database")
            if (
                data.get("power_produced") is not None
                or data.get("power_consumed") is not None
                or data.get("power_exported") is not None
                or data.get("power_imported") is not None
                or data.get("energy_imported_kwh") is not None
                or data.get("energy_exported_kwh") is not None
            ):
                power_reading_id = insert_power_reading(
                    location_id=location["id"],
                    timestamp=data["timestamp"],
                    power_produced=data.get("power_produced"),
                    power_consumed=data.get("power_consumed"),
                    power_exported=data.get("power_exported"),
                    power_imported=data.get("power_imported"),
                    energy_imported_kwh=data.get("energy_imported_kwh"),
                    energy_exported_kwh=data.get("energy_exported_kwh"),
                    source="enphase",
                    raw_data=data.get("raw_data"),
                )
                print(f"✓ Saved power reading (ID: {power_reading_id})")
            else:
                print("⚠ No power or energy data to save")

        return True, data

    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        logger.exception("Error testing Enphase API")
        return False, None


def test_openweather(
    location: Dict[str, Any], api_config: Dict[str, Any], save: bool = False
) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """Test OpenWeather API service. Returns (success, data)."""
    print_section("Testing OpenWeather API")

    # Get API key
    api_key = get_openweather_api_key(location["name"])
    if not api_key:
        print("❌ ERROR: OpenWeather API key not found in environment variables")
        print(
            f"   Set OPENWEATHER_API_KEY or OPENWEATHER_API_KEY_{location['name'].upper().replace(' ', '_')}"
        )
        return False, None

    print(f"Location: {location['name']}")
    print(f"Coordinates: {location['latitude']}, {location['longitude']}")
    if location.get("timezone"):
        print(f"Timezone: {location['timezone']}")

    try:
        # Fetch data
        print("\n📡 Fetching data from OpenWeather API...")
        client = OpenWeatherApiClient(api_key=api_key)
        data = client.fetch_current_data(
            latitude=location["latitude"],
            longitude=location["longitude"],
            timezone=location.get("timezone"),
        )

        # Display normalized data
        print_section("Normalized Data")
        print_data("Timestamp", data.get("timestamp"))
        print("\nIrradiance Data (W/m²):")
        print_data("  GHI Clear Sky", data.get("ghi_clear_sky"))
        print_data("  GHI Cloudy Sky", data.get("ghi_cloudy_sky"))
        print_data("  DNI Clear Sky", data.get("dni_clear_sky"))
        print_data("  DNI Cloudy Sky", data.get("dni_cloudy_sky"))
        print_data("  DHI Clear Sky", data.get("dhi_clear_sky"))
        print_data("  DHI Cloudy Sky", data.get("dhi_cloudy_sky"))

        # Save to database if requested
        if save:
            if location.get("id") == 0:
                print("❌ ERROR: Cannot save to database - no valid location ID")
                print("   Use database location or set DATABASE_URL")
                return False, data

            print_section("Saving to Database")
            has_data = any(
                [
                    data.get("ghi_clear_sky") is not None,
                    data.get("ghi_cloudy_sky") is not None,
                    data.get("dni_clear_sky") is not None,
                ]
            )
            if has_data:
                irradiance_reading_id = insert_irradiance_reading(
                    location_id=location["id"],
                    timestamp=data["timestamp"],
                    ghi_clear_sky=data.get("ghi_clear_sky"),
                    ghi_cloudy_sky=data.get("ghi_cloudy_sky"),
                    dni_clear_sky=data.get("dni_clear_sky"),
                    dni_cloudy_sky=data.get("dni_cloudy_sky"),
                    dhi_clear_sky=data.get("dhi_clear_sky"),
                    dhi_cloudy_sky=data.get("dhi_cloudy_sky"),
                    source="openweather",
                    raw_data=data.get("raw_data"),
                )
                print(f"✓ Saved irradiance reading (ID: {irradiance_reading_id})")
            else:
                print("⚠ No irradiance data to save (all values are None)")

        return True, data

    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        logger.exception("Error testing OpenWeather API")
        return False, None


def test_tempest(
    location: Dict[str, Any], api_config: Dict[str, Any], save: bool = False
) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """Test Tempest API service. Returns (success, data)."""
    print_section("Testing Tempest API")

    # Get credentials
    token = get_tempest_credentials(location["name"])
    if not token:
        print("❌ ERROR: Tempest token not found in environment variables")
        print(f"   Set TEMPEST_TOKEN or TEMPEST_TOKEN_{location['name'].upper().replace(' ', '_')}")
        return False, None

    # Get config
    config = api_config.get("config", {})
    if isinstance(config, str):
        config = json.loads(config)

    station_id = config.get("station_id")
    if not station_id:
        print("❌ ERROR: Tempest station_id not configured")
        print(
            "   Add it via: python -m home_monitor.manage add_api_config <location_id> tempest '{\"station_id\": 35943}'"
        )
        return False, None

    print(f"Location: {location['name']}")
    print(f"Station ID: {station_id}")

    try:
        # Fetch data
        print("\n📡 Fetching data from Tempest API...")
        client = TempestApiClient(token=token, station_id=station_id)
        data = client.fetch_current_data()

        # Display normalized data
        print_section("Normalized Data")
        print_data("Timestamp", data.get("timestamp"))
        print("\nIrradiance Data (W/m²):")
        print_data("  GHI Clear Sky", data.get("ghi_clear_sky"))
        print_data("  GHI Cloudy Sky (actual measured)", data.get("ghi_cloudy_sky"))
        print_data("  DNI Clear Sky", data.get("dni_clear_sky"))
        print_data("  DNI Cloudy Sky", data.get("dni_cloudy_sky"))
        print_data("  DHI Clear Sky", data.get("dhi_clear_sky"))
        print_data("  DHI Cloudy Sky", data.get("dhi_cloudy_sky"))

        # Save to database if requested
        if save:
            print_section("Saving to Database")
            if data.get("ghi_cloudy_sky") is not None:
                irradiance_reading_id = insert_irradiance_reading(
                    location_id=location["id"],
                    timestamp=data["timestamp"],
                    ghi_clear_sky=data.get("ghi_clear_sky"),
                    ghi_cloudy_sky=data.get("ghi_cloudy_sky"),
                    dni_clear_sky=data.get("dni_clear_sky"),
                    dni_cloudy_sky=data.get("dni_cloudy_sky"),
                    dhi_clear_sky=data.get("dhi_clear_sky"),
                    dhi_cloudy_sky=data.get("dhi_cloudy_sky"),
                    source="tempest",
                    raw_data=data.get("raw_data"),
                )
                print(f"✓ Saved irradiance reading (ID: {irradiance_reading_id})")
            else:
                print("⚠ No irradiance data to save (ghi_cloudy_sky is None)")

        return True, data

    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        logger.exception("Error testing Tempest API")
        return False, None


def test_flume(
    location: Dict[str, Any], api_config: Dict[str, Any], save: bool = False
) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """Test Flume API service. Returns (success, data)."""
    print_section("Testing Flume API")

    # Get credentials
    access_token, user_id = get_flume_credentials()
    if not access_token:
        print("❌ ERROR: Flume access token not found")
        print("   Access token not found in database. Run 'make flume-token' to set up tokens.")
        return False, None

    # Get config
    config = api_config.get("config", {})
    if isinstance(config, str):
        config = json.loads(config)

    device_id = config.get("device_id")
    if not device_id:
        print("❌ ERROR: Flume device_id not configured")
        print(
            '   Add it via: python -m home_monitor.manage add_api_config <location_id> flume \'{"device_id": "..."}\''
        )
        return False, None

    print(f"Location: {location['name']}")
    print(f"Device ID: {device_id}")
    print(f"User ID: {user_id or '(extracted from token)'}")

    try:
        # Fetch data
        print("\n📡 Fetching data from Flume API...")
        client = FlumeApiClient(
            access_token=access_token,
            user_id=user_id,
            device_id=device_id,
        )
        data = client.fetch_current_data()

        # Display normalized data
        print_section("Normalized Data")
        print_data("Timestamp", data.get("timestamp"))
        print("\nWater Usage Data:")
        print_data("  Flow Rate (GPM)", data.get("flow_rate_gpm"))
        print_data("  Usage Today (gallons)", data.get("usage_today_gallons"))
        print_data("  Usage Last Hour (gallons)", data.get("usage_hour_gallons"))

        # Save to database if requested
        if save:
            if location.get("id") == 0:
                print("❌ ERROR: Cannot save to database - no valid location ID")
                print("   Use database location or set DATABASE_URL")
                return False, data

            print_section("Saving to Database")
            readings_saved = 0

            # Save daily usage
            if data.get("usage_today_gallons") is not None:
                water_reading_id = insert_water_reading(
                    location_id=location["id"],
                    timestamp=data["timestamp"],
                    usage_gallons=data.get("usage_today_gallons"),
                    usage_period="day",
                    source="flume",
                    raw_data=data.get("raw_data"),
                )
                print(f"✓ Saved daily water reading (ID: {water_reading_id})")
                readings_saved += 1

            # Save hourly usage
            if data.get("usage_hour_gallons") is not None:
                water_reading_id = insert_water_reading(
                    location_id=location["id"],
                    timestamp=data["timestamp"],
                    usage_gallons=data.get("usage_hour_gallons"),
                    usage_period="hour",
                    source="flume",
                    raw_data=data.get("raw_data"),
                )
                print(f"✓ Saved hourly water reading (ID: {water_reading_id})")
                readings_saved += 1

            if readings_saved == 0:
                print("⚠ No water data to save (all values are None)")

        return True, data

    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        logger.exception("Error testing Flume API")
        return False, None


def test_rachio(
    location: Dict[str, Any], api_config: Dict[str, Any], save: bool = False
) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """Test Rachio API service. Returns (success, data)."""
    print_section("Testing Rachio API")

    # Get credentials
    api_key = get_rachio_credentials()
    if not api_key:
        print("❌ ERROR: Rachio API key not found in environment variables")
        print("   Set RACHIO_API_KEY in your .env file")
        print("   Get your API key from https://app.rach.io/ under Account Settings")
        return False, None

    # Get config
    config = api_config.get("config", {})
    if isinstance(config, str):
        config = json.loads(config)

    device_id = config.get("device_id")
    if not device_id:
        print("❌ ERROR: Rachio device_id not configured")
        print(
            '   Add it via: python -m home_monitor.manage add_api_config <location_id> rachio \'{"device_id": "..."}\''
        )
        return False, None

    print(f"Location: {location['name']}")
    print(f"Device ID: {device_id}")

    try:
        # Fetch data
        print("\n📡 Fetching data from Rachio API...")
        client = RachioApiClient(api_key=api_key, device_id=device_id)

        # Check current watering status
        status = client.is_currently_watering()
        print_section("Current Status")
        if status["is_running"]:
            print("💧 Sprinkler is currently RUNNING")
            if status["current_zone"]:
                zone = status["current_zone"]
                print(
                    f"   Zone: {zone.get('zone_name', 'Unknown')} (Zone {zone.get('zone_number', '?')})"
                )
                print(f"   Started at: {zone.get('started_at')}")
            print(f"   Schedule type: {status.get('schedule_type', 'Unknown')}")
        else:
            print("⏸  Sprinkler is currently IDLE")

        # Fetch recent watering events
        print("\n📡 Fetching watering events from last 24 hours...")
        watering_runs = client.fetch_watering_events()

        # Prepare combined data for display and saving
        data = {
            "timestamp": (
                status.get("raw_data", {}).get("timestamp") if status.get("raw_data") else None
            ),
            "is_running": status["is_running"],
            "current_zone": status["current_zone"],
            "schedule_type": status["schedule_type"],
            "watering_runs": watering_runs,
            "raw_data": {
                "current_status": status.get("raw_data"),
                "watering_runs_count": len(watering_runs),
            },
        }

        print_section("Recent Watering Events (last 24 hours)")
        if watering_runs:
            print(f"Found {len(watering_runs)} watering run(s):\n")
            for i, run in enumerate(watering_runs[:10]):  # Show first 10
                print(
                    f"  [{i + 1}] {run.get('zone_name', 'Unknown Zone')} (Zone {run.get('zone_number', '?')})"
                )
                print(f"      Start: {run.get('start_time')}")
                print(f"      End:   {run.get('end_time')}")
                duration_min = (run.get("duration_seconds") or 0) // 60
                print(f"      Duration: {duration_min} minutes")
                print(f"      Schedule: {run.get('schedule_type', 'Unknown')}")
                print()
            if len(watering_runs) > 10:
                print(f"  ... and {len(watering_runs) - 10} more runs")
        else:
            print("  No watering events found in the last 24 hours")

        # Save to database if requested
        if save:
            if location.get("id") == 0:
                print("❌ ERROR: Cannot save to database - no valid location ID")
                print("   Use database location or set DATABASE_URL")
                return False, data

            print_section("Saving to Database")
            if watering_runs:
                saved_count = 0
                skipped_count = 0
                for run in watering_runs:
                    try:
                        run_id = insert_sprinkler_run(
                            location_id=location["id"],
                            device_id=device_id,
                            zone_id=run["zone_id"],
                            zone_name=run.get("zone_name"),
                            zone_number=run.get("zone_number"),
                            start_time=run["start_time"],
                            end_time=run["end_time"],
                            duration_seconds=run.get("duration_seconds"),
                            schedule_type=run.get("schedule_type"),
                            source="rachio",
                            raw_data=run.get("raw_data"),
                        )
                        print(f"✓ Saved sprinkler run (ID: {run_id})")
                        saved_count += 1
                    except Exception as e:
                        # Likely duplicate entry
                        skipped_count += 1
                        logger.debug(f"Skipped run (possibly duplicate): {e}")

                if saved_count > 0:
                    print(f"\n✓ Saved {saved_count} sprinkler run(s)")
                if skipped_count > 0:
                    print(f"⚠ Skipped {skipped_count} run(s) (possibly duplicates)")
            else:
                print("⚠ No watering runs to save")

        return True, data

    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        logger.exception("Error testing Rachio API")
        return False, None


def test_tankutility(
    location: Dict[str, Any], api_config: Dict[str, Any], save: bool = False
) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """Test Tank Utility API service. Returns (success, data)."""
    print_section("Testing Tank Utility API")

    # Get credentials
    email, password = get_tankutility_credentials()
    if not email or not password:
        print("❌ ERROR: Tank Utility credentials not found in environment variables")
        print("   Set TANK_UTILITY_EMAIL and TANK_UTILITY_PASSWORD in your .env file")
        return False, None

    # Get config
    config = api_config.get("config", {})
    if isinstance(config, str):
        config = json.loads(config)

    device_id = config.get("device_id")
    if not device_id:
        print("❌ ERROR: Tank Utility device_id not configured")
        print('   Add it via: "tankutility": {"device_id": "..."} in sites.json')
        return False, None

    print(f"Location: {location['name']}")
    print(f"Device ID: {device_id}")
    print(f"Email: {email}")

    try:
        # Fetch data
        print("\n📡 Fetching data from Tank Utility API...")
        client = TankUtilityApiClient(email=email, password=password)
        data = client.fetch_current_data(device_id=device_id)

        # Display normalized data
        print_section("Normalized Data")
        print_data("Timestamp", data.get("timestamp"))
        print("\nTank Data:")
        print_data("  Tank Level (%)", data.get("tank_level_percent"))
        print_data("  Tank Level (gallons)", data.get("tank_level_gallons"))
        print_data("  Capacity (gallons)", data.get("capacity_gallons"))
        print_data("  Temperature (°F)", data.get("temperature_f"))
        print("\nDevice Info:")
        print_data("  Device Name", data.get("device_name"))
        print_data("  Fuel Type", data.get("fuel_type"))
        print_data("  Orientation", data.get("orientation"))
        print_data("  Avg Consumption (gal/day)", data.get("average_consumption"))
        print("\nBattery Status:")
        print_data("  Battery Status", data.get("battery_status"))
        print_data("  Battery Warning", data.get("battery_warn"))
        print_data("  Battery Critical", data.get("battery_crit"))

        # Save to database if requested
        if save:
            if location.get("id") == 0:
                print("❌ ERROR: Cannot save to database - no valid location ID")
                print("   Use database location or set DATABASE_URL")
                return False, data

            print_section("Saving to Database")
            if data.get("tank_level_percent") is not None:
                propane_reading_id = insert_propane_reading(
                    location_id=location["id"],
                    device_id=device_id,
                    timestamp=data["timestamp"],
                    tank_level_percent=data.get("tank_level_percent"),
                    tank_level_gallons=data.get("tank_level_gallons"),
                    capacity_gallons=data.get("capacity_gallons"),
                    temperature_f=data.get("temperature_f"),
                    battery_status=data.get("battery_status"),
                    battery_warn=data.get("battery_warn"),
                    battery_crit=data.get("battery_crit"),
                    fuel_type=data.get("fuel_type"),
                    source="tankutility",
                    raw_data=data.get("raw_data"),
                )
                print(f"✓ Saved propane reading (ID: {propane_reading_id})")
            else:
                print("⚠ No tank level data to save")

        return True, data

    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        logger.exception("Error testing Tank Utility API")
        return False, None


def test_iaqualink(
    location: Dict[str, Any], api_config: Dict[str, Any], save: bool = False
) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """Test iAqualink API service. Returns (success, data)."""
    print_section("Testing iAqualink API")

    # Get credentials
    email, password = get_iaqualink_credentials()
    if not email or not password:
        print("❌ ERROR: iAqualink credentials not found in environment variables")
        print("   Set IAQUALINK_EMAIL and IAQUALINK_PASSWORD in your .env file")
        return False, None

    # Get config
    config = api_config.get("config", {})
    if isinstance(config, str):
        config = json.loads(config)

    serial_number = config.get("serial_number")
    device_name = config.get("device_name")

    print(f"Location: {location['name']}")
    print(f"Email: {email}")
    if serial_number:
        print(f"Serial Number: {serial_number}")
    elif device_name:
        print(f"Device Name: {device_name}")
    else:
        print("Device: First device in account")

    try:
        # Fetch data
        print("\n📡 Fetching data from iAqualink API...")
        client = IAqualinkApiClient(
            email=email,
            password=password,
            device_name=device_name,
            serial_number=serial_number,
        )

        # List devices first
        print("\n📋 Available Devices:")
        devices = client.list_devices()
        for i, device in enumerate(devices):
            print(f"   [{i + 1}] {device.get('name')} (Serial: {device.get('serial_number')})")

        data = client.fetch_current_data()

        # Display normalized data
        print_section("Normalized Data")
        print_data("Timestamp", data.get("timestamp"))
        print("\nTemperatures (°F):")
        print_data("  Pool Temp", data.get("pool_temp"))
        print_data("  Spa Temp", data.get("spa_temp"))
        print_data("  Air Temp", data.get("air_temp"))
        print("\nSet Points (°F):")
        print_data("  Pool Set Point", data.get("pool_set_point"))
        print_data("  Spa Set Point", data.get("spa_set_point"))
        print("\nPump Status:")
        print_data("  Pool Pump", "ON" if data.get("pool_pump") else "OFF")
        print_data("  Spa Pump", "ON" if data.get("spa_pump") else "OFF")
        print("\nHeater Status:")
        print_data("  Pool Heater", "ON" if data.get("pool_heater") else "OFF")
        print_data("  Spa Heater", "ON" if data.get("spa_heater") else "OFF")

        # Get serial number from data if not provided
        if not serial_number:
            # Extract from raw_data or devices list
            if devices:
                serial_number = devices[0].get("serial_number")

        # Save to database if requested
        if save:
            if location.get("id") == 0:
                print("❌ ERROR: Cannot save to database - no valid location ID")
                print("   Use database location or set DATABASE_URL")
                return False, data

            print_section("Saving to Database")
            if data.get("pool_temp") is not None or data.get("spa_temp") is not None:
                pool_reading_id = insert_pool_reading(
                    location_id=location["id"],
                    serial_number=serial_number or "unknown",
                    timestamp=data["timestamp"],
                    pool_temp=data.get("pool_temp"),
                    spa_temp=data.get("spa_temp"),
                    air_temp=data.get("air_temp"),
                    pool_set_point=data.get("pool_set_point"),
                    spa_set_point=data.get("spa_set_point"),
                    pool_pump=data.get("pool_pump"),
                    spa_pump=data.get("spa_pump"),
                    pool_heater=data.get("pool_heater"),
                    spa_heater=data.get("spa_heater"),
                    source="iaqualink",
                    raw_data=data.get("raw_data"),
                )
                print(f"✓ Saved pool reading (ID: {pool_reading_id})")
            else:
                print("⚠ No pool data to save")

        return True, data

    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        logger.exception("Error testing iAqualink API")
        return False, None


def test_span(
    location: Dict[str, Any], api_config: Dict[str, Any], save: bool = False
) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """Test Span Panel API service. Returns (success, data)."""
    from home_monitor.apis.span import SpanPanelClient
    from home_monitor.database import (
        get_span_panel_token_by_host,
        insert_span_circuit_readings,
        insert_span_panel_reading,
    )

    print_section("Testing Span Panel API")

    # Get config
    config = api_config.get("config", {})
    if isinstance(config, str):
        config = json.loads(config)

    panel_host = config.get("panel_host")
    panel_name = config.get("panel_name", panel_host)

    if not panel_host:
        print("❌ ERROR: Span panel_host not configured")
        return False, None

    print(f"Location: {location['name']}")
    print(f"Panel Host: {panel_host}")
    print(f"Panel Name: {panel_name}")

    # Get token from database
    token_data = get_span_panel_token_by_host(panel_host)
    if not token_data:
        print(f"\n⚠️  No token found for panel at {panel_host}")
        print("   Testing without authentication (status endpoint only)...")
        print(f"   To register: make span-register HOST={panel_host}")
        token = None
    else:
        token = token_data.get("token")
        print(f"Panel Serial: {token_data.get('panel_serial')}")

    try:
        # Fetch data
        print("\n📡 Fetching data from Span Panel...")
        client = SpanPanelClient(
            panel_host=panel_host,
            token=token,
            panel_name=panel_name,
        )

        # Check connectivity first
        if not client.check_connection():
            print(f"\n❌ ERROR: Cannot reach panel at {panel_host}")
            return False, None

        # Get panel data
        data = client.fetch_current_data()

        # Display normalized panel data
        print_section("Panel Data")
        print_data("Timestamp", data.get("timestamp"))
        print_data("Panel Serial", data.get("panel_serial"))
        print("\nPower Data:")
        print_data("  Grid Power (W)", data.get("instant_grid_power_w"))
        print_data("  Feedthrough Power (W)", data.get("feedthrough_power_w"))
        print("\nPanel State:")
        print_data("  Main Relay", data.get("main_relay_state"))
        print_data("  DSM Grid State", data.get("dsm_grid_state"))
        print_data("  DSM State", data.get("dsm_state"))
        print_data("  Run Config", data.get("current_run_config"))
        print("\nSystem Info:")
        print_data("  Door State", data.get("door_state"))
        print_data("  Firmware", data.get("firmware_version"))
        print_data("  Uptime (seconds)", data.get("uptime_seconds"))
        print("\nNetwork:")
        print_data("  Ethernet", "Connected" if data.get("eth0_link") else "Disconnected")
        print_data("  WiFi", "Connected" if data.get("wlan_link") else "Disconnected")
        print_data("  Cellular", "Connected" if data.get("wwan_link") else "Disconnected")
        if data.get("battery_soe_percent") is not None:
            print("\nBattery:")
            print_data("  State of Charge (%)", data.get("battery_soe_percent"))

        # Get circuit data if we have a token
        circuits = []
        if token:
            try:
                circuits = client.fetch_circuit_data()
                print_section("Circuit Summary")
                print(f"Total circuits: {len(circuits)}")

                if circuits:
                    # Calculate totals and show top circuits
                    total_power = sum(c.get("instant_power_w", 0) or 0 for c in circuits)
                    print(f"Total circuit power: {total_power:.1f}W")

                    # Sort by power and show top 10
                    sorted_circuits = sorted(
                        circuits,
                        key=lambda c: abs(c.get("instant_power_w", 0) or 0),
                        reverse=True,
                    )

                    print("\nTop 10 circuits by power:")
                    for i, c in enumerate(sorted_circuits[:10]):
                        name = c.get("circuit_name", c.get("circuit_id", "?"))
                        power = c.get("instant_power_w", 0) or 0
                        state = c.get("relay_state", "?")
                        priority = c.get("priority", "?")
                        print(f"  {i + 1:2}. {name:<25} {power:>8.1f}W  [{state}, {priority}]")
            except Exception as e:
                print(f"\n⚠️  Could not fetch circuit data: {e}")

        # Add circuits to data for display
        data["circuits"] = circuits
        data["circuit_count"] = len(circuits)

        # Save to database if requested
        if save:
            if location.get("id") == 0:
                print("❌ ERROR: Cannot save to database - no valid location ID")
                print("   Use database location or set DATABASE_URL")
                return False, data

            print_section("Saving to Database")
            panel_serial = data.get("panel_serial")
            if not panel_serial:
                print("⚠ Cannot save: panel serial not available")
            else:
                # Save panel reading
                if data.get("instant_grid_power_w") is not None:
                    reading_id = insert_span_panel_reading(
                        location_id=location["id"],
                        panel_serial=panel_serial,
                        timestamp=data["timestamp"],
                        instant_grid_power_w=data.get("instant_grid_power_w"),
                        feedthrough_power_w=data.get("feedthrough_power_w"),
                        main_relay_state=data.get("main_relay_state"),
                        dsm_grid_state=data.get("dsm_grid_state"),
                        dsm_state=data.get("dsm_state"),
                        current_run_config=data.get("current_run_config"),
                        door_state=data.get("door_state"),
                        firmware_version=data.get("firmware_version"),
                        uptime_seconds=data.get("uptime_seconds"),
                        battery_soe_percent=data.get("battery_soe_percent"),
                        eth0_link=data.get("eth0_link"),
                        wlan_link=data.get("wlan_link"),
                        wwan_link=data.get("wwan_link"),
                        source="span",
                        raw_data=data.get("raw_data"),
                    )
                    print(f"✓ Saved panel reading (ID: {reading_id})")

                # Save circuit readings
                if circuits:
                    count = insert_span_circuit_readings(
                        location_id=location["id"],
                        panel_serial=panel_serial,
                        timestamp=data["timestamp"],
                        circuits=circuits,
                        source="span",
                    )
                    print(f"✓ Saved {count} circuit readings")

        return True, data

    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        logger.exception("Error testing Span API")
        return False, None


def show_raw_data(data: Dict[str, Any], show_raw: bool = True):
    """Display raw API response data."""
    if not show_raw:
        return

    if "raw_data" in data and data["raw_data"]:
        print_section("Raw API Response")
        print(json.dumps(data["raw_data"], indent=2, default=str))


def find_location_and_config(
    service_name: str,
    location_name: Optional[str] = None,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    station_id: Optional[int] = None,
    energy_site_id: Optional[str] = None,
    system_id: Optional[int] = None,
    device_id: Optional[str] = None,
    panel_host: Optional[str] = None,
    require_db: bool = True,
) -> tuple:
    """
    Find location and API config for the specified service from sites.json.

    Args:
        service_name: Name of the service to test
        location_name: Optional site name to filter by
        latitude: Optional latitude for testing without database (overrides config)
        longitude: Optional longitude for testing without database (overrides config)
        station_id: Optional station ID for testing without database (overrides config)
        energy_site_id: Optional energy site ID for testing without database (overrides config)
        system_id: Optional system ID for testing without database (overrides config)
        device_id: Optional device ID for testing without database (Flume/Rachio)
        panel_host: Optional panel host for testing Span panel
        require_db: If False, allow testing without database when required params are provided

    Returns:
        Tuple of (location_dict, api_config_dict) or (None, None) if not found
    """
    # Load site configuration
    try:
        sites = get_sites()
    except FileNotFoundError as e:
        print(f"❌ ERROR: {e}")
        print("   Create a sites.json file in the project root")
        return None, None
    except Exception as e:
        print(f"❌ ERROR: Failed to load site configuration: {e}")
        return None, None

    if not sites:
        print("❌ ERROR: No sites found in sites.json")
        return None, None

    # Use site name from argument or first available site
    site_name = location_name
    if not site_name:
        site_name = list(sites.keys())[0]

    site_config = get_site(site_name)
    if not site_config:
        print(f"❌ ERROR: Site '{site_name}' not found in configuration")
        print("   Available sites:")
        for name in sites.keys():
            print(f"     - {name}")
        return None, None

    # Check if service is configured for this site
    if service_name == "tempest":
        if "tempest" not in site_config:
            print(f"❌ ERROR: Tempest not configured for site '{site_name}'")
            return None, None
        tempest_config = site_config["tempest"]
        station_id_from_config = tempest_config.get("station_id")
        if station_id is None:
            station_id = station_id_from_config
        mock_config = {
            "id": 0,
            "location_id": 0,
            "api_type": "tempest",
            "enabled": True,
            "config": {"station_id": station_id or station_id_from_config},
        }
    elif service_name == "openweather":
        if "openweather" not in site_config:
            print(f"❌ ERROR: OpenWeather not configured for site '{site_name}'")
            return None, None
        openweather_config = site_config["openweather"]
        if latitude is None:
            latitude = openweather_config["latitude"]
        if longitude is None:
            longitude = openweather_config["longitude"]
        mock_config = {
            "id": 0,
            "location_id": 0,
            "api_type": "openweather",
            "enabled": True,
            "config": {},
        }
    elif service_name == "enphase":
        if "enphase" not in site_config:
            print(f"❌ ERROR: Enphase not configured for site '{site_name}'")
            return None, None
        enphase_config = site_config["enphase"]
        site_id_from_config = enphase_config.get("site_id")
        if system_id is None:
            system_id = site_id_from_config
        mock_config = {
            "id": 0,
            "location_id": 0,
            "api_type": "enphase",
            "enabled": True,
            "config": {"system_id": system_id or site_id_from_config},
        }
    elif service_name == "tesla":
        if "tesla" not in site_config:
            print(f"❌ ERROR: Tesla not configured for site '{site_name}'")
            return None, None
        tesla_config = site_config["tesla"]
        site_ids = tesla_config.get("site_ids", [])
        if not site_ids:
            print(f"❌ ERROR: Tesla site_ids not configured for site '{site_name}'")
            return None, None
        if energy_site_id is None:
            # Use first site_id if not specified
            energy_site_id = str(site_ids[0])
        mock_config = {
            "id": 0,
            "location_id": 0,
            "api_type": "tesla",
            "enabled": True,
            "config": {"energy_site_id": energy_site_id},
        }
    elif service_name == "flume":
        if "flume" not in site_config:
            print(f"❌ ERROR: Flume not configured for site '{site_name}'")
            return None, None
        flume_config = site_config["flume"]
        device_id_from_config = flume_config.get("device_id")
        if device_id is None:
            device_id = device_id_from_config
        if not device_id:
            print(f"❌ ERROR: Flume device_id not configured for site '{site_name}'")
            return None, None
        mock_config = {
            "id": 0,
            "location_id": 0,
            "api_type": "flume",
            "enabled": True,
            "config": {"device_id": device_id},
        }
    elif service_name == "rachio":
        if "rachio" not in site_config:
            print(f"❌ ERROR: Rachio not configured for site '{site_name}'")
            return None, None
        rachio_config = site_config["rachio"]
        device_id_from_config = rachio_config.get("device_id")
        if device_id is None:
            device_id = device_id_from_config
        if not device_id:
            print(f"❌ ERROR: Rachio device_id not configured for site '{site_name}'")
            return None, None
        mock_config = {
            "id": 0,
            "location_id": 0,
            "api_type": "rachio",
            "enabled": True,
            "config": {"device_id": device_id},
        }
    elif service_name == "tankutility":
        if "tankutility" not in site_config:
            print(f"❌ ERROR: Tank Utility not configured for site '{site_name}'")
            return None, None
        tankutility_config = site_config["tankutility"]
        device_id_from_config = tankutility_config.get("device_id")
        if device_id is None:
            device_id = device_id_from_config
        if not device_id:
            print(f"❌ ERROR: Tank Utility device_id not configured for site '{site_name}'")
            return None, None
        mock_config = {
            "id": 0,
            "location_id": 0,
            "api_type": "tankutility",
            "enabled": True,
            "config": {"device_id": device_id},
        }
    elif service_name == "iaqualink":
        # iAqualink doesn't require site config - credentials come from env vars
        iaqualink_config = site_config.get("iaqualink", {})
        serial_from_config = iaqualink_config.get("serial_number")
        device_name_from_config = iaqualink_config.get("device_name")
        mock_config = {
            "id": 0,
            "location_id": 0,
            "api_type": "iaqualink",
            "enabled": True,
            "config": {
                "serial_number": serial_from_config,
                "device_name": device_name_from_config,
            },
        }
    elif service_name == "span":
        # Span panel - get first panel from config or use --panel-host
        span_config = site_config.get("span", {})
        panels = span_config.get("panels", [])
        panel_host_from_config = None
        panel_name_from_config = None
        if panels:
            first_panel = panels[0]
            panel_host_from_config = first_panel.get("host")
            panel_name_from_config = first_panel.get("name", panel_host_from_config)
        # Allow --panel-host to override config
        effective_panel_host = panel_host or panel_host_from_config
        effective_panel_name = panel_name_from_config or effective_panel_host
        if not effective_panel_host:
            print(f"❌ ERROR: Span panel not configured for site '{site_name}'")
            print("   Either add span config to sites.json or use --panel-host")
            return None, None
        mock_config = {
            "id": 0,
            "location_id": 0,
            "api_type": "span",
            "enabled": True,
            "config": {
                "panel_host": effective_panel_host,
                "panel_name": effective_panel_name,
            },
        }
    else:
        print(f"❌ ERROR: Unknown service: {service_name}")
        return None, None

    # Get location_id from database if saving
    location_id = 0
    db_available = bool(os.getenv("DATABASE_URL"))
    if require_db or db_available:
        try:
            location_id = ensure_site_in_database(site_name)
        except Exception as e:
            if require_db:
                print(f"❌ ERROR: Failed to ensure site in database: {e}")
                return None, None
            # If not required, continue with location_id=0

    # Create location dict
    openweather_config = site_config.get("openweather", {})
    mock_location = {
        "id": location_id,
        "name": site_name,
        "latitude": openweather_config.get("latitude"),
        "longitude": openweather_config.get("longitude"),
        "timezone": None,
    }

    return mock_location, mock_config


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Test individual API services independently",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/test_service.py tesla
  python scripts/test_service.py tesla --energy-site-id <energy_site_id>  # Test without database
  python scripts/test_service.py enphase --location "Home"
  python scripts/test_service.py enphase --system-id <system_id>  # Test without database
  python scripts/test_service.py openweather --save-to-db
  python scripts/test_service.py openweather --lat 37.7749 --lon -122.4194  # Test without database
  python scripts/test_service.py tempest --station-id 35943  # Test without database
  python scripts/test_service.py tempest --hide-raw
  python scripts/test_service.py flume --location "FL"
  python scripts/test_service.py flume --device-id <device_id>  # Test without database
  python scripts/test_service.py rachio --location "FL"
  python scripts/test_service.py rachio --device-id <device_id>  # Test without database
  python scripts/test_service.py tankutility --location "FL"
  python scripts/test_service.py tankutility --device-id <device_id>  # Test without database
        """,
    )
    parser.add_argument(
        "service",
        choices=[
            "tesla",
            "enphase",
            "openweather",
            "tempest",
            "flume",
            "rachio",
            "tankutility",
            "iaqualink",
            "span",
        ],
        help="Service to test",
    )
    parser.add_argument(
        "--location",
        help="Location name (optional, uses first available location if not specified)",
    )
    parser.add_argument(
        "--save-to-db",
        action="store_true",
        dest="save",
        help="Save fetched data to database (default: False, just test and display)",
    )
    parser.add_argument(
        "--hide-raw",
        action="store_true",
        dest="no_raw",
        help="Don't display raw API response data (default: False, shows raw response)",
    )
    parser.add_argument(
        "--lat",
        type=float,
        help="Latitude for testing without database (OpenWeather only)",
    )
    parser.add_argument(
        "--lon",
        type=float,
        help="Longitude for testing without database (OpenWeather only)",
    )
    parser.add_argument(
        "--station-id",
        type=int,
        help="Station ID for testing without database (Tempest only)",
    )
    parser.add_argument(
        "--energy-site-id",
        type=str,
        help="Energy Site ID for testing without database (Tesla only)",
    )
    parser.add_argument(
        "--system-id",
        type=int,
        help="System ID for testing without database (Enphase only)",
    )
    parser.add_argument(
        "--device-id",
        type=str,
        help="Device ID for testing without database (Flume/Rachio only)",
    )
    parser.add_argument(
        "--panel-host",
        type=str,
        help="Panel host for testing Span panel",
    )

    args = parser.parse_args()

    # Find location and config
    # Don't require database if --save-to-db is not set
    require_db = args.save
    location, api_config = find_location_and_config(
        args.service,
        args.location,
        latitude=args.lat,
        longitude=args.lon,
        station_id=args.station_id,
        energy_site_id=args.energy_site_id,
        system_id=args.system_id,
        device_id=args.device_id,
        panel_host=args.panel_host,
        require_db=require_db,
    )
    if not location or not api_config:
        return 1

    # Test the service
    show_raw = not args.no_raw
    success = False
    data = None

    try:
        if args.service == "tesla":
            success, data = test_tesla(location, api_config, args.save)
        elif args.service == "enphase":
            success, data = test_enphase(location, api_config, args.save)
        elif args.service == "openweather":
            success, data = test_openweather(location, api_config, args.save)
        elif args.service == "tempest":
            success, data = test_tempest(location, api_config, args.save)
        elif args.service == "flume":
            success, data = test_flume(location, api_config, args.save)
        elif args.service == "rachio":
            success, data = test_rachio(location, api_config, args.save)
        elif args.service == "tankutility":
            success, data = test_tankutility(location, api_config, args.save)
        elif args.service == "iaqualink":
            success, data = test_iaqualink(location, api_config, args.save)
        elif args.service == "span":
            success, data = test_span(location, api_config, args.save)

        # Show raw data if requested and available
        if success and data:
            show_raw_data(data, show_raw)

    except KeyboardInterrupt:
        print("\n\n⚠ Interrupted by user")
        return 130
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        logger.exception("Unexpected error")
        return 1

    print_section("Summary")
    if success:
        print("✓ Test completed successfully")
        if not args.save:
            print("  (Data was not saved to database. Use --save to persist data)")
    else:
        print("✗ Test failed")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
