"""
Power metric SQL helpers shared by Grafana dashboard generation and email reports.
"""

from __future__ import annotations

from datetime import datetime

TESLA_EXPORT_GAUGE_MAX_KWH = 80.0


def sql_string_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def tesla_export_day_ctes(tz_lit: str, prefix: str = "") -> str:
    """Shared CTEs: bounds → minute_power → ordered → segments (Tesla export integration)."""
    bounds = f"{prefix}bounds"
    minute_power = f"{prefix}minute_power"
    ordered = f"{prefix}ordered"
    segments = f"{prefix}segments"
    return f"""{bounds} AS (
  SELECT
    (date_trunc('day', CURRENT_TIMESTAMP AT TIME ZONE {tz_lit}) AT TIME ZONE {tz_lit}) AS start_utc,
    ((date_trunc('day', CURRENT_TIMESTAMP AT TIME ZONE {tz_lit}) + interval '1 day') AT TIME ZONE {tz_lit}) AS end_utc
),
{minute_power} AS (
  SELECT
    pr.location_id,
    date_trunc('minute', pr.timestamp) AS ts_min,
    SUM(COALESCE(pr.power_exported, 0::float8))::float8 AS export_w
  FROM power_readings pr
  JOIN locations l ON pr.location_id = l.id
  CROSS JOIN {bounds} b
  WHERE pr.source = 'tesla'
    AND l.name IN ($location)
    AND pr.timestamp >= b.start_utc
    AND pr.timestamp < b.end_utc
  GROUP BY pr.location_id, date_trunc('minute', pr.timestamp)
),
{ordered} AS (
  SELECT
    location_id,
    ts_min,
    export_w,
    LEAD(ts_min) OVER (PARTITION BY location_id ORDER BY ts_min) AS next_ts,
    LEAD(export_w) OVER (PARTITION BY location_id ORDER BY ts_min) AS next_w
  FROM {minute_power}
),
{segments} AS (
  SELECT
    o.location_id,
    CASE
      WHEN o.next_ts IS NOT NULL THEN
        EXTRACT(EPOCH FROM (o.next_ts - o.ts_min))
        * (o.export_w + COALESCE(o.next_w, o.export_w)) / 2.0
      ELSE
        EXTRACT(EPOCH FROM (
          LEAST(CURRENT_TIMESTAMP, (SELECT end_utc FROM {bounds})) - o.ts_min
        )) * o.export_w
    END / 3600000.0 AS kwh
  FROM {ordered} o
)"""


def tesla_import_day_ctes(tz_lit: str, prefix: str = "") -> str:
    """Shared CTEs: bounds → minute_power → ordered → segments (Tesla import integration)."""
    bounds = f"{prefix}bounds"
    minute_power = f"{prefix}minute_power"
    ordered = f"{prefix}ordered"
    segments = f"{prefix}segments"
    return f"""{bounds} AS (
  SELECT
    (date_trunc('day', CURRENT_TIMESTAMP AT TIME ZONE {tz_lit}) AT TIME ZONE {tz_lit}) AS start_utc,
    ((date_trunc('day', CURRENT_TIMESTAMP AT TIME ZONE {tz_lit}) + interval '1 day') AT TIME ZONE {tz_lit}) AS end_utc
),
{minute_power} AS (
  SELECT
    pr.location_id,
    date_trunc('minute', pr.timestamp) AS ts_min,
    SUM(COALESCE(pr.power_imported, 0::float8))::float8 AS import_w
  FROM power_readings pr
  JOIN locations l ON pr.location_id = l.id
  CROSS JOIN {bounds} b
  WHERE pr.source = 'tesla'
    AND l.name IN ($location)
    AND pr.timestamp >= b.start_utc
    AND pr.timestamp < b.end_utc
  GROUP BY pr.location_id, date_trunc('minute', pr.timestamp)
),
{ordered} AS (
  SELECT
    location_id,
    ts_min,
    import_w,
    LEAD(ts_min) OVER (PARTITION BY location_id ORDER BY ts_min) AS next_ts,
    LEAD(import_w) OVER (PARTITION BY location_id ORDER BY ts_min) AS next_w
  FROM {minute_power}
),
{segments} AS (
  SELECT
    o.location_id,
    CASE
      WHEN o.next_ts IS NOT NULL THEN
        EXTRACT(EPOCH FROM (o.next_ts - o.ts_min))
        * (o.import_w + COALESCE(o.next_w, o.import_w)) / 2.0
      ELSE
        EXTRACT(EPOCH FROM (
          LEAST(CURRENT_TIMESTAMP, (SELECT end_utc FROM {bounds})) - o.ts_min
        )) * o.import_w
    END / 3600000.0 AS kwh
  FROM {ordered} o
)"""


def sql_tesla_exported_today_kwh(all_day_timezone: str) -> str:
    """Standalone query: daily Tesla grid export (kWh); used by tests and ad-hoc panels."""
    tz_lit = sql_string_literal(all_day_timezone)
    return f"""WITH {tesla_export_day_ctes(tz_lit)}
SELECT
  COALESCE((SELECT SUM(kwh) FROM segments), 0) AS "Exported Today (kWh)",
  {TESLA_EXPORT_GAUGE_MAX_KWH!s}::float8 AS "Max"
"""


def sql_current_production_consumption_export(all_day_timezone: str) -> str:
    """Latest production/consumption plus Tesla import/export today (kWh); ignores Grafana range."""
    tz_lit = sql_string_literal(all_day_timezone)
    tesla_export_ctes = tesla_export_day_ctes(tz_lit, prefix="export_")
    tesla_import_ctes = tesla_import_day_ctes(tz_lit, prefix="import_")
    return f"""WITH latest_bucket AS (
  SELECT location_id, source, MAX(date_trunc('minute', timestamp)) as latest_minute
  FROM power_readings
  WHERE source IN ($source)
  GROUP BY location_id, source
),
production_data AS (
  SELECT
    COALESCE(SUM(pr.power_produced) / 1000.0, 0) AS production_kw,
    COALESCE(MAX(l.capacity_kw), 20) AS production_max
  FROM power_readings pr
  JOIN locations l ON pr.location_id = l.id
  JOIN latest_bucket lb ON pr.location_id = lb.location_id
    AND pr.source = lb.source
    AND date_trunc('minute', pr.timestamp) = lb.latest_minute
  WHERE pr.power_produced IS NOT NULL
    AND l.name IN ($location)
    AND pr.source IN ($source)
),
consumption_data AS (
  SELECT
    COALESCE(SUM(pr.power_consumed) / 1000.0, 0) AS consumption_kw,
    COALESCE(MAX(l.capacity_kw * 1.15), 20) AS consumption_max
  FROM power_readings pr
  JOIN locations l ON pr.location_id = l.id
  JOIN latest_bucket lb ON pr.location_id = lb.location_id
    AND pr.source = lb.source
    AND date_trunc('minute', pr.timestamp) = lb.latest_minute
  WHERE pr.power_consumed IS NOT NULL
    AND l.name IN ($location)
    AND pr.source IN ($source)
),
{tesla_export_ctes},
export_today AS (
  SELECT COALESCE(SUM(kwh), 0) AS export_kwh FROM export_segments
),
{tesla_import_ctes},
import_today AS (
  SELECT COALESCE(SUM(kwh), 0) AS import_kwh FROM import_segments
)
SELECT
  COALESCE((SELECT production_kw FROM production_data), 0) AS "Production (kW)",
  COALESCE((SELECT consumption_kw FROM consumption_data), 0) AS "Consumption (kW)",
  COALESCE((SELECT import_kwh FROM import_today), 0) AS "Imported Today (kWh)",
  COALESCE((SELECT export_kwh FROM export_today), 0) AS "Exported Today (kWh)",
  GREATEST(
    COALESCE((SELECT production_max FROM production_data), 20),
    COALESCE((SELECT consumption_max FROM consumption_data), 20)
  ) AS "Max"
"""


def sql_power_energy_kwh(
    location_id: int,
    start_utc: datetime,
    end_utc: datetime,
    power_column: str,
) -> str:
    """
    Trapezoidal integration of a power column (watts) over a time range for one location.

    power_column must be one of: power_produced, power_consumed, power_imported, power_exported
    """
    allowed = {
        "power_produced",
        "power_consumed",
        "power_imported",
        "power_exported",
    }
    if power_column not in allowed:
        raise ValueError(f"Invalid power column: {power_column}")
    return f"""
WITH minute_power AS (
  SELECT
    date_trunc('minute', pr.timestamp) AS ts_min,
    SUM(COALESCE(pr.{power_column}, 0::float8))::float8 AS power_w
  FROM power_readings pr
  WHERE pr.location_id = %(location_id)s
    AND pr.timestamp >= %(start_utc)s
    AND pr.timestamp < %(end_utc)s
    AND pr.{power_column} IS NOT NULL
  GROUP BY date_trunc('minute', pr.timestamp)
),
ordered AS (
  SELECT
    ts_min,
    power_w,
    LEAD(ts_min) OVER (ORDER BY ts_min) AS next_ts,
    LEAD(power_w) OVER (ORDER BY ts_min) AS next_w
  FROM minute_power
),
segments AS (
  SELECT
    CASE
      WHEN next_ts IS NOT NULL THEN
        EXTRACT(EPOCH FROM (next_ts - ts_min))
        * (power_w + COALESCE(next_w, power_w)) / 2.0
      ELSE
        EXTRACT(EPOCH FROM (%(end_utc)s - ts_min)) * power_w
    END / 3600000.0 AS kwh
  FROM ordered
)
SELECT COALESCE(SUM(kwh), 0) AS total_kwh FROM segments
"""
