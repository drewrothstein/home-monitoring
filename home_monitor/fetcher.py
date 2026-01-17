"""
Main data fetcher service that orchestrates data collection from all APIs.
"""

import logging
from datetime import datetime, timezone

import psutil

from home_monitor.apis.enphase import EnphaseApiClient
from home_monitor.apis.flume import FlumeApiClient
from home_monitor.apis.iaqualink import IAqualinkApiClient
from home_monitor.apis.openweather import OpenWeatherApiClient
from home_monitor.apis.rachio import RachioApiClient
from home_monitor.apis.tankutility import TankUtilityApiClient
from home_monitor.apis.tempest import TempestApiClient
from home_monitor.apis.tesla import TeslaApiClient
from home_monitor.config import (
    get_enphase_credentials,
    get_enphase_fetch_interval_cycles,
    get_flume_credentials,
    get_iaqualink_credentials,
    get_openweather_api_key,
    get_rachio_credentials,
    get_span_circuit_fetch_interval_minutes,
    get_tankutility_credentials,
    get_tempest_credentials,
    get_tesla_credentials,
)
from home_monitor.database import (
    get_battery_bank,
    get_battery_banks_by_energy_site,
    get_enphase_gateway_token,
    get_last_span_circuit_reading_time,
    get_span_panel_token_by_host,
    get_sprinkler_run_exists,
    init_database,
    insert_battery_reading,
    insert_enphase_local_reading,
    insert_irradiance_reading,
    insert_or_update_battery_bank,
    insert_pool_reading,
    insert_power_reading,
    insert_propane_reading,
    insert_span_circuit_readings,
    insert_span_panel_reading,
    insert_sprinkler_run,
    insert_system_reading,
    insert_water_reading,
    upsert_span_panel_token,
)
from home_monitor.site_config import (
    ensure_site_in_database,
    get_sites,
)
from home_monitor.token_manager import (
    refresh_enphase_token_if_needed,
    refresh_flume_token_if_needed,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def _sync_tesla_battery_banks(site_name: str, location_id: int, energy_site_id: str, client):
    """
    Synchronize battery bank metadata from Tesla site_info API.

    Args:
        site_name: Site name for logging
        location_id: Database location ID
        energy_site_id: Tesla energy site ID
        client: TeslaApiClient instance
    """
    try:
        logger.info(f"[Teslemetry] Fetching site_info for {site_name}, Site ID: {energy_site_id}")
        site_info = client.get_site_info()
        response_data = site_info.get("response", {})
        battery_count = response_data.get("battery_count", 0)
        components = response_data.get("components", {})

        if not isinstance(components, dict):
            logger.warning(
                f"[Teslemetry] components is not a dict for {site_name}, Site ID: {energy_site_id}: "
                f"type={type(components)}"
            )
            components = {}

        # Extract batteries from components.batteries or components.gateways (Powerwall 3)
        batteries_info = []
        if "batteries" in components:
            batteries_info = components["batteries"]
            if not isinstance(batteries_info, list):
                batteries_info = [batteries_info] if batteries_info else []
        elif "gateways" in components and isinstance(components["gateways"], list):
            batteries_info = [
                g
                for g in components["gateways"]
                if isinstance(g, dict)
                and g.get("part_type") in [2, 4]
                and "Powerwall" in g.get("part_name", "")
            ]

        # Create battery bank entries
        if battery_count > 0 and not batteries_info:
            logger.info(
                f"[Teslemetry] Found battery_count={battery_count} but no battery details. "
                f"Creating {battery_count} battery bank entries."
            )
            for idx in range(battery_count):
                insert_or_update_battery_bank(
                    location_id=location_id,
                    energy_site_id=energy_site_id,
                    battery_index=idx,
                    name=f"Battery {idx}",
                    capacity_kwh=None,
                    raw_data=None,
                )
        elif batteries_info:
            for idx, battery_info in enumerate(batteries_info):
                battery_name = (
                    battery_info.get("part_name")
                    or battery_info.get("serial_number")
                    or f"Battery {idx}"
                )
                # Get nameplate_energy, with fallbacks
                nameplate_energy_wh = battery_info.get("nameplate_energy")
                if not nameplate_energy_wh and battery_count > 0:
                    site_nameplate = response_data.get("nameplate_energy")
                    if site_nameplate:
                        nameplate_energy_wh = int(site_nameplate / battery_count)
                if not nameplate_energy_wh:
                    nameplate_energy_wh = 13500  # Default 13.5 kWh for Powerwall 2/3
                insert_or_update_battery_bank(
                    location_id=location_id,
                    energy_site_id=energy_site_id,
                    battery_index=idx,
                    name=battery_name,
                    capacity_kwh=nameplate_energy_wh / 1000.0,
                    serial_number=battery_info.get("serial_number"),
                    part_number=battery_info.get("part_number"),
                    raw_data=battery_info,
                )
                logger.info(
                    f"[Teslemetry] Stored battery bank: {battery_name} (index {idx}, serial: {battery_info.get('serial_number')}) for site {site_name}"
                )
        else:
            logger.warning(
                f"[Teslemetry] No batteries found in site_info for {site_name}, Site ID: {energy_site_id}. "
                f"battery_count={battery_count}"
            )
    except Exception as e:
        logger.warning(
            f"[Teslemetry] Failed to fetch site_info for {site_name}, Site ID: {energy_site_id}: {e}. "
            f"Continuing with live_status data only."
        )


def fetch_tesla_data(site_name: str, site_config: dict, location_id: int, energy_site_id: str):
    """
    Fetch data from Tesla API and store in database.

    Args:
        site_name: Site name (e.g., "NY", "FL")
        site_config: Site configuration dictionary
        location_id: Database location ID
        energy_site_id: Tesla energy site ID
    """
    try:
        # Get credentials from environment variables
        api_key, _ = get_tesla_credentials()
        if not api_key:
            logger.warning(
                f"[Teslemetry] API key not found in environment variables for site {site_name}"
            )
            return

        logger.info(f"[Teslemetry] Fetching data for {site_name}, Site ID: {energy_site_id}")
        client = TeslaApiClient(access_token=api_key, energy_site_id=energy_site_id)

        # Sync battery bank metadata if we don't have it yet
        if not get_battery_banks_by_energy_site(energy_site_id):
            _sync_tesla_battery_banks(site_name, location_id, energy_site_id, client)

        data = client.fetch_current_data()

        # Store power readings
        if data.get("power_produced") is not None or data.get("power_consumed") is not None:
            insert_power_reading(
                location_id=location_id,
                timestamp=data["timestamp"],
                power_produced=data.get("power_produced"),
                power_consumed=data.get("power_consumed"),
                power_exported=data.get("power_exported"),
                power_imported=data.get("power_imported"),
                source="tesla",
                raw_data=data.get("raw_data"),
            )

        # Store battery readings - handle both single and multiple batteries
        batteries = data.get("batteries", [])
        if batteries and isinstance(batteries, list):
            # Multiple batteries - store individual readings
            for battery in batteries:
                battery_index = battery.get("index")
                battery_bank_id = None
                if battery_index is not None:
                    battery_bank = get_battery_bank(location_id, energy_site_id, battery_index)
                    battery_bank_id = battery_bank["id"] if battery_bank else None

                battery_power = battery.get("battery_power", 0)
                insert_battery_reading(
                    location_id=location_id,
                    timestamp=data["timestamp"],
                    energy_charged=battery.get("energy_left"),
                    state_of_charge=battery.get("percentage_charged"),
                    power_charging=battery_power if battery_power > 0 else None,
                    power_discharging=abs(battery_power) if battery_power < 0 else None,
                    source="tesla",
                    raw_data={
                        "battery": battery,
                        "response": data.get("raw_data", {}).get("response", {}),
                    },
                    battery_bank_id=battery_bank_id,
                )
        elif data.get("battery_energy") is not None or data.get("battery_soc") is not None:
            # Single battery or legacy format - store aggregate reading
            battery_power = data.get("battery_power", 0)
            insert_battery_reading(
                location_id=location_id,
                timestamp=data["timestamp"],
                energy_charged=data.get("battery_energy"),
                state_of_charge=data.get("battery_soc"),
                power_charging=battery_power if battery_power > 0 else None,
                power_discharging=abs(battery_power) if battery_power < 0 else None,
                source="tesla",
                raw_data=data.get("raw_data"),
            )

        logger.info(
            f"[Teslemetry] Successfully fetched data for {site_name}, Site ID: {energy_site_id}"
        )

    except Exception as e:
        logger.error(f"[Teslemetry] Error fetching data for {site_name}: {e}", exc_info=True)


def fetch_enphase_data(site_name: str, site_config: dict, location_id: int):
    """
    Fetch data from Enphase API and store in database.

    Supports multiple Enphase apps with automatic rotation and failover.
    If one app fails (rate limit, auth error), automatically tries the next app.

    Args:
        site_name: Site name (e.g., "NY", "FL")
        site_config: Site configuration dictionary
        location_id: Database location ID
    """
    try:
        # Check if Enphase is configured for this site
        enphase_config = site_config.get("enphase")
        if not enphase_config:
            logger.debug(f"[Enphase] Not configured for site {site_name}")
            return

        # Get system_id from site config (site_id in config maps to system_id for API)
        system_id = enphase_config.get("site_id")

        # Try to use multi-app rotation with failover
        from home_monitor.enphase_app_manager import EnphaseAppRotator, get_enphase_app_count

        app_count = get_enphase_app_count()

        if app_count > 0:
            # Multi-app mode (or single app in new format)
            rotator = EnphaseAppRotator()
            data = None
            last_error = None

            while rotator.has_more_apps():
                app = rotator.get_current_app()
                if not app:
                    logger.warning(f"[Enphase] No apps available for {site_name}")
                    break

                if not app.access_token:
                    logger.warning(
                        f"[Enphase] App {app.app_index} has no access token. "
                        f"Run 'make enphase-exchange APP={app.app_index}' to set up."
                    )
                    rotator.mark_failure(Exception("No access token"))
                    continue

                try:
                    client = EnphaseApiClient(
                        access_token=app.access_token,
                        api_key=app.api_key,
                        system_id=system_id,
                    )
                    data = client.fetch_current_data(include_energy_telemetry=True)
                    rotator.mark_success()
                    logger.debug(f"[Enphase] App {app.app_index} succeeded for {site_name}")
                    break

                except Exception as api_error:
                    last_error = api_error
                    rotator.mark_failure(api_error)

                    if rotator.should_retry(api_error):
                        logger.info(
                            f"[Enphase] App {app.app_index} failed, trying next app: {api_error}"
                        )
                        continue
                    else:
                        # Non-retryable error
                        raise

            if data is None and last_error:
                raise last_error
            elif data is None:
                logger.warning(f"[Enphase] No data fetched for {site_name} - all apps exhausted")
                return

        else:
            # Legacy single-app mode (backward compatibility)
            access_token, _ = refresh_enphase_token_if_needed()
            if not access_token:
                access_token, _ = get_enphase_credentials()

            _, api_key = get_enphase_credentials()

            if not access_token or not api_key:
                logger.warning(
                    f"[Enphase] Credentials not found for site {site_name} (location_id={location_id})"
                )
                return

            client = EnphaseApiClient(
                access_token=access_token,
                api_key=api_key,
                system_id=system_id,
            )

            try:
                data = client.fetch_current_data(include_energy_telemetry=True)
            except Exception as api_error:
                error_str = str(api_error)
                if any(x in error_str for x in ["401", "403", "Unauthorized"]):
                    logger.info(f"[Enphase] Auth error, attempting token refresh for {site_name}")
                    access_token, was_refreshed = refresh_enphase_token_if_needed()
                    if was_refreshed and access_token:
                        client = EnphaseApiClient(
                            access_token=access_token, api_key=api_key, system_id=system_id
                        )
                        data = client.fetch_current_data(include_energy_telemetry=True)
                    else:
                        raise
                else:
                    raise

        # Store power readings (insert if we have any power data, not just power_produced)
        if (
            data.get("power_produced") is not None
            or data.get("power_consumed") is not None
            or data.get("power_exported") is not None
            or data.get("power_imported") is not None
            or data.get("energy_imported_kwh") is not None
            or data.get("energy_exported_kwh") is not None
        ):
            insert_power_reading(
                location_id=location_id,
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
            logger.info(
                f"[Enphase] Successfully fetched data for {site_name} "
                f"(produced={data.get('power_produced')}, consumed={data.get('power_consumed')}, "
                f"energy_imported={data.get('energy_imported_kwh')}, energy_exported={data.get('energy_exported_kwh')})"
            )
        else:
            logger.warning(
                f"[Enphase] No power data returned for {site_name}. "
                f"Raw data keys: {list(data.get('raw_data', {}).keys()) if data.get('raw_data') else 'None'}"
            )

    except Exception as e:
        logger.error(f"[Enphase] Error fetching data for {site_name}: {e}", exc_info=True)


def fetch_enphase_local_data(site_name: str, site_config: dict, location_id: int):
    """
    Fetch data from Enphase IQ Gateway local APIs and store in database.

    This provides richer data than the cloud API, including consumption data,
    grid readings, and detailed meter information. Requires network access to
    the gateway on the local network.

    Tokens are automatically refreshed when they are within 30 days of expiration,
    provided ENPHASE_ENLIGHTEN_USERNAME and ENPHASE_ENLIGHTEN_PASSWORD are configured.

    Args:
        site_name: Site name (e.g., "NY", "FL")
        site_config: Site configuration dictionary
        location_id: Database location ID
    """
    from home_monitor.apis.enphase_local import EnphaseLocalClient, check_and_refresh_token

    try:
        # Check if local gateway is configured for this site
        enphase_local_config = site_config.get("enphase_local")
        if not enphase_local_config:
            logger.debug(f"[EnphaseLocal] Not configured for site {site_name}")
            return

        gateways = enphase_local_config.get("gateways", [])
        if not gateways:
            logger.warning(f"[EnphaseLocal] No gateways configured for site {site_name}")
            return

        for gateway_config in gateways:
            gateway_serial = gateway_config.get("serial")
            gateway_host = gateway_config.get("host")

            if not gateway_serial or not gateway_host:
                logger.warning(
                    f"[EnphaseLocal] Gateway config missing serial or host for site {site_name}"
                )
                continue

            # Get token from database
            token_data = get_enphase_gateway_token(gateway_serial)
            if not token_data:
                logger.warning(
                    f"[EnphaseLocal] No token found for gateway {gateway_serial}. "
                    f"Run 'make enphase-gateway-store SERIAL={gateway_serial} HOST={gateway_host} TOKEN=...' to store a token."
                )
                continue

            token = token_data.get("token")
            expires_at = token_data.get("token_expires_at")
            # Use host from config (may differ from stored host if IP changed)
            effective_host = gateway_host or token_data.get("gateway_host")

            # Check if token needs refresh and refresh automatically if credentials available
            token, expires_at = check_and_refresh_token(
                gateway_serial=gateway_serial,
                gateway_host=effective_host,
                current_token=token,
                expires_at=expires_at,
            )

            logger.info(
                f"[EnphaseLocal] Fetching data from gateway {gateway_serial} at {effective_host} for {site_name}"
            )

            try:
                client = EnphaseLocalClient(
                    gateway_host=effective_host,
                    token=token,
                    gateway_serial=gateway_serial,
                )

                data = client.fetch_current_data()

                # Store the reading
                insert_enphase_local_reading(
                    location_id=location_id,
                    gateway_serial=gateway_serial,
                    timestamp=data["timestamp"],
                    power_produced=data.get("power_produced"),
                    power_consumed=data.get("power_consumed"),
                    power_net=data.get("power_net"),
                    grid_voltage_l1=data.get("grid_voltage_l1"),
                    grid_voltage_l2=data.get("grid_voltage_l2"),
                    grid_frequency=data.get("grid_frequency"),
                    energy_produced_today_wh=data.get("energy_produced_today_wh"),
                    energy_consumed_today_wh=data.get("energy_consumed_today_wh"),
                    energy_lifetime_wh=data.get("energy_lifetime_wh"),
                    source="enphase_local",
                    raw_data=data.get("raw_data"),
                )

                logger.info(
                    f"[EnphaseLocal] Successfully fetched data from gateway {gateway_serial} for {site_name} "
                    f"(produced={data.get('power_produced')}W, consumed={data.get('power_consumed')}W, "
                    f"net={data.get('power_net')}W)"
                )

            except Exception as gateway_error:
                logger.error(
                    f"[EnphaseLocal] Error fetching from gateway {gateway_serial}: {gateway_error}"
                )
                continue

    except Exception as e:
        logger.error(f"[EnphaseLocal] Error fetching data for {site_name}: {e}", exc_info=True)


def fetch_openweather_data(site_name: str, site_config: dict, location_id: int):
    """
    Fetch data from OpenWeather API and store in database.

    Args:
        site_name: Site name (e.g., "NY", "FL")
        site_config: Site configuration dictionary
        location_id: Database location ID
    """
    try:
        # Get API key from environment variables
        api_key = get_openweather_api_key(site_name)
        if not api_key:
            logger.warning(
                f"[OpenWeather] API key not found in environment variables for site {site_name}"
            )
            return

        # Get coordinates from site config (required)
        openweather_config = site_config.get("openweather", {})
        latitude = openweather_config["latitude"]
        longitude = openweather_config["longitude"]

        client = OpenWeatherApiClient(api_key=api_key)
        data = client.fetch_current_data(
            latitude=latitude,
            longitude=longitude,
            timezone=None,  # Can be added to site config later if needed
        )

        # Store irradiance readings
        if any(
            data.get(k) is not None for k in ["ghi_clear_sky", "ghi_cloudy_sky", "dni_clear_sky"]
        ):
            insert_irradiance_reading(
                location_id=location_id,
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
            logger.info(f"[OpenWeather] Successfully fetched data for {site_name}")
        else:
            logger.warning(f"[OpenWeather] No irradiance data returned for {site_name}")

    except Exception as e:
        logger.error(f"[OpenWeather] Error fetching data for {site_name}: {e}", exc_info=True)


def fetch_tempest_data(site_name: str, site_config: dict, location_id: int):
    """
    Fetch data from Tempest API and store in database.

    Args:
        site_name: Site name (e.g., "NY", "FL")
        site_config: Site configuration dictionary
        location_id: Database location ID
    """
    try:
        # Get credentials from environment variables
        token = get_tempest_credentials(site_name)
        if not token:
            logger.warning(
                f"[Tempest] Token not found in environment variables for site {site_name}"
            )
            return

        # Get station_id from site config (required)
        tempest_config = site_config.get("tempest", {})
        station_id = tempest_config.get("station_id")
        if not station_id:
            logger.warning(f"[Tempest] station_id not configured for site {site_name}")
            return

        client = TempestApiClient(token=token, station_id=station_id)
        data = client.fetch_current_data()

        # Store irradiance readings
        # Tempest provides actual measured solar radiation, which we store in ghi_cloudy_sky
        # (representing actual conditions, not a model)
        if data.get("ghi_cloudy_sky") is not None:
            insert_irradiance_reading(
                location_id=location_id,
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
            logger.info(f"[Tempest] Successfully fetched data for {site_name}")
        else:
            logger.warning(f"[Tempest] No irradiance data returned for {site_name}")

    except Exception as e:
        logger.error(f"[Tempest] Error fetching data for {site_name}: {e}", exc_info=True)


def fetch_flume_data(site_name: str, site_config: dict, location_id: int):
    """
    Fetch data from Flume API and store in database.

    Args:
        site_name: Site name (e.g., "NY", "FL")
        site_config: Site configuration dictionary
        location_id: Database location ID
    """
    try:
        # Check if Flume is configured for this site
        flume_config = site_config.get("flume")
        if not flume_config:
            logger.debug(f"[Flume] Not configured for site {site_name}")
            return

        # Refresh token if needed (checks expiration and refreshes automatically)
        # Tokens are global, not location-specific
        access_token, _ = refresh_flume_token_if_needed()
        if not access_token:
            access_token, _ = get_flume_credentials()

        if not access_token:
            logger.warning(
                f"[Flume] Credentials not found for site {site_name} (location_id={location_id}). "
                "Run 'make flume-token' to set up authentication."
            )
            return

        # Get device_id from flume config, timezone from site level
        device_id = flume_config.get("device_id")
        site_tz = site_config.get("timezone")

        client = FlumeApiClient(
            access_token=access_token,
            device_id=device_id,
            tz=site_tz,
        )

        try:
            data = client.fetch_current_data()
        except Exception as api_error:
            # Check if it's an auth error (token expired) and retry once
            error_str = str(api_error)
            if any(x in error_str for x in ["401", "403", "Unauthorized"]):
                logger.info(f"[Flume] Auth error, attempting token refresh for {site_name}")
                access_token, was_refreshed = refresh_flume_token_if_needed()
                if was_refreshed and access_token:
                    client = FlumeApiClient(
                        access_token=access_token,
                        device_id=device_id,
                        tz=site_tz,
                    )
                    data = client.fetch_current_data()
                else:
                    raise
            else:
                raise

        # Store water readings
        # Store daily usage
        if data.get("usage_today_gallons") is not None:
            insert_water_reading(
                location_id=location_id,
                timestamp=data["timestamp"],
                flow_rate_gpm=data.get("flow_rate_gpm"),
                usage_gallons=data.get("usage_today_gallons"),
                usage_period="day",
                source="flume",
                raw_data=data.get("raw_data"),
            )
            logger.info(
                f"[Flume] Successfully fetched data for {site_name} "
                f"(flow_rate={data.get('flow_rate_gpm')} GPM, "
                f"daily_usage={data.get('usage_today_gallons')} gal)"
            )
        elif data.get("usage_hour_gallons") is not None:
            # Fall back to hourly if daily not available
            insert_water_reading(
                location_id=location_id,
                timestamp=data["timestamp"],
                flow_rate_gpm=data.get("flow_rate_gpm"),
                usage_gallons=data.get("usage_hour_gallons"),
                usage_period="hour",
                source="flume",
                raw_data=data.get("raw_data"),
            )
            logger.info(
                f"[Flume] Successfully fetched hourly data for {site_name} "
                f"(flow_rate={data.get('flow_rate_gpm')} GPM, "
                f"hourly_usage={data.get('usage_hour_gallons')} gal)"
            )
        else:
            logger.warning(
                f"[Flume] No water usage data returned for {site_name}. "
                f"Raw data keys: {list(data.get('raw_data', {}).keys()) if data.get('raw_data') else 'None'}"
            )

    except Exception as e:
        logger.error(f"[Flume] Error fetching data for {site_name}: {e}", exc_info=True)


def fetch_rachio_data(site_name: str, site_config: dict, location_id: int):
    """
    Fetch data from Rachio API and store sprinkler runs in database.

    Args:
        site_name: Site name (e.g., "NY", "FL")
        site_config: Site configuration dictionary
        location_id: Database location ID
    """
    try:
        # Check if Rachio is configured for this site
        rachio_config = site_config.get("rachio")
        if not rachio_config:
            logger.debug(f"[Rachio] Not configured for site {site_name}")
            return

        # Get API key from environment variables
        api_key = get_rachio_credentials()
        if not api_key:
            logger.warning(
                f"[Rachio] API key not found in environment variables for site {site_name}. "
                "Set RACHIO_API_KEY in your .env file."
            )
            return

        # Get device_id from site config
        device_id = rachio_config.get("device_id")
        if not device_id:
            logger.warning(f"[Rachio] device_id not configured for site {site_name}")
            return

        logger.info(f"[Rachio] Fetching data for {site_name}, Device ID: {device_id}")
        client = RachioApiClient(api_key=api_key, device_id=device_id)

        # Fetch watering events from the last 24 hours
        watering_runs = client.fetch_watering_events()

        if not watering_runs:
            logger.info(f"[Rachio] No watering events in last 24h for {site_name}")
            return

        # Store new watering runs (avoid duplicates)
        new_runs = 0
        for run in watering_runs:
            # Check if this run already exists
            if get_sprinkler_run_exists(
                location_id=location_id,
                device_id=device_id,
                zone_id=run["zone_id"],
                start_time=run["start_time"],
            ):
                continue

            # Insert new run
            insert_sprinkler_run(
                location_id=location_id,
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
            new_runs += 1

        if new_runs > 0:
            logger.info(f"[Rachio] Stored {new_runs} new sprinkler runs for {site_name}")
        else:
            logger.info(
                f"[Rachio] No new runs to store for {site_name} "
                f"({len(watering_runs)} runs already in database)"
            )

    except Exception as e:
        logger.error(f"[Rachio] Error fetching data for {site_name}: {e}", exc_info=True)


def fetch_tankutility_data(site_name: str, site_config: dict, location_id: int):
    """
    Fetch data from Tank Utility API and store propane tank readings in database.

    Args:
        site_name: Site name (e.g., "NY", "FL")
        site_config: Site configuration dictionary
        location_id: Database location ID
    """
    try:
        # Check if Tank Utility is configured for this site
        tankutility_config = site_config.get("tankutility")
        if not tankutility_config:
            logger.debug(f"[TankUtility] Not configured for site {site_name}")
            return

        # Get credentials from environment variables
        email, password = get_tankutility_credentials()
        if not email or not password:
            logger.warning(
                f"[TankUtility] Credentials not found for site {site_name}. "
                "Set TANK_UTILITY_EMAIL and TANK_UTILITY_PASSWORD in your .env file."
            )
            return

        # Get device_id from site config, timezone from site level
        device_id = tankutility_config.get("device_id")
        if not device_id:
            logger.warning(f"[TankUtility] device_id not configured for site {site_name}")
            return

        site_tz = site_config.get("timezone")

        logger.info(f"[TankUtility] Fetching data for {site_name}, Device ID: {device_id}")
        client = TankUtilityApiClient(email=email, password=password, tz=site_tz)

        data = client.fetch_current_data(device_id=device_id)

        # Store propane reading
        if data.get("tank_level_percent") is not None:
            insert_propane_reading(
                location_id=location_id,
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
            logger.info(
                f"[TankUtility] Successfully fetched data for {site_name} "
                f"(level={data.get('tank_level_percent'):.1f}%, "
                f"gallons={data.get('tank_level_gallons'):.0f}/{data.get('capacity_gallons'):.0f})"
            )
        else:
            logger.warning(
                f"[TankUtility] No tank level data returned for {site_name}. "
                f"Raw data keys: {list(data.get('raw_data', {}).keys()) if data.get('raw_data') else 'None'}"
            )

    except Exception as e:
        logger.error(f"[TankUtility] Error fetching data for {site_name}: {e}", exc_info=True)


def fetch_iaqualink_data(site_name: str, site_config: dict, location_id: int):
    """
    Fetch data from iAqualink API and store pool readings in database.

    Args:
        site_name: Site name (e.g., "NY", "FL")
        site_config: Site configuration dictionary
        location_id: Database location ID
    """
    try:
        # Check if iAqualink is configured for this site
        iaqualink_config = site_config.get("iaqualink")
        if not iaqualink_config:
            logger.debug(f"[iAqualink] Not configured for site {site_name}")
            return

        # Get credentials from environment variables
        email, password = get_iaqualink_credentials()
        if not email or not password:
            logger.warning(
                f"[iAqualink] Credentials not found for site {site_name}. "
                "Set IAQUALINK_EMAIL and IAQUALINK_PASSWORD in your .env file."
            )
            return

        # Get serial_number or device_name from site config
        serial_number = iaqualink_config.get("serial_number")
        device_name = iaqualink_config.get("device_name")

        logger.info(f"[iAqualink] Fetching data for {site_name}")
        client = IAqualinkApiClient(
            email=email,
            password=password,
            device_name=device_name,
            serial_number=serial_number,
        )

        data = client.fetch_current_data()

        # Get serial number if we don't have it
        if not serial_number:
            devices = client.list_devices()
            if devices:
                serial_number = devices[0].get("serial_number", "unknown")

        # Store pool reading if we have any useful data
        # Note: pool_temp and spa_temp may be empty when pumps are off (especially at night)
        # but we still want to store set points, air temp, and pump/heater status
        has_pool_data = any(
            data.get(key) is not None
            for key in [
                "pool_temp",
                "spa_temp",
                "air_temp",
                "pool_set_point",
                "spa_set_point",
            ]
        )
        if has_pool_data:
            insert_pool_reading(
                location_id=location_id,
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
            logger.info(
                f"[iAqualink] Successfully fetched data for {site_name} "
                f"(pool_temp={data.get('pool_temp')}°F, spa_temp={data.get('spa_temp')}°F, "
                f"air_temp={data.get('air_temp')}°F, pool_pump={'ON' if data.get('pool_pump') else 'OFF'})"
            )
        else:
            logger.warning(
                f"[iAqualink] No pool data returned for {site_name}. "
                f"Raw data keys: {list(data.get('raw_data', {}).keys()) if data.get('raw_data') else 'None'}"
            )

    except Exception as e:
        logger.error(f"[iAqualink] Error fetching data for {site_name}: {e}", exc_info=True)


def fetch_span_data(site_name: str, site_config: dict, location_id: int):
    """
    Fetch data from Span Power Panel local APIs and store in database.

    Panel-level data is fetched every cycle. Circuit-level data is fetched
    at a configurable interval (default: 15 minutes) to reduce storage.

    Args:
        site_name: Site name (e.g., "NY", "FL")
        site_config: Site configuration dictionary
        location_id: Database location ID
    """
    from datetime import timedelta

    from home_monitor.apis.span import SpanPanelClient

    try:
        # Check if Span is configured for this site
        span_config = site_config.get("span")
        if not span_config:
            logger.debug(f"[Span] Not configured for site {site_name}")
            return

        panels = span_config.get("panels", [])
        if not panels:
            logger.warning(f"[Span] No panels configured for site {site_name}")
            return

        # Get circuit fetch interval
        circuit_interval_minutes = get_span_circuit_fetch_interval_minutes()

        for panel_config in panels:
            panel_host = panel_config.get("host")
            panel_name = panel_config.get("name", panel_host)

            if not panel_host:
                logger.warning(f"[Span] Panel config missing host for site {site_name}")
                continue

            # Get token from database
            token_data = get_span_panel_token_by_host(panel_host)
            if not token_data:
                logger.warning(
                    f"[Span] No token found for panel at {panel_host}. "
                    f"Run 'make span-register HOST={panel_host} NAME=\"{panel_name}\"' to register."
                )
                continue

            panel_serial = token_data.get("panel_serial")
            token = token_data.get("token")

            # Update location_id in token if not set (auto-fix for existing tokens)
            if token_data.get("location_id") is None and panel_serial:
                logger.info(
                    f"[Span] Updating location_id for panel {panel_serial} to {location_id}"
                )
                upsert_span_panel_token(
                    panel_serial=panel_serial,
                    panel_host=panel_host,
                    token=token,
                    panel_name=token_data.get("panel_name"),
                    location_id=location_id,
                )

            logger.info(
                f"[Span] Fetching data from panel '{panel_name}' ({panel_serial}) "
                f"at {panel_host} for {site_name}"
            )

            try:
                client = SpanPanelClient(
                    panel_host=panel_host,
                    token=token,
                    panel_name=panel_name,
                )

                # Fetch panel-level data (every cycle)
                data = client.fetch_current_data()

                # Use serial from API if not in token data
                if not panel_serial and data.get("panel_serial"):
                    panel_serial = data["panel_serial"]

                if panel_serial:
                    # Insert panel reading
                    insert_span_panel_reading(
                        location_id=location_id,
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

                    logger.info(
                        f"[Span] Successfully fetched panel data from '{panel_name}' for {site_name} "
                        f"(grid_power={data.get('instant_grid_power_w')}W, "
                        f"door={data.get('door_state')})"
                    )

                    # Check if we should fetch circuit data
                    last_circuit_time = get_last_span_circuit_reading_time(panel_serial)
                    now = datetime.now(timezone.utc)
                    should_fetch_circuits = False

                    if last_circuit_time is None:
                        should_fetch_circuits = True
                    else:
                        # Ensure timezone awareness
                        if last_circuit_time.tzinfo is None:
                            last_circuit_time = last_circuit_time.replace(tzinfo=timezone.utc)
                        time_since_last = now - last_circuit_time
                        if time_since_last >= timedelta(minutes=circuit_interval_minutes):
                            should_fetch_circuits = True

                    if should_fetch_circuits:
                        try:
                            circuits = client.fetch_circuit_data()
                            if circuits:
                                count = insert_span_circuit_readings(
                                    location_id=location_id,
                                    panel_serial=panel_serial,
                                    timestamp=data["timestamp"],
                                    circuits=circuits,
                                    source="span",
                                )
                                logger.info(f"[Span] Fetched {count} circuits from '{panel_name}'")
                            else:
                                logger.warning(f"[Span] No circuits returned for '{panel_name}'")
                        except Exception as circuit_error:
                            logger.error(
                                f"[Span] Error fetching circuits from '{panel_name}': {circuit_error}"
                            )
                    else:
                        logger.debug(
                            f"[Span] Skipping circuit fetch for '{panel_name}' "
                            f"(last fetch was {last_circuit_time})"
                        )
                else:
                    logger.warning(f"[Span] Could not determine panel serial for {panel_host}")

            except Exception as panel_error:
                logger.error(
                    f"[Span] Error fetching from panel '{panel_name}' at {panel_host}: {panel_error}"
                )
                continue

    except Exception as e:
        logger.error(f"[Span] Error fetching data for {site_name}: {e}", exc_info=True)


def fetch_system_stats():
    """
    Fetch system stats (CPU, memory, disk) and store in database.

    This collects stats for the container/host running the fetcher service.
    """
    try:
        # Get CPU usage (averaged over 1 second interval)
        cpu_percent = psutil.cpu_percent(interval=1)

        # Get memory usage
        memory = psutil.virtual_memory()
        memory_percent = memory.percent
        memory_used_mb = memory.used / (1024 * 1024)
        memory_total_mb = memory.total / (1024 * 1024)

        # Get disk usage (root filesystem)
        disk = psutil.disk_usage("/")
        disk_percent = disk.percent
        disk_used_gb = disk.used / (1024 * 1024 * 1024)
        disk_total_gb = disk.total / (1024 * 1024 * 1024)

        insert_system_reading(
            timestamp=datetime.now(timezone.utc),
            cpu_percent=cpu_percent,
            memory_percent=memory_percent,
            memory_used_mb=memory_used_mb,
            memory_total_mb=memory_total_mb,
            disk_percent=disk_percent,
            disk_used_gb=disk_used_gb,
            disk_total_gb=disk_total_gb,
        )

        logger.info(
            f"[System] CPU: {cpu_percent:.1f}%, "
            f"Memory: {memory_percent:.1f}% ({memory_used_mb:.0f}/{memory_total_mb:.0f} MB), "
            f"Disk: {disk_percent:.1f}% ({disk_used_gb:.1f}/{disk_total_gb:.1f} GB)"
        )

    except Exception as e:
        logger.error(f"[System] Error collecting system stats: {e}", exc_info=True)


def fetch_all_data(cycle_count: int = 0):
    """
    Fetch data from all enabled APIs for all sites defined in sites.json.

    Args:
        cycle_count: Current fetch cycle number (used to throttle Enphase API calls)
    """
    try:
        # Get all sites from configuration
        sites = get_sites()

        if not sites:
            logger.warning("No sites found in configuration")
            return

        # Determine if this is an Enphase fetch cycle
        enphase_interval = get_enphase_fetch_interval_cycles()
        is_enphase_cycle = (cycle_count % enphase_interval) == 0

        for site_name, site_config in sites.items():
            # Build list of configured integrations for this site (all are optional)
            integrations = []
            if "tempest" in site_config:
                integrations.append("Tempest")
            if "openweather" in site_config:
                integrations.append("OpenWeather")
            if "enphase" in site_config:
                integrations.append("Enphase")
            if "enphase_local" in site_config:
                gateway_count = len(site_config["enphase_local"].get("gateways", []))
                integrations.append(
                    f"EnphaseLocal({gateway_count})" if gateway_count > 1 else "EnphaseLocal"
                )
            if "tesla" in site_config:
                site_count = len(site_config["tesla"].get("site_ids", []))
                integrations.append(f"Teslemetry({site_count})" if site_count > 1 else "Teslemetry")
            if "flume" in site_config:
                integrations.append("Flume")
            if "rachio" in site_config:
                integrations.append("Rachio")
            if "tankutility" in site_config:
                integrations.append("TankUtility")
            if "iaqualink" in site_config:
                integrations.append("iAqualink")
            if "span" in site_config:
                panel_count = len(site_config["span"].get("panels", []))
                integrations.append(f"Span({panel_count})" if panel_count > 1 else "Span")

            integration_str = ", ".join(integrations) if integrations else "none"
            logger.info(f"Processing site: {site_name} [{integration_str}]")

            # Ensure site exists in database and get location_id
            try:
                location_id = ensure_site_in_database(site_name)
            except Exception as e:
                logger.error(f"Failed to ensure site '{site_name}' in database: {e}")
                continue

            # Fetch Tempest data (optional)
            if "tempest" in site_config:
                fetch_tempest_data(site_name, site_config, location_id)

            # Fetch OpenWeather data (optional)
            if "openweather" in site_config:
                fetch_openweather_data(site_name, site_config, location_id)

            # Fetch Enphase data (optional, throttled to save API quota)
            if "enphase" in site_config:
                if is_enphase_cycle:
                    fetch_enphase_data(site_name, site_config, location_id)
                else:
                    next_cycle = ((cycle_count // enphase_interval) + 1) * enphase_interval
                    logger.info(
                        f"[Enphase] Skipping fetch for {site_name} (cycle {cycle_count}, next at cycle {next_cycle})"
                    )

            # Fetch Enphase local gateway data (optional, not rate-limited)
            # Local gateway APIs are accessed directly on the network, no cloud quota
            if "enphase_local" in site_config:
                fetch_enphase_local_data(site_name, site_config, location_id)

            # Fetch Tesla data (optional, can have multiple site_ids)
            if "tesla" in site_config:
                tesla_config = site_config["tesla"]
                site_ids = tesla_config.get("site_ids", [])
                for energy_site_id in site_ids:
                    fetch_tesla_data(site_name, site_config, location_id, str(energy_site_id))

            # Fetch Flume water data (optional)
            if "flume" in site_config:
                fetch_flume_data(site_name, site_config, location_id)

            # Fetch Rachio sprinkler data (optional)
            if "rachio" in site_config:
                fetch_rachio_data(site_name, site_config, location_id)

            # Fetch Tank Utility propane data (optional)
            if "tankutility" in site_config:
                fetch_tankutility_data(site_name, site_config, location_id)

            # Fetch iAqualink pool data (optional)
            if "iaqualink" in site_config:
                fetch_iaqualink_data(site_name, site_config, location_id)

            # Fetch Span panel data (optional, can have multiple panels)
            if "span" in site_config:
                fetch_span_data(site_name, site_config, location_id)

        # Fetch system stats (always runs, not site-specific)
        fetch_system_stats()

        logger.info("Successfully fetched and stored data for all sites")

    except Exception as e:
        logger.error(f"Error during data fetch: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    """Run the data fetcher when executed as a script."""
    logger.info("Initializing database schema")
    try:
        init_database()
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}", exc_info=True)
        raise

    logger.info("Starting data fetch cycle")
    fetch_all_data()
    logger.info("Data fetch cycle completed")
