"""
Database connection and schema management using raw SQL.
"""

import json
import logging
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
from psycopg2.extras import RealDictCursor

logger = logging.getLogger(__name__)


def get_database_url() -> str:
    """Get database URL from environment variable."""
    url = os.getenv("DATABASE_URL")
    if not url:
        raise ValueError("DATABASE_URL environment variable is required")
    return url


@contextmanager
def get_connection():
    """
    Get a database connection context manager.

    Usage:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM locations")
    """
    url = get_database_url()
    conn = psycopg2.connect(url)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _add_column_if_not_exists(cur, table_name: str, column_name: str, column_type: str) -> None:
    """
    Add a column to a table if it doesn't already exist.

    Args:
        cur: Database cursor
        table_name: Name of the table
        column_name: Name of the column to add
        column_type: SQL type for the column (e.g., 'TEXT', 'DOUBLE PRECISION')
    """
    # Check if column exists (case-insensitive comparison since PostgreSQL stores unquoted identifiers in lowercase)
    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = %s AND LOWER(column_name) = LOWER(%s)
        """,
        (table_name, column_name),
    )
    if not cur.fetchone():
        # Column doesn't exist, add it
        cur.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")


def _add_columns_if_not_exists(cur, table_name: str, columns: List[Tuple[str, str]]) -> None:
    """
    Add multiple columns to a table if they don't already exist.

    Args:
        cur: Database cursor
        table_name: Name of the table
        columns: List of (column_name, column_type) tuples
    """
    for column_name, column_type in columns:
        _add_column_if_not_exists(cur, table_name, column_name, column_type)


def _get_table_columns(cur, table_name: str) -> set:
    """Get all column names for a table (lowercase for case-insensitive matching)."""
    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = %s
        """,
        (table_name,),
    )
    # PostgreSQL stores column names in lowercase, return as-is
    return {row[0] for row in cur.fetchall()}


def _parse_jsonb_field(row_dict: Dict[str, Any], field_name: str) -> None:
    """
    Parse a JSONB field from a row dictionary in-place.

    Args:
        row_dict: Dictionary representing a database row
        field_name: Name of the JSONB field to parse
    """
    value = row_dict.get(field_name)
    if isinstance(value, str):
        try:
            row_dict[field_name] = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            pass


def _row_to_dict(row, jsonb_fields: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Convert a database row to a dictionary and parse JSONB fields.

    Args:
        row: Database row (from RealDictCursor or regular cursor)
        jsonb_fields: Optional list of JSONB field names to parse

    Returns:
        Dictionary representation of the row with parsed JSONB fields
    """
    row_dict = dict(row)
    if jsonb_fields:
        for field in jsonb_fields:
            _parse_jsonb_field(row_dict, field)
    return row_dict


def _rows_to_dicts(rows, jsonb_fields: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """
    Convert multiple database rows to dictionaries and parse JSONB fields.

    Args:
        rows: Iterable of database rows
        jsonb_fields: Optional list of JSONB field names to parse

    Returns:
        List of dictionary representations of rows with parsed JSONB fields
    """
    return [_row_to_dict(row, jsonb_fields) for row in rows]


def _insert_with_flattened_raw_data(
    cur,
    table_name: str,
    base_columns: Dict[str, Any],
    raw_data: Optional[Dict[str, Any]],
) -> int:
    """
    Insert a row with flattened raw_data fields.

    Args:
        cur: Database cursor
        table_name: Name of the table
        base_columns: Dictionary of base column values (id, timestamp, etc.)
        raw_data: Raw data dictionary to flatten

    Returns:
        The inserted row ID
    """
    # Get existing columns
    existing_columns = _get_table_columns(cur, table_name)

    # Flatten raw_data
    flattened = {}
    missing_columns = []

    if raw_data:
        flattened_raw = _flatten_jsonb_fields(raw_data)
        for key, value in flattened_raw.items():
            column_name = f"raw_data_{key}"
            # PostgreSQL stores column names in lowercase, so compare lowercase
            if column_name.lower() in existing_columns:
                flattened[column_name.lower()] = value
            else:
                missing_columns.append(column_name)

    # Log warnings for missing columns
    if missing_columns:
        logger.warning(
            f"New raw_data fields found in {table_name} without columns: {', '.join(missing_columns)}. "
            f"Please update the schema to add these columns."
        )

    # Combine base columns and flattened raw_data columns
    all_columns = {**base_columns, **flattened}

    # Build INSERT statement
    column_names = list(all_columns.keys())
    placeholders = ", ".join(["%s"] * len(column_names))
    column_list = ", ".join(column_names)

    query = f"""
        INSERT INTO {table_name} ({column_list})
        VALUES ({placeholders})
        RETURNING id
    """

    cur.execute(query, list(all_columns.values()))
    return cur.fetchone()[0]


def init_database():
    """Initialize database schema with all tables."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            # Create locations table
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS locations (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(255) NOT NULL UNIQUE,
                    latitude DOUBLE PRECISION NOT NULL,
                    longitude DOUBLE PRECISION NOT NULL,
                    timezone VARCHAR(50),
                    capacity_kw DOUBLE PRECISION,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_locations_name ON locations(name);
            """
            )

            # Add capacity_kw column if it doesn't exist (for existing databases)
            _add_column_if_not_exists(cur, "locations", "capacity_kw", "DOUBLE PRECISION")

            # Create location_api_configs table
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS location_api_configs (
                    id SERIAL PRIMARY KEY,
                    location_id INTEGER NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
                    api_type VARCHAR(50) NOT NULL,
                    enabled BOOLEAN NOT NULL DEFAULT TRUE,
                    config JSONB NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_api_configs_location_id ON location_api_configs(location_id);
            """
            )

            # Create power_readings table
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS power_readings (
                    id SERIAL PRIMARY KEY,
                    location_id INTEGER NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
                    timestamp TIMESTAMP NOT NULL,
                    power_produced DOUBLE PRECISION,
                    power_consumed DOUBLE PRECISION,
                    power_exported DOUBLE PRECISION,
                    power_imported DOUBLE PRECISION,
                    energy_imported_kwh DOUBLE PRECISION,
                    energy_exported_kwh DOUBLE PRECISION,
                    source VARCHAR(50) NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_power_readings_location_id ON power_readings(location_id);
                CREATE INDEX IF NOT EXISTS idx_power_readings_timestamp ON power_readings(timestamp);
            """
            )

            # Add energy columns if they don't exist (for existing databases)
            _add_column_if_not_exists(
                cur, "power_readings", "energy_imported_kwh", "DOUBLE PRECISION"
            )
            _add_column_if_not_exists(
                cur, "power_readings", "energy_exported_kwh", "DOUBLE PRECISION"
            )

            # Add columns for flattened raw_data fields (power_readings)
            _add_columns_if_not_exists(
                cur,
                "power_readings",
                [
                    # Enphase fields
                    ("raw_data_current_power", "TEXT"),
                    ("raw_data_power_production", "TEXT"),
                    ("raw_data_production", "TEXT"),
                    ("raw_data_production_wNow", "TEXT"),
                    ("raw_data_status", "TEXT"),
                    ("raw_data_system_id", "TEXT"),
                    ("raw_data_energy_lifetime", "TEXT"),
                    ("raw_data_energy_today", "TEXT"),
                    ("raw_data_last_interval_end_at", "TEXT"),
                    ("raw_data_last_report_at", "TEXT"),
                    ("raw_data_modules", "TEXT"),
                    ("raw_data_operational_at", "TEXT"),
                    ("raw_data_size_w", "TEXT"),
                    ("raw_data_nmi", "TEXT"),
                    ("raw_data_source", "TEXT"),
                    ("raw_data_summary_date", "TEXT"),
                    ("raw_data_battery_charge_w", "TEXT"),
                    ("raw_data_battery_discharge_w", "TEXT"),
                    ("raw_data_battery_capacity_wh", "TEXT"),
                    # Enphase energy telemetry fields
                    ("raw_data_energy_import_telemetry_system_id", "TEXT"),
                    ("raw_data_energy_import_telemetry_granularity", "TEXT"),
                    ("raw_data_energy_import_telemetry_total_devices", "TEXT"),
                    ("raw_data_energy_import_telemetry_start_at", "TEXT"),
                    ("raw_data_energy_import_telemetry_end_at", "TEXT"),
                    ("raw_data_energy_import_telemetry_items", "TEXT"),
                    ("raw_data_energy_import_telemetry_intervals", "TEXT"),
                    ("raw_data_energy_import_telemetry_meta_status", "TEXT"),
                    ("raw_data_energy_import_telemetry_meta_last_report_at", "TEXT"),
                    ("raw_data_energy_import_telemetry_meta_last_energy_at", "TEXT"),
                    ("raw_data_energy_import_telemetry_meta_operational_at", "TEXT"),
                    ("raw_data_energy_export_telemetry_system_id", "TEXT"),
                    ("raw_data_energy_export_telemetry_granularity", "TEXT"),
                    ("raw_data_energy_export_telemetry_total_devices", "TEXT"),
                    ("raw_data_energy_export_telemetry_start_at", "TEXT"),
                    ("raw_data_energy_export_telemetry_end_at", "TEXT"),
                    ("raw_data_energy_export_telemetry_items", "TEXT"),
                    ("raw_data_energy_export_telemetry_intervals", "TEXT"),
                    ("raw_data_energy_export_telemetry_meta_status", "TEXT"),
                    ("raw_data_energy_export_telemetry_meta_last_report_at", "TEXT"),
                    ("raw_data_energy_export_telemetry_meta_last_energy_at", "TEXT"),
                    ("raw_data_energy_export_telemetry_meta_operational_at", "TEXT"),
                    # Tesla fields
                    ("raw_data_response", "TEXT"),
                    ("raw_data_response_solar_power", "TEXT"),
                    ("raw_data_response_load_power", "TEXT"),
                    ("raw_data_response_grid_power", "TEXT"),
                    ("raw_data_response_battery_power", "TEXT"),
                    ("raw_data_response_percentage_charged", "TEXT"),
                    ("raw_data_response_grid_status", "TEXT"),
                    ("raw_data_response_generator_power", "TEXT"),
                    ("raw_data_response_wall_connectors", "TEXT"),
                    ("raw_data_response_island_status", "TEXT"),
                    ("raw_data_response_storm_mode_active", "TEXT"),
                    ("raw_data_response_storm_mode_states", "TEXT"),
                    ("raw_data_response_timestamp", "TEXT"),
                    # Tesla nested fields - wall_connectors
                    ("raw_data_response_wall_connectors_din", "TEXT"),
                    ("raw_data_response_wall_connectors_wall_connector_state", "TEXT"),
                    ("raw_data_response_wall_connectors_wall_connector_fault_state", "TEXT"),
                    ("raw_data_response_wall_connectors_wall_connector_power", "TEXT"),
                    ("raw_data_response_wall_connectors_ocpp_status", "TEXT"),
                    ("raw_data_response_wall_connectors_powershare_session_state", "TEXT"),
                    ("raw_data_response_wall_connectors_vin", "TEXT"),
                    # Tesla nested fields - storm_mode_states
                    ("raw_data_response_storm_mode_states_watch_event_id", "TEXT"),
                    ("raw_data_response_storm_mode_states_start_time", "TEXT"),
                    ("raw_data_response_storm_mode_states_end_time", "TEXT"),
                    ("raw_data_response_storm_mode_states_storm_type", "TEXT"),
                    # Raw data JSONB - MUST be last column
                    ("raw_data", "JSONB"),
                ],
            )

            # Create battery_banks table to store battery bank metadata from site_info API
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS battery_banks (
                    id SERIAL PRIMARY KEY,
                    location_id INTEGER NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
                    energy_site_id VARCHAR(255) NOT NULL,
                    battery_index INTEGER NOT NULL,
                    name VARCHAR(255),
                    capacity_kwh DOUBLE PRECISION,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    UNIQUE(energy_site_id, battery_index)
                );
                CREATE INDEX IF NOT EXISTS idx_battery_banks_location_id ON battery_banks(location_id);
                CREATE INDEX IF NOT EXISTS idx_battery_banks_energy_site_id ON battery_banks(energy_site_id);
            """
            )
            _add_columns_if_not_exists(
                cur,
                "battery_banks",
                [
                    ("serial_number", "VARCHAR(255)"),
                    ("part_number", "VARCHAR(255)"),
                    # Raw data JSONB - MUST be last column
                    ("raw_data", "JSONB"),
                ],
            )

            # Create battery_readings table
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS battery_readings (
                    id SERIAL PRIMARY KEY,
                    location_id INTEGER NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
                    battery_bank_id INTEGER REFERENCES battery_banks(id) ON DELETE SET NULL,
                    timestamp TIMESTAMP NOT NULL,
                    energy_charged DOUBLE PRECISION,
                    energy_discharged DOUBLE PRECISION,
                    power_charging DOUBLE PRECISION,
                    power_discharging DOUBLE PRECISION,
                    state_of_charge DOUBLE PRECISION,
                    source VARCHAR(50) NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_battery_readings_location_id ON battery_readings(location_id);
                CREATE INDEX IF NOT EXISTS idx_battery_readings_timestamp ON battery_readings(timestamp);
                CREATE INDEX IF NOT EXISTS idx_battery_readings_battery_bank_id ON battery_readings(battery_bank_id);
            """
            )

            # Add columns for flattened raw_data fields (battery_readings)
            _add_columns_if_not_exists(
                cur,
                "battery_readings",
                [
                    # Tesla fields
                    ("raw_data_response", "TEXT"),
                    ("raw_data_response_battery_power", "TEXT"),
                    ("raw_data_response_percentage_charged", "TEXT"),
                    ("raw_data_response_energy_left", "TEXT"),
                    ("raw_data_response_battery_energy", "TEXT"),
                    ("raw_data_response_solar_power", "TEXT"),
                    ("raw_data_response_load_power", "TEXT"),
                    ("raw_data_response_grid_status", "TEXT"),
                    ("raw_data_response_grid_power", "TEXT"),
                    ("raw_data_response_generator_power", "TEXT"),
                    ("raw_data_response_wall_connectors", "TEXT"),
                    ("raw_data_response_island_status", "TEXT"),
                    ("raw_data_response_storm_mode_active", "TEXT"),
                    ("raw_data_response_storm_mode_states", "TEXT"),
                    ("raw_data_response_timestamp", "TEXT"),
                    # Tesla nested fields - wall_connectors
                    ("raw_data_response_wall_connectors_din", "TEXT"),
                    ("raw_data_response_wall_connectors_wall_connector_state", "TEXT"),
                    ("raw_data_response_wall_connectors_wall_connector_fault_state", "TEXT"),
                    ("raw_data_response_wall_connectors_wall_connector_power", "TEXT"),
                    ("raw_data_response_wall_connectors_ocpp_status", "TEXT"),
                    ("raw_data_response_wall_connectors_powershare_session_state", "TEXT"),
                    ("raw_data_response_wall_connectors_vin", "TEXT"),
                    # Tesla nested fields - storm_mode_states
                    ("raw_data_response_storm_mode_states_watch_event_id", "TEXT"),
                    ("raw_data_response_storm_mode_states_start_time", "TEXT"),
                    ("raw_data_response_storm_mode_states_end_time", "TEXT"),
                    ("raw_data_response_storm_mode_states_storm_type", "TEXT"),
                    # Raw data JSONB - MUST be last column
                    ("raw_data", "JSONB"),
                ],
            )

            # Create irradiance_readings table
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS irradiance_readings (
                    id SERIAL PRIMARY KEY,
                    location_id INTEGER NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
                    timestamp TIMESTAMP NOT NULL,
                    ghi_clear_sky DOUBLE PRECISION,
                    ghi_cloudy_sky DOUBLE PRECISION,
                    dni_clear_sky DOUBLE PRECISION,
                    dni_cloudy_sky DOUBLE PRECISION,
                    dhi_clear_sky DOUBLE PRECISION,
                    dhi_cloudy_sky DOUBLE PRECISION,
                    source VARCHAR(50) NOT NULL DEFAULT 'openweather',
                    created_at TIMESTAMP NOT NULL DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_irradiance_readings_location_id ON irradiance_readings(location_id);
                CREATE INDEX IF NOT EXISTS idx_irradiance_readings_timestamp ON irradiance_readings(timestamp);
            """
            )

            # Create water_readings table
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS water_readings (
                    id SERIAL PRIMARY KEY,
                    location_id INTEGER NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
                    timestamp TIMESTAMP NOT NULL,
                    flow_rate_gpm DOUBLE PRECISION,
                    usage_gallons DOUBLE PRECISION,
                    usage_period VARCHAR(20),
                    source VARCHAR(50) NOT NULL DEFAULT 'flume',
                    created_at TIMESTAMP NOT NULL DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_water_readings_location_id ON water_readings(location_id);
                CREATE INDEX IF NOT EXISTS idx_water_readings_timestamp ON water_readings(timestamp);
            """
            )

            # Add columns for flattened raw_data fields (water_readings)
            _add_columns_if_not_exists(
                cur,
                "water_readings",
                [
                    # Flume fields
                    ("raw_data_current_flow_rate", "TEXT"),
                    ("raw_data_current_flow_rate_value", "TEXT"),
                    ("raw_data_daily_usage", "TEXT"),
                    ("raw_data_daily_usage_datetime", "TEXT"),
                    ("raw_data_daily_usage_value", "TEXT"),
                    ("raw_data_hourly_usage", "TEXT"),
                    ("raw_data_hourly_usage_datetime", "TEXT"),
                    ("raw_data_hourly_usage_value", "TEXT"),
                    # Raw data JSONB - MUST be last column
                    ("raw_data", "JSONB"),
                ],
            )

            # Create sprinkler_runs table for Rachio irrigation events
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS sprinkler_runs (
                    id SERIAL PRIMARY KEY,
                    location_id INTEGER NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
                    device_id VARCHAR(255) NOT NULL,
                    zone_id VARCHAR(255) NOT NULL,
                    zone_name VARCHAR(255),
                    zone_number INTEGER,
                    start_time TIMESTAMP NOT NULL,
                    end_time TIMESTAMP NOT NULL,
                    duration_seconds INTEGER,
                    schedule_type VARCHAR(50),
                    source VARCHAR(50) NOT NULL DEFAULT 'rachio',
                    created_at TIMESTAMP NOT NULL DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_sprinkler_runs_location_id ON sprinkler_runs(location_id);
                CREATE INDEX IF NOT EXISTS idx_sprinkler_runs_start_time ON sprinkler_runs(start_time);
                CREATE INDEX IF NOT EXISTS idx_sprinkler_runs_end_time ON sprinkler_runs(end_time);
                CREATE INDEX IF NOT EXISTS idx_sprinkler_runs_device_zone ON sprinkler_runs(device_id, zone_id);
            """
            )

            # Add columns for flattened raw_data fields (sprinkler_runs)
            _add_columns_if_not_exists(
                cur,
                "sprinkler_runs",
                [
                    # Rachio start_event fields
                    ("raw_data_start_event_id", "TEXT"),
                    ("raw_data_start_event_deviceId", "TEXT"),
                    ("raw_data_start_event_type", "TEXT"),
                    ("raw_data_start_event_eventDate", "TEXT"),
                    ("raw_data_start_event_summary", "TEXT"),
                    ("raw_data_start_event_subType", "TEXT"),
                    ("raw_data_start_event_hidden", "TEXT"),
                    ("raw_data_start_event_topic", "TEXT"),
                    # Rachio end_event fields
                    ("raw_data_end_event_id", "TEXT"),
                    ("raw_data_end_event_deviceId", "TEXT"),
                    ("raw_data_end_event_type", "TEXT"),
                    ("raw_data_end_event_eventDate", "TEXT"),
                    ("raw_data_end_event_summary", "TEXT"),
                    ("raw_data_end_event_subType", "TEXT"),
                    ("raw_data_end_event_hidden", "TEXT"),
                    ("raw_data_end_event_topic", "TEXT"),
                    # Raw data JSONB - MUST be last column
                    ("raw_data", "JSONB"),
                ],
            )

            # Add columns for flattened raw_data fields (irradiance_readings)
            _add_columns_if_not_exists(
                cur,
                "irradiance_readings",
                [
                    # OpenWeather fields
                    ("raw_data_intervals", "TEXT"),
                    ("raw_data_intervals_start", "TEXT"),
                    ("raw_data_intervals_avg_irradiance", "TEXT"),
                    ("raw_data_intervals_avg_irradiance_clear_sky", "TEXT"),
                    ("raw_data_intervals_avg_irradiance_clear_sky_ghi", "TEXT"),
                    ("raw_data_intervals_avg_irradiance_clear_sky_dni", "TEXT"),
                    ("raw_data_intervals_avg_irradiance_clear_sky_dhi", "TEXT"),
                    ("raw_data_intervals_avg_irradiance_cloudy_sky", "TEXT"),
                    ("raw_data_intervals_avg_irradiance_cloudy_sky_ghi", "TEXT"),
                    ("raw_data_intervals_avg_irradiance_cloudy_sky_dni", "TEXT"),
                    ("raw_data_intervals_avg_irradiance_cloudy_sky_dhi", "TEXT"),
                    # OpenWeather intervals additional fields
                    ("raw_data_intervals_end", "TEXT"),
                    ("raw_data_intervals_max_irradiance_clear_sky_ghi", "TEXT"),
                    ("raw_data_intervals_max_irradiance_clear_sky_dni", "TEXT"),
                    ("raw_data_intervals_max_irradiance_clear_sky_dhi", "TEXT"),
                    ("raw_data_intervals_max_irradiance_cloudy_sky_ghi", "TEXT"),
                    ("raw_data_intervals_max_irradiance_cloudy_sky_dni", "TEXT"),
                    ("raw_data_intervals_max_irradiance_cloudy_sky_dhi", "TEXT"),
                    ("raw_data_intervals_irradiation_clear_sky_ghi", "TEXT"),
                    ("raw_data_intervals_irradiation_clear_sky_dni", "TEXT"),
                    ("raw_data_intervals_irradiation_clear_sky_dhi", "TEXT"),
                    ("raw_data_intervals_irradiation_cloudy_sky_ghi", "TEXT"),
                    ("raw_data_intervals_irradiation_cloudy_sky_dni", "TEXT"),
                    ("raw_data_intervals_irradiation_cloudy_sky_dhi", "TEXT"),
                    # Tempest fields
                    ("raw_data_obs", "TEXT"),
                    ("raw_data_obs_solar_radiation", "TEXT"),
                    ("raw_data_obs_irradiance", "TEXT"),
                    ("raw_data_obs_solar_rad", "TEXT"),
                    # Tempest obs detailed fields
                    ("raw_data_obs_air_density", "TEXT"),
                    ("raw_data_obs_air_temperature", "TEXT"),
                    ("raw_data_obs_barometric_pressure", "TEXT"),
                    ("raw_data_obs_brightness", "TEXT"),
                    ("raw_data_obs_delta_t", "TEXT"),
                    ("raw_data_obs_dew_point", "TEXT"),
                    ("raw_data_obs_feels_like", "TEXT"),
                    ("raw_data_obs_heat_index", "TEXT"),
                    ("raw_data_obs_lightning_strike_count", "TEXT"),
                    ("raw_data_obs_lightning_strike_count_last_1hr", "TEXT"),
                    ("raw_data_obs_lightning_strike_count_last_3hr", "TEXT"),
                    ("raw_data_obs_lightning_strike_last_distance", "TEXT"),
                    ("raw_data_obs_lightning_strike_last_epoch", "TEXT"),
                    ("raw_data_obs_precip", "TEXT"),
                    ("raw_data_obs_precip_accum_last_1hr", "TEXT"),
                    ("raw_data_obs_precip_accum_local_day", "TEXT"),
                    ("raw_data_obs_precip_accum_local_day_final", "TEXT"),
                    ("raw_data_obs_precip_accum_local_yesterday", "TEXT"),
                    ("raw_data_obs_precip_accum_local_yesterday_final", "TEXT"),
                    ("raw_data_obs_precip_analysis_type_yesterday", "TEXT"),
                    ("raw_data_obs_precip_minutes_local_day", "TEXT"),
                    ("raw_data_obs_precip_minutes_local_yesterday", "TEXT"),
                    ("raw_data_obs_precip_minutes_local_yesterday_final", "TEXT"),
                    ("raw_data_obs_pressure_trend", "TEXT"),
                    ("raw_data_obs_relative_humidity", "TEXT"),
                    ("raw_data_obs_sea_level_pressure", "TEXT"),
                    ("raw_data_obs_station_pressure", "TEXT"),
                    ("raw_data_obs_timestamp", "TEXT"),
                    ("raw_data_obs_uv", "TEXT"),
                    ("raw_data_obs_wet_bulb_globe_temperature", "TEXT"),
                    ("raw_data_obs_wet_bulb_temperature", "TEXT"),
                    ("raw_data_obs_wind_avg", "TEXT"),
                    ("raw_data_obs_wind_chill", "TEXT"),
                    ("raw_data_obs_wind_direction", "TEXT"),
                    ("raw_data_obs_wind_gust", "TEXT"),
                    ("raw_data_obs_wind_lull", "TEXT"),
                    ("raw_data_stats_day", "TEXT"),
                    # Tempest stats fields
                    ("raw_data_type", "TEXT"),
                    ("raw_data_first_ob_day_local", "TEXT"),
                    ("raw_data_last_ob_day_local", "TEXT"),
                    ("raw_data_stats_week", "TEXT"),
                    ("raw_data_stats_month", "TEXT"),
                    ("raw_data_stats_year", "TEXT"),
                    ("raw_data_stats_alltime", "TEXT"),
                    ("raw_data_stats_week_time", "TEXT"),
                    ("raw_data_stats_month_time", "TEXT"),
                    ("raw_data_stats_year_time", "TEXT"),
                    ("raw_data_stats_alltime_time", "TEXT"),
                    ("raw_data_status_status_code", "TEXT"),
                    ("raw_data_status_status_message", "TEXT"),
                    ("raw_data_elevation", "TEXT"),
                    ("raw_data_is_public", "TEXT"),
                    ("raw_data_latitude", "TEXT"),
                    ("raw_data_longitude", "TEXT"),
                    ("raw_data_outdoor_keys", "TEXT"),
                    ("raw_data_public_name", "TEXT"),
                    ("raw_data_station_id", "TEXT"),
                    ("raw_data_station_name", "TEXT"),
                    ("raw_data_station_units_units_direction", "TEXT"),
                    ("raw_data_station_units_units_distance", "TEXT"),
                    ("raw_data_station_units_units_other", "TEXT"),
                    ("raw_data_station_units_units_precip", "TEXT"),
                    ("raw_data_station_units_units_pressure", "TEXT"),
                    ("raw_data_station_units_units_temp", "TEXT"),
                    ("raw_data_station_units_units_wind", "TEXT"),
                    ("raw_data_timezone", "TEXT"),
                    # OpenWeather additional fields
                    ("raw_data_lat", "TEXT"),
                    ("raw_data_lon", "TEXT"),
                    ("raw_data_date", "TEXT"),
                    ("raw_data_interval", "TEXT"),
                    ("raw_data_tz", "TEXT"),
                    ("raw_data_sunrise", "TEXT"),
                    ("raw_data_sunset", "TEXT"),
                    # Raw data JSONB - MUST be last column
                    ("raw_data", "JSONB"),
                ],
            )

            # Create propane_readings table for Tank Utility propane tank monitoring
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS propane_readings (
                    id SERIAL PRIMARY KEY,
                    location_id INTEGER NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
                    device_id VARCHAR(255) NOT NULL,
                    timestamp TIMESTAMP NOT NULL,
                    tank_level_percent DOUBLE PRECISION,
                    tank_level_gallons DOUBLE PRECISION,
                    capacity_gallons DOUBLE PRECISION,
                    temperature_f DOUBLE PRECISION,
                    battery_status VARCHAR(50),
                    battery_warn BOOLEAN,
                    battery_crit BOOLEAN,
                    fuel_type VARCHAR(50),
                    source VARCHAR(50) NOT NULL DEFAULT 'tankutility',
                    created_at TIMESTAMP NOT NULL DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_propane_readings_location_id ON propane_readings(location_id);
                CREATE INDEX IF NOT EXISTS idx_propane_readings_timestamp ON propane_readings(timestamp);
                CREATE INDEX IF NOT EXISTS idx_propane_readings_device_id ON propane_readings(device_id);
            """
            )

            # Add columns for flattened raw_data fields (propane_readings)
            _add_columns_if_not_exists(
                cur,
                "propane_readings",
                [
                    # Tank Utility device fields
                    ("raw_data_device_device_id", "TEXT"),
                    ("raw_data_device_short_device_id", "TEXT"),
                    ("raw_data_device_name", "TEXT"),
                    ("raw_data_device_address", "TEXT"),
                    ("raw_data_device_fuel_type", "TEXT"),
                    ("raw_data_device_capacity", "TEXT"),
                    ("raw_data_device_orientation", "TEXT"),
                    ("raw_data_device_status", "TEXT"),
                    ("raw_data_device_battery_level", "TEXT"),
                    ("raw_data_device_battery_warn", "TEXT"),
                    ("raw_data_device_battery_crit", "TEXT"),
                    ("raw_data_device_average_consumption", "TEXT"),
                    ("raw_data_device_reading_interval", "TEXT"),
                    ("raw_data_device_transmission_interval", "TEXT"),
                    # Tank Utility lastReading fields
                    ("raw_data_device_lastReading_tank", "TEXT"),
                    ("raw_data_device_lastReading_temperature", "TEXT"),
                    ("raw_data_device_lastReading_time", "TEXT"),
                    ("raw_data_device_lastReading_time_iso", "TEXT"),
                    ("raw_data_device_lastReading_sw_rev", "TEXT"),
                    ("raw_data_device_lastReading_event_code", "TEXT"),
                    # Tank Utility telemetry fields (LTE signal info)
                    ("raw_data_device_telemetry", "TEXT"),
                    # Tank Utility additional device fields
                    ("raw_data_device_account_id", "TEXT"),
                    ("raw_data_device_fuel_dealer_id", "TEXT"),
                    ("raw_data_device_connection_type", "TEXT"),
                    ("raw_data_device_product_id", "TEXT"),
                    ("raw_data_device_product_name", "TEXT"),
                    ("raw_data_device_supplier_id", "TEXT"),
                    ("raw_data_device_consumption_types", "TEXT"),
                    ("raw_data_device_consumption_type_backup_heating", "TEXT"),
                    ("raw_data_device_consumption_type_bulk_storage", "TEXT"),
                    ("raw_data_device_consumption_type_commercial_industrial", "TEXT"),
                    ("raw_data_device_consumption_type_cooking", "TEXT"),
                    ("raw_data_device_consumption_type_fireplace", "TEXT"),
                    ("raw_data_device_consumption_type_generator", "TEXT"),
                    ("raw_data_device_consumption_type_heating", "TEXT"),
                    ("raw_data_device_consumption_type_hot_water", "TEXT"),
                    ("raw_data_device_consumption_type_laundry_dryer", "TEXT"),
                    ("raw_data_device_consumption_type_pool", "TEXT"),
                    ("raw_data_device_consumption_type_retail_fill_up", "TEXT"),
                    ("raw_data_device_estimated_fill_date", "TEXT"),
                    ("raw_data_device_fixed_transmission_time", "TEXT"),
                    ("raw_data_device_threshold_1", "TEXT"),
                    ("raw_data_device_threshold_2", "TEXT"),
                    ("raw_data_device_change_of_value", "TEXT"),
                    # Tank Utility additional lastReading fields
                    ("raw_data_device_lastReading_fixed_transmission_time", "TEXT"),
                    ("raw_data_device_lastReading_reading_interval", "TEXT"),
                    ("raw_data_device_lastReading_transmission_interval", "TEXT"),
                    ("raw_data_device_lastReading_threshold_1", "TEXT"),
                    ("raw_data_device_lastReading_threshold_2", "TEXT"),
                    ("raw_data_device_lastReading_change_of_value", "TEXT"),
                    # Tank Utility detailed telemetry fields
                    ("raw_data_device_telemetry_attempt_no", "TEXT"),
                    ("raw_data_device_telemetry_band", "TEXT"),
                    ("raw_data_device_telemetry_cell_id", "TEXT"),
                    ("raw_data_device_telemetry_chn", "TEXT"),
                    ("raw_data_device_telemetry_fplmn", "TEXT"),
                    ("raw_data_device_telemetry_http_status_code", "TEXT"),
                    ("raw_data_device_telemetry_module_temp", "TEXT"),
                    ("raw_data_device_telemetry_module_voltage", "TEXT"),
                    ("raw_data_device_telemetry_plmn", "TEXT"),
                    ("raw_data_device_telemetry_rat", "TEXT"),
                    ("raw_data_device_telemetry_rsrp", "TEXT"),
                    ("raw_data_device_telemetry_rsrq", "TEXT"),
                    ("raw_data_device_telemetry_rssi", "TEXT"),
                    ("raw_data_device_telemetry_srxlev", "TEXT"),
                    ("raw_data_device_telemetry_state", "TEXT"),
                    ("raw_data_device_telemetry_time_to_conn", "TEXT"),
                    ("raw_data_device_telemetry_tlm_time", "TEXT"),
                    ("raw_data_device_telemetry_type", "TEXT"),
                    # Raw data JSONB - MUST be last column
                    ("raw_data", "JSONB"),
                ],
            )

            # Create pool_readings table for iAqualink pool monitoring
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS pool_readings (
                    id SERIAL PRIMARY KEY,
                    location_id INTEGER NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
                    serial_number VARCHAR(255) NOT NULL,
                    timestamp TIMESTAMP NOT NULL,
                    pool_temp INTEGER,
                    spa_temp INTEGER,
                    air_temp INTEGER,
                    pool_set_point INTEGER,
                    spa_set_point INTEGER,
                    pool_pump BOOLEAN,
                    spa_pump BOOLEAN,
                    pool_heater BOOLEAN,
                    spa_heater BOOLEAN,
                    source VARCHAR(50) NOT NULL DEFAULT 'iaqualink',
                    created_at TIMESTAMP NOT NULL DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_pool_readings_location_id ON pool_readings(location_id);
                CREATE INDEX IF NOT EXISTS idx_pool_readings_timestamp ON pool_readings(timestamp);
                CREATE INDEX IF NOT EXISTS idx_pool_readings_serial ON pool_readings(serial_number);
            """
            )

            # Add columns for flattened raw_data fields (pool_readings)
            _add_columns_if_not_exists(
                cur,
                "pool_readings",
                [
                    # iAqualink home_screen fields (from live API)
                    ("raw_data_home_screen", "TEXT"),
                    ("raw_data_home_screen_status", "TEXT"),
                    ("raw_data_message", "TEXT"),
                    ("raw_data_serial", "TEXT"),
                    # Backfill fields (from BigQuery migration)
                    ("raw_data_backfill_source", "TEXT"),
                    ("raw_data_original_row_datetime", "TEXT"),
                    ("raw_data_original_row_pool_temp", "TEXT"),
                    ("raw_data_original_row_air_temp", "TEXT"),
                    ("raw_data_original_row_spa_temp", "TEXT"),
                    ("raw_data_original_row_pool_set_point", "TEXT"),
                    ("raw_data_original_row_spa_set_point", "TEXT"),
                    ("raw_data_original_row_spa_pump", "TEXT"),
                    ("raw_data_original_row_pool_pump", "TEXT"),
                    ("raw_data_original_row_spa_heater", "TEXT"),
                    ("raw_data_original_row_pool_heater", "TEXT"),
                    # Raw data JSONB - MUST be last column
                    ("raw_data", "JSONB"),
                ],
            )

            # Create system_readings table for system stats monitoring
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS system_readings (
                    id SERIAL PRIMARY KEY,
                    timestamp TIMESTAMP NOT NULL,
                    cpu_percent DOUBLE PRECISION,
                    memory_percent DOUBLE PRECISION,
                    memory_used_mb DOUBLE PRECISION,
                    memory_total_mb DOUBLE PRECISION,
                    disk_percent DOUBLE PRECISION,
                    disk_used_gb DOUBLE PRECISION,
                    disk_total_gb DOUBLE PRECISION,
                    source VARCHAR(50) NOT NULL DEFAULT 'psutil',
                    created_at TIMESTAMP NOT NULL DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_system_readings_timestamp
                    ON system_readings(timestamp);
            """
            )

            # Add disk columns if they don't exist (for existing databases)
            _add_columns_if_not_exists(
                cur,
                "system_readings",
                [
                    ("disk_percent", "DOUBLE PRECISION"),
                    ("disk_used_gb", "DOUBLE PRECISION"),
                    ("disk_total_gb", "DOUBLE PRECISION"),
                ],
            )

            # Create enphase_local_readings table for local gateway data
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS enphase_local_readings (
                    id SERIAL PRIMARY KEY,
                    location_id INTEGER NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
                    gateway_serial VARCHAR(255) NOT NULL,
                    timestamp TIMESTAMP NOT NULL,
                    power_produced DOUBLE PRECISION,
                    power_consumed DOUBLE PRECISION,
                    power_net DOUBLE PRECISION,
                    grid_voltage_l1 DOUBLE PRECISION,
                    grid_voltage_l2 DOUBLE PRECISION,
                    grid_frequency DOUBLE PRECISION,
                    energy_produced_today_wh DOUBLE PRECISION,
                    energy_consumed_today_wh DOUBLE PRECISION,
                    energy_lifetime_wh DOUBLE PRECISION,
                    source VARCHAR(50) NOT NULL DEFAULT 'enphase_local',
                    created_at TIMESTAMP NOT NULL DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_enphase_local_readings_location_id
                    ON enphase_local_readings(location_id);
                CREATE INDEX IF NOT EXISTS idx_enphase_local_readings_timestamp
                    ON enphase_local_readings(timestamp);
                CREATE INDEX IF NOT EXISTS idx_enphase_local_readings_gateway
                    ON enphase_local_readings(gateway_serial);
            """
            )

            # Add columns for flattened raw_data fields (enphase_local_readings)
            _add_columns_if_not_exists(
                cur,
                "enphase_local_readings",
                [
                    # Production endpoint fields
                    ("raw_data_production", "TEXT"),
                    ("raw_data_production_wattHoursToday", "TEXT"),
                    ("raw_data_production_wattHoursSevenDays", "TEXT"),
                    ("raw_data_production_wattHoursLifetime", "TEXT"),
                    ("raw_data_production_wattsNow", "TEXT"),
                    # Consumption report fields
                    ("raw_data_consumption_report", "TEXT"),
                    ("raw_data_consumption_report_createdAt", "TEXT"),
                    ("raw_data_consumption_report_reportType", "TEXT"),
                    ("raw_data_consumption_report_cumulative_currW", "TEXT"),
                    ("raw_data_consumption_report_cumulative_actPower", "TEXT"),
                    ("raw_data_consumption_report_cumulative_apprntPwr", "TEXT"),
                    ("raw_data_consumption_report_cumulative_reactPwr", "TEXT"),
                    ("raw_data_consumption_report_cumulative_whDlvdCum", "TEXT"),
                    ("raw_data_consumption_report_cumulative_whRcvdCum", "TEXT"),
                    ("raw_data_consumption_report_cumulative_varhLagCum", "TEXT"),
                    ("raw_data_consumption_report_cumulative_varhLeadCum", "TEXT"),
                    ("raw_data_consumption_report_cumulative_vahCum", "TEXT"),
                    ("raw_data_consumption_report_cumulative_rmsVoltage", "TEXT"),
                    ("raw_data_consumption_report_cumulative_rmsCurrent", "TEXT"),
                    ("raw_data_consumption_report_cumulative_pwrFactor", "TEXT"),
                    ("raw_data_consumption_report_cumulative_freqHz", "TEXT"),
                    ("raw_data_consumption_report_lines", "TEXT"),
                    ("raw_data_consumption_report_lines_currW", "TEXT"),
                    ("raw_data_consumption_report_lines_actPower", "TEXT"),
                    ("raw_data_consumption_report_lines_apprntPwr", "TEXT"),
                    ("raw_data_consumption_report_lines_reactPwr", "TEXT"),
                    ("raw_data_consumption_report_lines_whDlvdCum", "TEXT"),
                    ("raw_data_consumption_report_lines_whRcvdCum", "TEXT"),
                    ("raw_data_consumption_report_lines_varhLagCum", "TEXT"),
                    ("raw_data_consumption_report_lines_varhLeadCum", "TEXT"),
                    ("raw_data_consumption_report_lines_vahCum", "TEXT"),
                    ("raw_data_consumption_report_lines_rmsVoltage", "TEXT"),
                    ("raw_data_consumption_report_lines_rmsCurrent", "TEXT"),
                    ("raw_data_consumption_report_lines_pwrFactor", "TEXT"),
                    ("raw_data_consumption_report_lines_freqHz", "TEXT"),
                    # Grid reading fields
                    ("raw_data_grid_reading", "TEXT"),
                    ("raw_data_grid_reading_channels", "TEXT"),
                    ("raw_data_grid_reading_channels_phase", "TEXT"),
                    ("raw_data_grid_reading_channels_activePower", "TEXT"),
                    ("raw_data_grid_reading_channels_reactivePower", "TEXT"),
                    ("raw_data_grid_reading_channels_voltage", "TEXT"),
                    ("raw_data_grid_reading_channels_current", "TEXT"),
                    ("raw_data_grid_reading_channels_freq", "TEXT"),
                    # Meter readings fields
                    ("raw_data_meter_readings", "TEXT"),
                    ("raw_data_meter_readings_eid", "TEXT"),
                    ("raw_data_meter_readings_timestamp", "TEXT"),
                    ("raw_data_meter_readings_actEnergyDlvd", "TEXT"),
                    ("raw_data_meter_readings_actEnergyRcvd", "TEXT"),
                    ("raw_data_meter_readings_apparentEnergy", "TEXT"),
                    ("raw_data_meter_readings_reactEnergyLagg", "TEXT"),
                    ("raw_data_meter_readings_reactEnergyLead", "TEXT"),
                    ("raw_data_meter_readings_instantaneousDemand", "TEXT"),
                    ("raw_data_meter_readings_activePower", "TEXT"),
                    ("raw_data_meter_readings_apparentPower", "TEXT"),
                    ("raw_data_meter_readings_reactivePower", "TEXT"),
                    ("raw_data_meter_readings_pwrFactor", "TEXT"),
                    ("raw_data_meter_readings_voltage", "TEXT"),
                    ("raw_data_meter_readings_current", "TEXT"),
                    ("raw_data_meter_readings_freq", "TEXT"),
                    ("raw_data_meter_readings_channels", "TEXT"),
                    ("raw_data_meter_readings_channels_eid", "TEXT"),
                    ("raw_data_meter_readings_channels_timestamp", "TEXT"),
                    ("raw_data_meter_readings_channels_actEnergyDlvd", "TEXT"),
                    ("raw_data_meter_readings_channels_actEnergyRcvd", "TEXT"),
                    ("raw_data_meter_readings_channels_apparentEnergy", "TEXT"),
                    ("raw_data_meter_readings_channels_reactEnergyLagg", "TEXT"),
                    ("raw_data_meter_readings_channels_reactEnergyLead", "TEXT"),
                    ("raw_data_meter_readings_channels_instantaneousDemand", "TEXT"),
                    ("raw_data_meter_readings_channels_activePower", "TEXT"),
                    ("raw_data_meter_readings_channels_apparentPower", "TEXT"),
                    ("raw_data_meter_readings_channels_reactivePower", "TEXT"),
                    ("raw_data_meter_readings_channels_pwrFactor", "TEXT"),
                    ("raw_data_meter_readings_channels_voltage", "TEXT"),
                    ("raw_data_meter_readings_channels_current", "TEXT"),
                    ("raw_data_meter_readings_channels_freq", "TEXT"),
                    # Production JSON endpoint fields (production.json)
                    ("raw_data_production_json_production", "TEXT"),
                    ("raw_data_production_json_production_type", "TEXT"),
                    ("raw_data_production_json_production_activeCount", "TEXT"),
                    ("raw_data_production_json_production_readingTime", "TEXT"),
                    ("raw_data_production_json_production_wNow", "TEXT"),
                    ("raw_data_production_json_production_whLifetime", "TEXT"),
                    ("raw_data_production_json_consumption", "TEXT"),
                    ("raw_data_production_json_consumption_type", "TEXT"),
                    ("raw_data_production_json_consumption_activeCount", "TEXT"),
                    ("raw_data_production_json_consumption_measurementType", "TEXT"),
                    ("raw_data_production_json_consumption_readingTime", "TEXT"),
                    ("raw_data_production_json_consumption_wNow", "TEXT"),
                    ("raw_data_production_json_consumption_whLifetime", "TEXT"),
                    ("raw_data_production_json_consumption_varhLeadLifetime", "TEXT"),
                    ("raw_data_production_json_consumption_varhLagLifetime", "TEXT"),
                    ("raw_data_production_json_consumption_vahLifetime", "TEXT"),
                    ("raw_data_production_json_consumption_rmsCurrent", "TEXT"),
                    ("raw_data_production_json_consumption_rmsVoltage", "TEXT"),
                    ("raw_data_production_json_consumption_reactPwr", "TEXT"),
                    ("raw_data_production_json_consumption_apprntPwr", "TEXT"),
                    ("raw_data_production_json_consumption_pwrFactor", "TEXT"),
                    ("raw_data_production_json_consumption_whToday", "TEXT"),
                    ("raw_data_production_json_consumption_whLastSevenDays", "TEXT"),
                    ("raw_data_production_json_consumption_vahToday", "TEXT"),
                    ("raw_data_production_json_consumption_varhLeadToday", "TEXT"),
                    ("raw_data_production_json_consumption_varhLagToday", "TEXT"),
                    ("raw_data_production_json_storage", "TEXT"),
                    ("raw_data_production_json_storage_type", "TEXT"),
                    ("raw_data_production_json_storage_activeCount", "TEXT"),
                    ("raw_data_production_json_storage_readingTime", "TEXT"),
                    ("raw_data_production_json_storage_wNow", "TEXT"),
                    ("raw_data_production_json_storage_whNow", "TEXT"),
                    ("raw_data_production_json_storage_state", "TEXT"),
                    # Raw data JSONB - MUST be last column
                    ("raw_data", "JSONB"),
                ],
            )

            # Create enphase_gateway_tokens table for local gateway token storage
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS enphase_gateway_tokens (
                    id SERIAL PRIMARY KEY,
                    gateway_serial VARCHAR(255) NOT NULL UNIQUE,
                    gateway_host VARCHAR(255) NOT NULL,
                    token TEXT NOT NULL,
                    token_expires_at TIMESTAMPTZ,
                    location_id INTEGER REFERENCES locations(id) ON DELETE SET NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_enphase_gateway_tokens_serial
                    ON enphase_gateway_tokens(gateway_serial);
            """
            )

            # Create span_panel_tokens table for Span panel token storage
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS span_panel_tokens (
                    id SERIAL PRIMARY KEY,
                    panel_serial VARCHAR(255) NOT NULL UNIQUE,
                    panel_host VARCHAR(255) NOT NULL,
                    panel_name VARCHAR(255),
                    token TEXT NOT NULL,
                    token_created_at TIMESTAMPTZ,
                    location_id INTEGER REFERENCES locations(id) ON DELETE SET NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_span_panel_tokens_serial
                    ON span_panel_tokens(panel_serial);
            """
            )

            # Create span_panel_readings table for panel-level data (every fetch cycle)
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS span_panel_readings (
                    id SERIAL PRIMARY KEY,
                    location_id INTEGER NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
                    panel_serial VARCHAR(255) NOT NULL,
                    timestamp TIMESTAMPTZ NOT NULL,
                    instant_grid_power_w DOUBLE PRECISION,
                    feedthrough_power_w DOUBLE PRECISION,
                    main_relay_state VARCHAR(50),
                    dsm_grid_state VARCHAR(50),
                    dsm_state VARCHAR(50),
                    current_run_config VARCHAR(50),
                    door_state VARCHAR(50),
                    firmware_version VARCHAR(100),
                    uptime_seconds INTEGER,
                    battery_soe_percent DOUBLE PRECISION,
                    eth0_link BOOLEAN,
                    wlan_link BOOLEAN,
                    wwan_link BOOLEAN,
                    source VARCHAR(50) NOT NULL DEFAULT 'span',
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    raw_data JSONB
                );
                CREATE INDEX IF NOT EXISTS idx_span_panel_readings_location_id
                    ON span_panel_readings(location_id);
                CREATE INDEX IF NOT EXISTS idx_span_panel_readings_timestamp
                    ON span_panel_readings(timestamp);
                CREATE INDEX IF NOT EXISTS idx_span_panel_readings_panel_serial
                    ON span_panel_readings(panel_serial);
            """
            )

            # Create span_circuit_readings table for circuit-level data (every 15 min)
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS span_circuit_readings (
                    id SERIAL PRIMARY KEY,
                    location_id INTEGER NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
                    panel_serial VARCHAR(255) NOT NULL,
                    timestamp TIMESTAMPTZ NOT NULL,
                    circuit_id VARCHAR(255) NOT NULL,
                    circuit_name VARCHAR(255),
                    tabs TEXT,
                    instant_power_w DOUBLE PRECISION,
                    import_energy_wh DOUBLE PRECISION,
                    export_energy_wh DOUBLE PRECISION,
                    relay_state VARCHAR(50),
                    priority VARCHAR(50),
                    is_user_controllable BOOLEAN,
                    is_sheddable BOOLEAN,
                    is_never_backup BOOLEAN,
                    source VARCHAR(50) NOT NULL DEFAULT 'span',
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    raw_data JSONB
                );
                CREATE INDEX IF NOT EXISTS idx_span_circuit_readings_location_id
                    ON span_circuit_readings(location_id);
                CREATE INDEX IF NOT EXISTS idx_span_circuit_readings_timestamp
                    ON span_circuit_readings(timestamp);
                CREATE INDEX IF NOT EXISTS idx_span_circuit_readings_panel_circuit
                    ON span_circuit_readings(panel_serial, circuit_id, timestamp);
            """
            )

            # Create fetch_run_summaries table for tracking fetcher runs
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS fetch_run_summaries (
                    id SERIAL PRIMARY KEY,
                    started_at TIMESTAMPTZ NOT NULL,
                    completed_at TIMESTAMPTZ,
                    status VARCHAR(50) NOT NULL DEFAULT 'running',
                    total_data_points INTEGER DEFAULT 0,
                    integrations_summary JSONB,
                    error_message TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_fetch_run_summaries_started_at
                    ON fetch_run_summaries(started_at DESC);
            """
            )

        conn.commit()

    # Create enphase_app_tokens table (for multi-app support)
    create_enphase_app_tokens_table()

    print("Database schema initialized successfully")


def get_locations() -> List[Dict[str, Any]]:
    """Get all locations."""
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM locations ORDER BY id")
            return [dict(row) for row in cur.fetchall()]


def get_location_api_configs(location_id: int, enabled_only: bool = True) -> List[Dict[str, Any]]:
    """Get API configs for a location."""
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if enabled_only:
                cur.execute(
                    "SELECT * FROM location_api_configs WHERE location_id = %s AND enabled = TRUE ORDER BY id",
                    (location_id,),
                )
            else:
                cur.execute(
                    "SELECT * FROM location_api_configs WHERE location_id = %s ORDER BY id",
                    (location_id,),
                )
            return _rows_to_dicts(cur.fetchall(), ["config"])


def get_any_enphase_config_with_tokens() -> Optional[Dict[str, Any]]:
    """
    Get any Enphase API config that has tokens stored.

    Since Enphase tokens are account-level (not location-specific), we can use
    tokens from any location. Returns the first one found with tokens.

    Returns:
        API config dict with tokens, or None if not found
    """
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Get all enphase configs
            cur.execute("SELECT * FROM location_api_configs WHERE api_type = 'enphase' ORDER BY id")
            rows = _rows_to_dicts(cur.fetchall(), ["config"])

            # Find first one with tokens
            for row_dict in rows:
                config = row_dict.get("config", {})
                if isinstance(config, dict) and config.get("access_token"):
                    return row_dict
            return None


def get_any_flume_config_with_tokens() -> Optional[Dict[str, Any]]:
    """
    Get any Flume API config that has tokens stored.

    Since Flume tokens are account-level (not location-specific), we can use
    tokens from any location. Returns the first one found with tokens.

    Returns:
        API config dict with tokens, or None if not found
    """
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Get all flume configs
            cur.execute("SELECT * FROM location_api_configs WHERE api_type = 'flume' ORDER BY id")
            rows = _rows_to_dicts(cur.fetchall(), ["config"])

            # Find first one with tokens
            for row_dict in rows:
                config = row_dict.get("config", {})
                if isinstance(config, dict) and config.get("access_token"):
                    return row_dict
            return None


def get_location_api_config(location_id: int, api_type: str) -> Optional[Dict[str, Any]]:
    """Get a specific API config for a location."""
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM location_api_configs WHERE location_id = %s AND api_type = %s",
                (location_id, api_type),
            )
            row = cur.fetchone()
            if not row:
                return None
            return _row_to_dict(row, ["config"])


def update_enphase_tokens_globally(
    access_token: Optional[str] = None,
    refresh_token: Optional[str] = None,
    token_expires_at: Optional[datetime] = None,
) -> None:
    """
    Update Enphase tokens globally (in all Enphase API configs).

    Since Enphase tokens are account-level (not location-specific), we update
    them in all locations that have Enphase configs.

    Args:
        access_token: Access token to store (optional)
        refresh_token: Refresh token to store (optional)
        token_expires_at: Token expiration timestamp (optional)
    """
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Get all enphase configs
            cur.execute("SELECT id, config FROM location_api_configs WHERE api_type = 'enphase'")
            rows = cur.fetchall()

            if not rows:
                raise ValueError(
                    "No Enphase API configs found. Create at least one enphase config first."
                )

            # Update tokens in all enphase configs
            for row in rows:
                config_id = row["id"]
                row_dict = _row_to_dict(row, ["config"])
                existing_config = row_dict.get("config") or {}

                # Update tokens
                if access_token is not None:
                    existing_config["access_token"] = access_token
                if refresh_token is not None:
                    existing_config["refresh_token"] = refresh_token
                if token_expires_at is not None:
                    existing_config["token_expires_at"] = token_expires_at.isoformat()

                # Update in database
                cur.execute(
                    """
                    UPDATE location_api_configs
                    SET config = %s, updated_at = NOW()
                    WHERE id = %s
                    """,
                    (json.dumps(existing_config), config_id),
                )


def update_flume_tokens_globally(
    access_token: Optional[str] = None,
    refresh_token: Optional[str] = None,
    token_expires_at: Optional[datetime] = None,
    user_id: Optional[str] = None,
) -> None:
    """
    Update Flume tokens globally (in all Flume API configs).

    Since Flume tokens are account-level (not location-specific), we update
    them in all locations that have Flume configs.

    Args:
        access_token: Access token to store (optional)
        refresh_token: Refresh token to store (optional)
        token_expires_at: Token expiration timestamp (optional)
        user_id: Flume user ID extracted from JWT (optional)
    """
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Get all flume configs
            cur.execute("SELECT id, config FROM location_api_configs WHERE api_type = 'flume'")
            rows = cur.fetchall()

            if not rows:
                raise ValueError(
                    "No Flume API configs found. Create at least one flume config first."
                )

            # Update tokens in all flume configs
            for row in rows:
                config_id = row["id"]
                row_dict = _row_to_dict(row, ["config"])
                existing_config = row_dict.get("config") or {}

                # Update tokens
                if access_token is not None:
                    existing_config["access_token"] = access_token
                if refresh_token is not None:
                    existing_config["refresh_token"] = refresh_token
                if token_expires_at is not None:
                    existing_config["token_expires_at"] = token_expires_at.isoformat()
                if user_id is not None:
                    existing_config["user_id"] = user_id

                # Update in database
                cur.execute(
                    """
                    UPDATE location_api_configs
                    SET config = %s, updated_at = NOW()
                    WHERE id = %s
                    """,
                    (json.dumps(existing_config), config_id),
                )


def insert_power_reading(
    location_id: int,
    timestamp: datetime,
    power_produced: Optional[float] = None,
    power_consumed: Optional[float] = None,
    power_exported: Optional[float] = None,
    power_imported: Optional[float] = None,
    energy_imported_kwh: Optional[float] = None,
    energy_exported_kwh: Optional[float] = None,
    source: str = "unknown",
    raw_data: Optional[Dict[str, Any]] = None,
) -> int:
    """Insert a power reading with flattened raw_data fields and return the ID."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            base_columns = {
                "location_id": location_id,
                "timestamp": timestamp,
                "power_produced": power_produced,
                "power_consumed": power_consumed,
                "power_exported": power_exported,
                "power_imported": power_imported,
                "energy_imported_kwh": energy_imported_kwh,
                "energy_exported_kwh": energy_exported_kwh,
                "source": source,
                "raw_data": json.dumps(raw_data) if raw_data else None,
            }
            return _insert_with_flattened_raw_data(cur, "power_readings", base_columns, raw_data)


def insert_battery_reading(
    location_id: int,
    timestamp: datetime,
    energy_charged: Optional[float] = None,
    energy_discharged: Optional[float] = None,
    power_charging: Optional[float] = None,
    power_discharging: Optional[float] = None,
    state_of_charge: Optional[float] = None,
    source: str = "unknown",
    raw_data: Optional[Dict[str, Any]] = None,
    battery_bank_id: Optional[int] = None,
) -> int:
    """Insert a battery reading with flattened raw_data fields and return the ID."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            base_columns = {
                "location_id": location_id,
                "timestamp": timestamp,
                "battery_bank_id": battery_bank_id,
                "energy_charged": energy_charged,
                "energy_discharged": energy_discharged,
                "power_charging": power_charging,
                "power_discharging": power_discharging,
                "state_of_charge": state_of_charge,
                "source": source,
                "raw_data": json.dumps(raw_data) if raw_data else None,
            }
            return _insert_with_flattened_raw_data(cur, "battery_readings", base_columns, raw_data)


def get_battery_bank(
    location_id: int, energy_site_id: str, battery_index: int
) -> Optional[Dict[str, Any]]:
    """Get a battery bank by location_id, energy_site_id, and battery_index."""
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT * FROM battery_banks
                WHERE location_id = %s AND energy_site_id = %s AND battery_index = %s
                """,
                (location_id, energy_site_id, battery_index),
            )
            row = cur.fetchone()
            if not row:
                return None
            return _row_to_dict(row, ["raw_data"])


def get_battery_banks_by_location(location_id: int) -> List[Dict[str, Any]]:
    """Get all battery banks for a location."""
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT * FROM battery_banks
                WHERE location_id = %s
                ORDER BY energy_site_id, battery_index
                """,
                (location_id,),
            )
            return _rows_to_dicts(cur.fetchall(), ["raw_data"])


def get_battery_banks_by_energy_site(energy_site_id: str) -> List[Dict[str, Any]]:
    """Get all battery banks for an energy site."""
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT * FROM battery_banks
                WHERE energy_site_id = %s
                ORDER BY battery_index
                """,
                (energy_site_id,),
            )
            return _rows_to_dicts(cur.fetchall(), ["raw_data"])


def insert_or_update_battery_bank(
    location_id: int,
    energy_site_id: str,
    battery_index: int,
    name: Optional[str] = None,
    capacity_kwh: Optional[float] = None,
    serial_number: Optional[str] = None,
    part_number: Optional[str] = None,
    raw_data: Optional[Dict[str, Any]] = None,
) -> int:
    """
    Insert or update a battery bank record.

    Returns:
        The battery bank ID
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            # Check if exists
            cur.execute(
                """
                SELECT id FROM battery_banks
                WHERE location_id = %s AND energy_site_id = %s AND battery_index = %s
                """,
                (location_id, energy_site_id, battery_index),
            )
            existing = cur.fetchone()

            if existing:
                # Update
                cur.execute(
                    """
                    UPDATE battery_banks
                    SET name = COALESCE(%s, name),
                        capacity_kwh = COALESCE(%s, capacity_kwh),
                        serial_number = COALESCE(%s, serial_number),
                        part_number = COALESCE(%s, part_number),
                        raw_data = COALESCE(%s::jsonb, raw_data),
                        updated_at = NOW()
                    WHERE id = %s
                    RETURNING id
                    """,
                    (
                        name,
                        capacity_kwh,
                        serial_number,
                        part_number,
                        json.dumps(raw_data) if raw_data else None,
                        existing[0],
                    ),
                )
                return cur.fetchone()[0]
            else:
                # Insert
                cur.execute(
                    """
                    INSERT INTO battery_banks
                    (location_id, energy_site_id, battery_index, name, capacity_kwh, serial_number, part_number, raw_data)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                    RETURNING id
                    """,
                    (
                        location_id,
                        energy_site_id,
                        battery_index,
                        name,
                        capacity_kwh,
                        serial_number,
                        part_number,
                        json.dumps(raw_data) if raw_data else None,
                    ),
                )
                return cur.fetchone()[0]


def insert_irradiance_reading(
    location_id: int,
    timestamp: datetime,
    ghi_clear_sky: Optional[float] = None,
    ghi_cloudy_sky: Optional[float] = None,
    dni_clear_sky: Optional[float] = None,
    dni_cloudy_sky: Optional[float] = None,
    dhi_clear_sky: Optional[float] = None,
    dhi_cloudy_sky: Optional[float] = None,
    source: str = "openweather",
    raw_data: Optional[Dict[str, Any]] = None,
) -> int:
    """Insert an irradiance reading with flattened raw_data fields and return the ID."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            base_columns = {
                "location_id": location_id,
                "timestamp": timestamp,
                "ghi_clear_sky": ghi_clear_sky,
                "ghi_cloudy_sky": ghi_cloudy_sky,
                "dni_clear_sky": dni_clear_sky,
                "dni_cloudy_sky": dni_cloudy_sky,
                "dhi_clear_sky": dhi_clear_sky,
                "dhi_cloudy_sky": dhi_cloudy_sky,
                "source": source,
                "raw_data": json.dumps(raw_data) if raw_data else None,
            }
            return _insert_with_flattened_raw_data(
                cur, "irradiance_readings", base_columns, raw_data
            )


def insert_water_reading(
    location_id: int,
    timestamp: datetime,
    flow_rate_gpm: Optional[float] = None,
    usage_gallons: Optional[float] = None,
    usage_period: Optional[str] = None,
    source: str = "flume",
    raw_data: Optional[Dict[str, Any]] = None,
) -> int:
    """Insert a water reading with flattened raw_data fields and return the ID."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            base_columns = {
                "location_id": location_id,
                "timestamp": timestamp,
                "flow_rate_gpm": flow_rate_gpm,
                "usage_gallons": usage_gallons,
                "usage_period": usage_period,
                "source": source,
                "raw_data": json.dumps(raw_data) if raw_data else None,
            }
            return _insert_with_flattened_raw_data(cur, "water_readings", base_columns, raw_data)


def insert_sprinkler_run(
    location_id: int,
    device_id: str,
    zone_id: str,
    start_time: datetime,
    end_time: datetime,
    zone_name: Optional[str] = None,
    zone_number: Optional[int] = None,
    duration_seconds: Optional[int] = None,
    schedule_type: Optional[str] = None,
    source: str = "rachio",
    raw_data: Optional[Dict[str, Any]] = None,
) -> int:
    """Insert a sprinkler run record with flattened raw_data fields and return the ID."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            base_columns = {
                "location_id": location_id,
                "device_id": device_id,
                "zone_id": zone_id,
                "zone_name": zone_name,
                "zone_number": zone_number,
                "start_time": start_time,
                "end_time": end_time,
                "duration_seconds": duration_seconds,
                "schedule_type": schedule_type,
                "source": source,
                "raw_data": json.dumps(raw_data) if raw_data else None,
            }
            return _insert_with_flattened_raw_data(cur, "sprinkler_runs", base_columns, raw_data)


def get_sprinkler_run_exists(
    location_id: int,
    device_id: str,
    zone_id: str,
    start_time: datetime,
) -> bool:
    """
    Check if a sprinkler run already exists in the database.

    This is used to avoid inserting duplicate runs when polling the API.

    Args:
        location_id: Location ID
        device_id: Device ID
        zone_id: Zone ID
        start_time: Start time of the run

    Returns:
        True if the run already exists, False otherwise
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1 FROM sprinkler_runs
                WHERE location_id = %s
                  AND device_id = %s
                  AND zone_id = %s
                  AND start_time = %s
                LIMIT 1
                """,
                (location_id, device_id, zone_id, start_time),
            )
            return cur.fetchone() is not None


def insert_propane_reading(
    location_id: int,
    device_id: str,
    timestamp: datetime,
    tank_level_percent: Optional[float] = None,
    tank_level_gallons: Optional[float] = None,
    capacity_gallons: Optional[float] = None,
    temperature_f: Optional[float] = None,
    battery_status: Optional[str] = None,
    battery_warn: Optional[bool] = None,
    battery_crit: Optional[bool] = None,
    fuel_type: Optional[str] = None,
    source: str = "tankutility",
    raw_data: Optional[Dict[str, Any]] = None,
) -> int:
    """Insert a propane tank reading with flattened raw_data fields and return the ID."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            base_columns = {
                "location_id": location_id,
                "device_id": device_id,
                "timestamp": timestamp,
                "tank_level_percent": tank_level_percent,
                "tank_level_gallons": tank_level_gallons,
                "capacity_gallons": capacity_gallons,
                "temperature_f": temperature_f,
                "battery_status": battery_status,
                "battery_warn": battery_warn,
                "battery_crit": battery_crit,
                "fuel_type": fuel_type,
                "source": source,
                "raw_data": json.dumps(raw_data) if raw_data else None,
            }
            return _insert_with_flattened_raw_data(cur, "propane_readings", base_columns, raw_data)


def insert_pool_reading(
    location_id: int,
    serial_number: str,
    timestamp: datetime,
    pool_temp: Optional[int] = None,
    spa_temp: Optional[int] = None,
    air_temp: Optional[int] = None,
    pool_set_point: Optional[int] = None,
    spa_set_point: Optional[int] = None,
    pool_pump: Optional[bool] = None,
    spa_pump: Optional[bool] = None,
    pool_heater: Optional[bool] = None,
    spa_heater: Optional[bool] = None,
    source: str = "iaqualink",
    raw_data: Optional[Dict[str, Any]] = None,
) -> int:
    """Insert a pool reading with flattened raw_data fields and return the ID."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            base_columns = {
                "location_id": location_id,
                "serial_number": serial_number,
                "timestamp": timestamp,
                "pool_temp": pool_temp,
                "spa_temp": spa_temp,
                "air_temp": air_temp,
                "pool_set_point": pool_set_point,
                "spa_set_point": spa_set_point,
                "pool_pump": pool_pump,
                "spa_pump": spa_pump,
                "pool_heater": pool_heater,
                "spa_heater": spa_heater,
                "source": source,
                "raw_data": json.dumps(raw_data) if raw_data else None,
            }
            return _insert_with_flattened_raw_data(cur, "pool_readings", base_columns, raw_data)


# =============================================================================
# Enphase Multi-App Token Management
# =============================================================================


def create_enphase_app_tokens_table() -> None:
    """
    Create the enphase_app_tokens table for multi-app token storage.

    This table stores OAuth tokens per Enphase app, allowing rotation
    across multiple apps to distribute API rate limits.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS enphase_app_tokens (
                    id SERIAL PRIMARY KEY,
                    app_index INTEGER NOT NULL UNIQUE,
                    access_token TEXT,
                    refresh_token TEXT,
                    token_expires_at TIMESTAMPTZ,
                    api_calls_today INTEGER DEFAULT 0,
                    last_reset_date DATE,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_enphase_app_tokens_app_index
                    ON enphase_app_tokens(app_index);
            """
            )


def get_enphase_app_tokens(app_index: int) -> Optional[Dict[str, Any]]:
    """
    Get tokens for a specific Enphase app.

    Args:
        app_index: The app index (1, 2, 3, ... N, or 0 for legacy)

    Returns:
        Dictionary with access_token, refresh_token, token_expires_at,
        api_calls_today, last_reset_date, or None if not found
    """
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM enphase_app_tokens WHERE app_index = %s",
                (app_index,),
            )
            row = cur.fetchone()
            if row:
                return {
                    "access_token": row.get("access_token"),
                    "refresh_token": row.get("refresh_token"),
                    "token_expires_at": row.get("token_expires_at"),
                    "api_calls_today": row.get("api_calls_today", 0),
                    "last_reset_date": row.get("last_reset_date"),
                }
            return None


def update_enphase_app_tokens(
    app_index: int,
    access_token: str,
    refresh_token: str,
    token_expires_at: datetime,
) -> None:
    """
    Insert or update tokens for a specific Enphase app.

    Args:
        app_index: The app index (1, 2, 3, ... N, or 0 for legacy)
        access_token: OAuth access token
        refresh_token: OAuth refresh token
        token_expires_at: Token expiration timestamp
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO enphase_app_tokens
                    (app_index, access_token, refresh_token, token_expires_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (app_index) DO UPDATE SET
                    access_token = EXCLUDED.access_token,
                    refresh_token = EXCLUDED.refresh_token,
                    token_expires_at = EXCLUDED.token_expires_at,
                    updated_at = NOW()
            """,
                (app_index, access_token, refresh_token, token_expires_at),
            )


def increment_enphase_app_api_calls(app_index: int) -> int:
    """
    Increment the API call counter for an Enphase app.

    Automatically resets the counter if it's a new day (UTC).

    Args:
        app_index: The app index to increment

    Returns:
        The new API call count for today
    """
    from datetime import date

    today = date.today()

    with get_connection() as conn:
        with conn.cursor() as cur:
            # First, check if we need to reset (new day)
            cur.execute(
                """
                UPDATE enphase_app_tokens
                SET api_calls_today = CASE
                        WHEN last_reset_date IS NULL OR last_reset_date < %s
                        THEN 1
                        ELSE api_calls_today + 1
                    END,
                    last_reset_date = %s,
                    updated_at = NOW()
                WHERE app_index = %s
                RETURNING api_calls_today
            """,
                (today, today, app_index),
            )
            result = cur.fetchone()
            if result:
                return result[0]

            # If no row exists, create one
            cur.execute(
                """
                INSERT INTO enphase_app_tokens
                    (app_index, api_calls_today, last_reset_date)
                VALUES (%s, 1, %s)
                ON CONFLICT (app_index) DO UPDATE SET
                    api_calls_today = enphase_app_tokens.api_calls_today + 1,
                    updated_at = NOW()
                RETURNING api_calls_today
            """,
                (app_index, today),
            )
            result = cur.fetchone()
            return result[0] if result else 1


def insert_system_reading(
    timestamp: datetime,
    cpu_percent: Optional[float] = None,
    memory_percent: Optional[float] = None,
    memory_used_mb: Optional[float] = None,
    memory_total_mb: Optional[float] = None,
    disk_percent: Optional[float] = None,
    disk_used_gb: Optional[float] = None,
    disk_total_gb: Optional[float] = None,
    source: str = "psutil",
) -> int:
    """Insert a system stats reading and return the ID."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO system_readings
                (timestamp, cpu_percent, memory_percent, memory_used_mb, memory_total_mb,
                 disk_percent, disk_used_gb, disk_total_gb, source)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    timestamp,
                    cpu_percent,
                    memory_percent,
                    memory_used_mb,
                    memory_total_mb,
                    disk_percent,
                    disk_used_gb,
                    disk_total_gb,
                    source,
                ),
            )
            return cur.fetchone()[0]


def get_all_enphase_app_stats() -> List[Dict[str, Any]]:
    """
    Get statistics for all Enphase apps.

    Returns:
        List of dictionaries with app_index, api_calls_today, last_reset_date,
        and token status for each app
    """
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    app_index,
                    api_calls_today,
                    last_reset_date,
                    CASE WHEN access_token IS NOT NULL THEN TRUE ELSE FALSE END as has_token,
                    token_expires_at,
                    updated_at
                FROM enphase_app_tokens
                ORDER BY app_index
            """
            )
            return [dict(row) for row in cur.fetchall()]


def _flatten_jsonb_fields(raw_data: Any) -> Dict[str, Any]:
    """
    Recursively flatten a JSONB object into a flat dictionary.

    Nested objects are flattened with underscore notation (e.g., "parent_child").
    Arrays are converted to JSON strings.
    Special characters in keys are replaced with underscores.

    Args:
        raw_data: JSONB data (can be dict, list, or already parsed)

    Returns:
        Flattened dictionary with all fields
    """
    if raw_data is None:
        return {}

    # Parse if string
    if isinstance(raw_data, str):
        try:
            raw_data = json.loads(raw_data)
        except (json.JSONDecodeError, TypeError):
            return {}

    def _sanitize_key(key: str) -> str:
        """Sanitize key name for SQL column (replace dots, spaces, etc. with underscore)."""
        return key.replace(".", "_").replace(" ", "_").replace("-", "_")

    def _flatten(obj: Any, parent_key: str = "", sep: str = "_") -> Dict[str, Any]:
        """Recursively flatten nested dictionary."""
        items = []

        if isinstance(obj, dict):
            for k, v in obj.items():
                sanitized_k = _sanitize_key(k)
                new_key = f"{parent_key}{sep}{sanitized_k}" if parent_key else sanitized_k
                if isinstance(v, dict):
                    items.extend(_flatten(v, new_key, sep=sep).items())
                elif isinstance(v, list):
                    # Store the entire array as JSON
                    items.append((new_key, json.dumps(v)))
                    # Also flatten objects within the array to extract their fields
                    # This allows fields like raw_data_obs_solar_radiation to be extracted
                    # from arrays like [{"solar_radiation": 0, ...}]
                    if v and isinstance(v[0], dict):
                        # Flatten the first object in the array using the same key prefix
                        # This extracts fields like obs.solar_radiation -> obs_solar_radiation
                        items.extend(_flatten(v[0], new_key, sep=sep).items())
                else:
                    items.append((new_key, v))
        elif isinstance(obj, list):
            # Store the entire array as JSON
            items.append((parent_key, json.dumps(obj)))
            # Also flatten objects within the array if present
            if obj and isinstance(obj[0], dict):
                items.extend(_flatten(obj[0], parent_key, sep=sep).items())
        else:
            items.append((parent_key, obj))

        return dict(items)

    return _flatten(raw_data)


# =============================================================================
# Enphase Local Gateway Functions
# =============================================================================


def insert_enphase_local_reading(
    location_id: int,
    gateway_serial: str,
    timestamp: datetime,
    power_produced: Optional[float] = None,
    power_consumed: Optional[float] = None,
    power_net: Optional[float] = None,
    grid_voltage_l1: Optional[float] = None,
    grid_voltage_l2: Optional[float] = None,
    grid_frequency: Optional[float] = None,
    energy_produced_today_wh: Optional[float] = None,
    energy_consumed_today_wh: Optional[float] = None,
    energy_lifetime_wh: Optional[float] = None,
    source: str = "enphase_local",
    raw_data: Optional[Dict[str, Any]] = None,
) -> int:
    """Insert an Enphase local gateway reading with flattened raw_data fields and return the ID."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            base_columns = {
                "location_id": location_id,
                "gateway_serial": gateway_serial,
                "timestamp": timestamp,
                "power_produced": power_produced,
                "power_consumed": power_consumed,
                "power_net": power_net,
                "grid_voltage_l1": grid_voltage_l1,
                "grid_voltage_l2": grid_voltage_l2,
                "grid_frequency": grid_frequency,
                "energy_produced_today_wh": energy_produced_today_wh,
                "energy_consumed_today_wh": energy_consumed_today_wh,
                "energy_lifetime_wh": energy_lifetime_wh,
                "source": source,
                "raw_data": json.dumps(raw_data) if raw_data else None,
            }
            return _insert_with_flattened_raw_data(
                cur, "enphase_local_readings", base_columns, raw_data
            )


def get_enphase_gateway_token(gateway_serial: str) -> Optional[Dict[str, Any]]:
    """
    Get token for a specific Enphase gateway.

    Args:
        gateway_serial: Serial number of the gateway

    Returns:
        Dictionary with token, gateway_host, token_expires_at, or None if not found
    """
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT gateway_serial, gateway_host, token, token_expires_at, location_id
                FROM enphase_gateway_tokens
                WHERE gateway_serial = %s
                """,
                (gateway_serial,),
            )
            row = cur.fetchone()
            return dict(row) if row else None


def get_all_enphase_gateway_tokens() -> List[Dict[str, Any]]:
    """
    Get all stored Enphase gateway tokens.

    Returns:
        List of dictionaries with gateway info and tokens
    """
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT gateway_serial, gateway_host, token, token_expires_at, location_id, updated_at
                FROM enphase_gateway_tokens
                ORDER BY gateway_serial
                """
            )
            return [dict(row) for row in cur.fetchall()]


def upsert_enphase_gateway_token(
    gateway_serial: str,
    gateway_host: str,
    token: str,
    token_expires_at: Optional[datetime] = None,
    location_id: Optional[int] = None,
) -> int:
    """
    Insert or update an Enphase gateway token.

    Args:
        gateway_serial: Serial number of the gateway
        gateway_host: IP address or hostname of the gateway
        token: Gateway access token
        token_expires_at: Token expiration timestamp
        location_id: Associated location ID (optional)

    Returns:
        The token record ID
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO enphase_gateway_tokens
                    (gateway_serial, gateway_host, token, token_expires_at, location_id)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (gateway_serial) DO UPDATE SET
                    gateway_host = EXCLUDED.gateway_host,
                    token = EXCLUDED.token,
                    token_expires_at = EXCLUDED.token_expires_at,
                    location_id = COALESCE(EXCLUDED.location_id, enphase_gateway_tokens.location_id),
                    updated_at = NOW()
                RETURNING id
                """,
                (gateway_serial, gateway_host, token, token_expires_at, location_id),
            )
            return cur.fetchone()[0]


def delete_enphase_gateway_token(gateway_serial: str) -> bool:
    """
    Delete an Enphase gateway token.

    Args:
        gateway_serial: Serial number of the gateway

    Returns:
        True if a token was deleted, False if not found
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM enphase_gateway_tokens
                WHERE gateway_serial = %s
                """,
                (gateway_serial,),
            )
            return cur.rowcount > 0


# =============================================================================
# Span Panel Functions
# =============================================================================


def get_span_panel_token(panel_serial: str) -> Optional[Dict[str, Any]]:
    """
    Get token for a specific Span panel.

    Args:
        panel_serial: Serial number of the panel

    Returns:
        Dictionary with token, panel_host, panel_name, etc., or None if not found
    """
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT panel_serial, panel_host, panel_name, token, token_created_at, location_id
                FROM span_panel_tokens
                WHERE panel_serial = %s
                """,
                (panel_serial,),
            )
            row = cur.fetchone()
            return dict(row) if row else None


def get_span_panel_token_by_host(panel_host: str) -> Optional[Dict[str, Any]]:
    """
    Get token for a Span panel by host address.

    Args:
        panel_host: IP address or hostname of the panel

    Returns:
        Dictionary with token info, or None if not found
    """
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT panel_serial, panel_host, panel_name, token, token_created_at, location_id
                FROM span_panel_tokens
                WHERE panel_host = %s
                """,
                (panel_host,),
            )
            row = cur.fetchone()
            return dict(row) if row else None


def get_all_span_panel_tokens() -> List[Dict[str, Any]]:
    """
    Get all stored Span panel tokens.

    Returns:
        List of dictionaries with panel info and tokens
    """
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT panel_serial, panel_host, panel_name, token, token_created_at,
                       location_id, created_at, updated_at
                FROM span_panel_tokens
                ORDER BY panel_name, panel_serial
                """
            )
            return [dict(row) for row in cur.fetchall()]


def upsert_span_panel_token(
    panel_serial: str,
    panel_host: str,
    token: str,
    panel_name: Optional[str] = None,
    token_created_at: Optional[datetime] = None,
    location_id: Optional[int] = None,
) -> int:
    """
    Insert or update a Span panel token.

    Args:
        panel_serial: Serial number of the panel
        panel_host: IP address or hostname of the panel
        token: Panel access token
        panel_name: Human-readable name for the panel
        token_created_at: When the token was created
        location_id: Associated location ID (optional)

    Returns:
        The token record ID
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO span_panel_tokens
                    (panel_serial, panel_host, panel_name, token, token_created_at, location_id)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (panel_serial) DO UPDATE SET
                    panel_host = EXCLUDED.panel_host,
                    panel_name = COALESCE(EXCLUDED.panel_name, span_panel_tokens.panel_name),
                    token = EXCLUDED.token,
                    token_created_at = EXCLUDED.token_created_at,
                    location_id = COALESCE(EXCLUDED.location_id, span_panel_tokens.location_id),
                    updated_at = NOW()
                RETURNING id
                """,
                (panel_serial, panel_host, panel_name, token, token_created_at, location_id),
            )
            return cur.fetchone()[0]


def delete_span_panel_token(panel_serial: str) -> bool:
    """
    Delete a Span panel token.

    Args:
        panel_serial: Serial number of the panel

    Returns:
        True if a token was deleted, False if not found
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM span_panel_tokens
                WHERE panel_serial = %s
                """,
                (panel_serial,),
            )
            return cur.rowcount > 0


def insert_span_panel_reading(
    location_id: int,
    panel_serial: str,
    timestamp: datetime,
    instant_grid_power_w: Optional[float] = None,
    feedthrough_power_w: Optional[float] = None,
    main_relay_state: Optional[str] = None,
    dsm_grid_state: Optional[str] = None,
    dsm_state: Optional[str] = None,
    current_run_config: Optional[str] = None,
    door_state: Optional[str] = None,
    firmware_version: Optional[str] = None,
    uptime_seconds: Optional[int] = None,
    battery_soe_percent: Optional[float] = None,
    eth0_link: Optional[bool] = None,
    wlan_link: Optional[bool] = None,
    wwan_link: Optional[bool] = None,
    source: str = "span",
    raw_data: Optional[Dict[str, Any]] = None,
) -> int:
    """
    Insert a Span panel reading and return the ID.

    Args:
        location_id: Database location ID
        panel_serial: Serial number of the panel
        timestamp: Reading timestamp
        instant_grid_power_w: Total grid power in watts
        feedthrough_power_w: Power through non-Span breakers
        main_relay_state: Main relay state (OPEN/CLOSED)
        dsm_grid_state: DSM grid state
        dsm_state: DSM state
        current_run_config: Current run configuration
        door_state: Panel door state (OPEN/CLOSED)
        firmware_version: Panel firmware version
        uptime_seconds: Panel uptime in seconds
        battery_soe_percent: Battery state of energy percentage
        eth0_link: Ethernet link status
        wlan_link: WiFi link status
        wwan_link: Cellular link status
        source: Data source identifier
        raw_data: Raw API response data

    Returns:
        The inserted reading ID
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO span_panel_readings (
                    location_id, panel_serial, timestamp, instant_grid_power_w,
                    feedthrough_power_w, main_relay_state, dsm_grid_state, dsm_state,
                    current_run_config, door_state, firmware_version, uptime_seconds,
                    battery_soe_percent, eth0_link, wlan_link, wwan_link, source, raw_data
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    location_id,
                    panel_serial,
                    timestamp,
                    instant_grid_power_w,
                    feedthrough_power_w,
                    main_relay_state,
                    dsm_grid_state,
                    dsm_state,
                    current_run_config,
                    door_state,
                    firmware_version,
                    uptime_seconds,
                    battery_soe_percent,
                    eth0_link,
                    wlan_link,
                    wwan_link,
                    source,
                    json.dumps(raw_data) if raw_data else None,
                ),
            )
            return cur.fetchone()[0]


def insert_span_circuit_readings(
    location_id: int,
    panel_serial: str,
    timestamp: datetime,
    circuits: List[Dict[str, Any]],
    source: str = "span",
) -> int:
    """
    Bulk insert Span circuit readings.

    Args:
        location_id: Database location ID
        panel_serial: Serial number of the panel
        timestamp: Reading timestamp
        circuits: List of circuit data dictionaries
        source: Data source identifier

    Returns:
        Number of circuits inserted
    """
    if not circuits:
        return 0

    with get_connection() as conn:
        with conn.cursor() as cur:
            for circuit in circuits:
                tabs_json = json.dumps(circuit.get("tabs")) if circuit.get("tabs") else None
                raw_data_json = (
                    json.dumps(circuit.get("raw_data")) if circuit.get("raw_data") else None
                )

                cur.execute(
                    """
                    INSERT INTO span_circuit_readings (
                        location_id, panel_serial, timestamp, circuit_id, circuit_name,
                        tabs, instant_power_w, import_energy_wh, export_energy_wh,
                        relay_state, priority, is_user_controllable, is_sheddable,
                        is_never_backup, source, raw_data
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        location_id,
                        panel_serial,
                        timestamp,
                        circuit.get("circuit_id"),
                        circuit.get("circuit_name"),
                        tabs_json,
                        circuit.get("instant_power_w"),
                        circuit.get("import_energy_wh"),
                        circuit.get("export_energy_wh"),
                        circuit.get("relay_state"),
                        circuit.get("priority"),
                        circuit.get("is_user_controllable"),
                        circuit.get("is_sheddable"),
                        circuit.get("is_never_backup"),
                        source,
                        raw_data_json,
                    ),
                )

            return len(circuits)


def get_last_span_circuit_reading_time(panel_serial: str) -> Optional[datetime]:
    """
    Get the timestamp of the most recent circuit reading for a panel.

    Args:
        panel_serial: Serial number of the panel

    Returns:
        Timestamp of last circuit reading, or None if no readings exist
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT MAX(timestamp) FROM span_circuit_readings
                WHERE panel_serial = %s
                """,
                (panel_serial,),
            )
            result = cur.fetchone()
            return result[0] if result and result[0] else None


# =============================================================================
# Fetch Run Summary Functions
# =============================================================================


def insert_fetch_run_summary(
    started_at: datetime,
    completed_at: Optional[datetime] = None,
    status: str = "running",
    total_data_points: int = 0,
    integrations_summary: Optional[Dict[str, Any]] = None,
    error_message: Optional[str] = None,
) -> int:
    """
    Insert a fetch run summary record.

    Args:
        started_at: When the fetch run started
        completed_at: When the fetch run completed (None if still running)
        status: Status of the run (running, success, error)
        total_data_points: Total number of data points inserted
        integrations_summary: Dict with counts per integration
        error_message: Error message if status is error

    Returns:
        The inserted row ID
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO fetch_run_summaries (
                    started_at, completed_at, status, total_data_points,
                    integrations_summary, error_message
                ) VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    started_at,
                    completed_at,
                    status,
                    total_data_points,
                    json.dumps(integrations_summary) if integrations_summary else None,
                    error_message,
                ),
            )
            return cur.fetchone()[0]


def update_fetch_run_summary(
    run_id: int,
    completed_at: Optional[datetime] = None,
    status: Optional[str] = None,
    total_data_points: Optional[int] = None,
    integrations_summary: Optional[Dict[str, Any]] = None,
    error_message: Optional[str] = None,
) -> None:
    """
    Update a fetch run summary record.

    Args:
        run_id: ID of the run to update
        completed_at: When the fetch run completed
        status: Status of the run
        total_data_points: Total number of data points inserted
        integrations_summary: Dict with counts per integration
        error_message: Error message if status is error
    """
    updates = []
    values = []

    if completed_at is not None:
        updates.append("completed_at = %s")
        values.append(completed_at)
    if status is not None:
        updates.append("status = %s")
        values.append(status)
    if total_data_points is not None:
        updates.append("total_data_points = %s")
        values.append(total_data_points)
    if integrations_summary is not None:
        updates.append("integrations_summary = %s")
        values.append(json.dumps(integrations_summary))
    if error_message is not None:
        updates.append("error_message = %s")
        values.append(error_message)

    if not updates:
        return

    values.append(run_id)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE fetch_run_summaries SET {', '.join(updates)} WHERE id = %s",
                tuple(values),
            )


def get_fetch_run_summaries(limit: int = 10) -> List[Dict[str, Any]]:
    """
    Get the most recent fetch run summaries.

    Args:
        limit: Maximum number of summaries to return

    Returns:
        List of fetch run summary dicts, ordered by started_at descending
    """
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT * FROM fetch_run_summaries
                ORDER BY started_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            return _rows_to_dicts(cur.fetchall(), ["integrations_summary"])


def prune_fetch_run_summaries(keep_count: int = 10) -> int:
    """
    Delete old fetch run summaries, keeping only the most recent ones.

    Args:
        keep_count: Number of recent summaries to keep

    Returns:
        Number of rows deleted
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM fetch_run_summaries
                WHERE id NOT IN (
                    SELECT id FROM fetch_run_summaries
                    ORDER BY started_at DESC
                    LIMIT %s
                )
                """,
                (keep_count,),
            )
            return cur.rowcount


# =============================================================================
# Span Circuit Aggregation/Pruning Functions
# =============================================================================


def aggregate_span_circuit_readings(
    last_days: int = 30,
    bucket_minutes: int = 30,
) -> Dict[str, int]:
    """
    Aggregate old span circuit readings into time buckets to reduce storage.

    For readings older than `last_days` days, this function:
    1. Groups readings by (panel_serial, circuit_id, time_bucket)
    2. For each group with multiple readings:
       - Averages instant_power_w
       - Sums import_energy_wh and export_energy_wh
       - Keeps the latest timestamp
       - Deletes original readings and inserts the aggregated one

    Args:
        last_days: Only aggregate readings older than this many days
        bucket_minutes: Time bucket size in minutes for aggregation

    Returns:
        Dict with counts: rows_deleted, rows_inserted, buckets_processed
    """
    from datetime import timedelta

    cutoff_date = datetime.now(timezone.utc) - timedelta(days=last_days)
    bucket_seconds = bucket_minutes * 60

    rows_deleted = 0
    rows_inserted = 0
    buckets_processed = 0

    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Find all readings older than cutoff, grouped into time buckets
            # We use date_trunc with custom interval via extract and floor
            cur.execute(
                """
                WITH bucketed AS (
                    SELECT
                        id,
                        location_id,
                        panel_serial,
                        circuit_id,
                        circuit_name,
                        tabs,
                        instant_power_w,
                        import_energy_wh,
                        export_energy_wh,
                        relay_state,
                        priority,
                        is_user_controllable,
                        is_sheddable,
                        is_never_backup,
                        source,
                        timestamp,
                        -- Create bucket key: floor timestamp to bucket_minutes
                        TO_TIMESTAMP(
                            FLOOR(EXTRACT(EPOCH FROM timestamp) / %s) * %s
                        ) AT TIME ZONE 'UTC' AS bucket_start
                    FROM span_circuit_readings
                    WHERE timestamp < %s
                )
                SELECT
                    panel_serial,
                    circuit_id,
                    bucket_start,
                    COUNT(*) as reading_count,
                    ARRAY_AGG(id ORDER BY timestamp DESC) as ids,
                    -- Aggregated values
                    AVG(instant_power_w) as avg_power_w,
                    SUM(import_energy_wh) as sum_import_wh,
                    SUM(export_energy_wh) as sum_export_wh,
                    -- Keep values from latest reading
                    (ARRAY_AGG(location_id ORDER BY timestamp DESC))[1] as location_id,
                    (ARRAY_AGG(circuit_name ORDER BY timestamp DESC))[1] as circuit_name,
                    (ARRAY_AGG(tabs ORDER BY timestamp DESC))[1] as tabs,
                    (ARRAY_AGG(relay_state ORDER BY timestamp DESC))[1] as relay_state,
                    (ARRAY_AGG(priority ORDER BY timestamp DESC))[1] as priority,
                    (ARRAY_AGG(is_user_controllable ORDER BY timestamp DESC))[1] as is_user_controllable,
                    (ARRAY_AGG(is_sheddable ORDER BY timestamp DESC))[1] as is_sheddable,
                    (ARRAY_AGG(is_never_backup ORDER BY timestamp DESC))[1] as is_never_backup,
                    (ARRAY_AGG(source ORDER BY timestamp DESC))[1] as source,
                    MAX(timestamp) as latest_timestamp
                FROM bucketed
                GROUP BY panel_serial, circuit_id, bucket_start
                HAVING COUNT(*) > 1
                ORDER BY panel_serial, circuit_id, bucket_start
                """,
                (bucket_seconds, bucket_seconds, cutoff_date),
            )

            buckets = cur.fetchall()

            for bucket in buckets:
                buckets_processed += 1
                ids_to_delete = bucket["ids"]
                reading_count = bucket["reading_count"]

                # Delete all readings in this bucket
                cur.execute(
                    "DELETE FROM span_circuit_readings WHERE id = ANY(%s)",
                    (ids_to_delete,),
                )
                rows_deleted += cur.rowcount

                # Insert aggregated reading
                cur.execute(
                    """
                    INSERT INTO span_circuit_readings (
                        location_id, panel_serial, timestamp, circuit_id, circuit_name,
                        tabs, instant_power_w, import_energy_wh, export_energy_wh,
                        relay_state, priority, is_user_controllable, is_sheddable,
                        is_never_backup, source, raw_data
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        bucket["location_id"],
                        bucket["panel_serial"],
                        bucket["latest_timestamp"],
                        bucket["circuit_id"],
                        bucket["circuit_name"],
                        bucket["tabs"],
                        bucket["avg_power_w"],
                        bucket["sum_import_wh"],
                        bucket["sum_export_wh"],
                        bucket["relay_state"],
                        bucket["priority"],
                        bucket["is_user_controllable"],
                        bucket["is_sheddable"],
                        bucket["is_never_backup"],
                        bucket["source"],
                        json.dumps({"aggregated": True, "original_count": reading_count}),
                    ),
                )
                rows_inserted += 1

        conn.commit()

    return {
        "rows_deleted": rows_deleted,
        "rows_inserted": rows_inserted,
        "buckets_processed": buckets_processed,
        "net_reduction": rows_deleted - rows_inserted,
    }
