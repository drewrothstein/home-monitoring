#!/usr/bin/env python3
"""
Generate Grafana dashboard JSON using grafanalib.

This script generates a dashboard that can be programmatically updated.
Run this script to regenerate the dashboard after making changes.
"""

import json
from datetime import date, timedelta
from typing import Any

from grafanalib.core import (
    GridPos,
    SqlTarget,
    Target,
)

from home_monitor.annotations_config import load_dashboard_annotations_config

# =============================================================================
# CONSTANTS
# =============================================================================

DATASOURCE = "Home Monitor PostgreSQL"
DASHBOARD_UID = "home-monitor"
DASHBOARD_TITLE = "Home Monitor"

# Colors
GREEN = "green"
YELLOW = "yellow"
ORANGE = "orange"
RED = "red"
BLUE = "blue"
TRANSPARENT = "transparent"
LIGHT_BLUE = "light-blue"
DARK_ORANGE = "dark-orange"

# Common legend calculations
LEGEND_CALCS_STANDARD = ["lastNotNull", "max", "mean"]
LEGEND_CALCS_WITH_SUM = ["lastNotNull", "max", "mean", "sum"]
LEGEND_CALCS_BATTERY = ["lastNotNull", "min", "max", "mean"]

# Gauge max for Tesla daily export (kWh); must match fieldConfig override on combined panel.
TESLA_EXPORT_GAUGE_MAX_KWH = 80.0


def threshold(color: str, value: float | None = None) -> dict[str, Any]:
    """Create a threshold step. value=None means base/default threshold."""
    return {"color": color, "value": value}


# =============================================================================
# SQL QUERIES
# =============================================================================

SQL_CURRENT_PRODUCTION = """WITH latest_bucket AS (
  SELECT location_id, source, MAX(date_trunc('minute', timestamp)) as latest_minute
  FROM power_readings
  WHERE source IN ($source)
  GROUP BY location_id, source
)
SELECT
  COALESCE(SUM(pr.power_produced) / 1000.0, 0) AS "Production (kW)",
  COALESCE(MAX(l.capacity_kw), 20) AS "Max"
FROM power_readings pr
JOIN locations l ON pr.location_id = l.id
JOIN latest_bucket lb ON pr.location_id = lb.location_id
  AND pr.source = lb.source
  AND date_trunc('minute', pr.timestamp) = lb.latest_minute
WHERE pr.power_produced IS NOT NULL
  AND l.name IN ($location)
  AND pr.source IN ($source)"""

SQL_CURRENT_CONSUMPTION = """WITH latest_bucket AS (
  SELECT location_id, source, MAX(date_trunc('minute', timestamp)) as latest_minute
  FROM power_readings
  WHERE source IN ($source)
  GROUP BY location_id, source
)
SELECT
  COALESCE(SUM(pr.power_consumed) / 1000.0, 0) AS "Consumption (kW)",
  COALESCE(MAX(l.capacity_kw * 1.15), 20) AS "Max"
FROM power_readings pr
JOIN locations l ON pr.location_id = l.id
JOIN latest_bucket lb ON pr.location_id = lb.location_id
  AND pr.source = lb.source
  AND date_trunc('minute', pr.timestamp) = lb.latest_minute
WHERE pr.power_consumed IS NOT NULL
  AND l.name IN ($location)
  AND pr.source IN ($source)"""

SQL_POWER_PRODUCTION = """SELECT
  date_trunc('minute', pr.timestamp) AS "time",
  l.name || ' (' || pr.source || ')' AS metric,
  SUM(pr.power_produced) / 1000.0 AS "Power Produced (kW)"
FROM power_readings pr
JOIN locations l ON pr.location_id = l.id
WHERE pr.timestamp >= $__timeFrom() AND pr.timestamp <= $__timeTo()
  AND pr.power_produced IS NOT NULL
  AND l.name IN ($location)
  AND pr.source IN ($source)
GROUP BY date_trunc('minute', pr.timestamp), l.name, pr.source
ORDER BY "time" """

SQL_POWER_CONSUMPTION = """SELECT
  date_trunc('minute', pr.timestamp) AS "time",
  l.name || ' (' || pr.source || ')' AS metric,
  SUM(pr.power_consumed) / 1000.0 AS "Power Consumed (kW)"
FROM power_readings pr
JOIN locations l ON pr.location_id = l.id
WHERE pr.timestamp >= $__timeFrom() AND pr.timestamp <= $__timeTo()
  AND pr.power_consumed IS NOT NULL
  AND l.name IN ($location)
  AND pr.source IN ($source)
GROUP BY date_trunc('minute', pr.timestamp), l.name, pr.source
ORDER BY "time" """

SQL_ENPHASE_POWER_PRODUCTION = """SELECT
  date_trunc('minute', pr.timestamp) AS "time",
  l.name || ' (enphase) - Production' AS metric,
  SUM(pr.power_produced) / 1000.0 AS "Power (kW)"
FROM power_readings pr
JOIN locations l ON pr.location_id = l.id
WHERE pr.timestamp >= $__timeFrom() AND pr.timestamp <= $__timeTo()
  AND pr.power_produced IS NOT NULL
  AND l.name IN ($location)
  AND pr.source = 'enphase'
GROUP BY date_trunc('minute', pr.timestamp), l.name, pr.source
ORDER BY "time" """


def sql_battery_soc(battery_index: int) -> str:
    """Generate SQL for battery state of charge by index."""
    fallback_rn = battery_index + 1
    return f"""-- Battery {battery_index + 1}: Get the latest reading for battery_index = {battery_index}
-- Uses battery_bank_id when available, otherwise falls back to readings within the latest minute
-- Only returns a value if there are enough distinct readings in that minute
SELECT
  l.name,
  COALESCE(
    (SELECT br.state_of_charge
     FROM battery_readings br
     INNER JOIN battery_banks bb ON br.battery_bank_id = bb.id
     WHERE br.location_id = l.id
       AND bb.battery_index = {battery_index}
       AND br.state_of_charge IS NOT NULL
     ORDER BY br.timestamp DESC
     LIMIT 1),
    (SELECT state_of_charge
     FROM (
       SELECT br.state_of_charge,
              ROW_NUMBER() OVER (PARTITION BY br.location_id ORDER BY br.timestamp DESC) as rn
       FROM battery_readings br
       WHERE br.location_id = l.id
         AND br.state_of_charge IS NOT NULL
         -- Only consider readings from the latest minute to avoid returning stale data
         AND br.timestamp >= (
           SELECT date_trunc('minute', MAX(br2.timestamp))
           FROM battery_readings br2
           WHERE br2.location_id = l.id
             AND br2.state_of_charge IS NOT NULL
         )
     ) ranked
     WHERE rn = {fallback_rn})
  ) AS "State of Charge (%)"
FROM locations l
WHERE l.name IN ($location)
ORDER BY l.name"""


SQL_NET_POWER = """WITH latest_bucket AS (
  SELECT location_id, MAX(date_trunc('minute', timestamp)) as latest_minute
  FROM power_readings
  WHERE source IN ($source)
  GROUP BY location_id
)
SELECT
  l.name || ' (' || pr.source || ')' AS name,
  (SUM(COALESCE(pr.power_produced, 0)) - SUM(COALESCE(pr.power_consumed, 0))) / 1000.0 AS "Net Power (kW)"
FROM power_readings pr
JOIN locations l ON pr.location_id = l.id
JOIN latest_bucket lb ON pr.location_id = lb.location_id
  AND date_trunc('minute', pr.timestamp) = lb.latest_minute
WHERE (pr.power_produced IS NOT NULL OR pr.power_consumed IS NOT NULL)
  AND l.name IN ($location)
  AND pr.source IN ($source)
GROUP BY l.name, pr.source
ORDER BY l.name, pr.source"""

SQL_ACTUAL_VS_THEORETICAL = """SELECT
  ir.timestamp AS "time",
  'Theoretical: ' || l.name || ' (' || ir.source || ')' AS metric,
  (ir.ghi_cloudy_sky / 1000.0) * COALESCE(l.capacity_kw, 0) AS "Power (kW)"
FROM irradiance_readings ir
JOIN locations l ON ir.location_id = l.id
WHERE ir.timestamp >= $__timeFrom() AND ir.timestamp <= $__timeTo()
  AND ir.ghi_cloudy_sky IS NOT NULL
  AND ir.source = 'tempest'
  AND l.capacity_kw IS NOT NULL
  AND l.capacity_kw > 0
  AND l.name IN ($location)
UNION ALL
SELECT
  date_trunc('minute', pr.timestamp) AS "time",
  'Actual: ' || l.name || ' (' || pr.source || ')' AS metric,
  SUM(pr.power_produced) / 1000.0 AS "Power (kW)"
FROM power_readings pr
JOIN locations l ON pr.location_id = l.id
WHERE pr.timestamp >= $__timeFrom() AND pr.timestamp <= $__timeTo()
  AND pr.power_produced IS NOT NULL
  AND l.name IN ($location)
  AND pr.source IN ($source)
GROUP BY date_trunc('minute', pr.timestamp), l.name, pr.source
ORDER BY "time" """

SQL_GRID_IMPORT_EXPORT = """SELECT
  date_trunc('minute', pr.timestamp) AS "time",
  l.name || ' (' || pr.source || ') - Grid Import' AS metric,
  SUM(pr.power_imported) / 1000.0 AS "Power Imported (kW)"
FROM power_readings pr
JOIN locations l ON pr.location_id = l.id
WHERE pr.timestamp >= $__timeFrom() AND pr.timestamp <= $__timeTo()
  AND pr.power_imported IS NOT NULL
  AND l.name IN ($location)
  AND pr.source IN ($source)
GROUP BY date_trunc('minute', pr.timestamp), l.name, pr.source
UNION ALL
SELECT
  date_trunc('minute', pr.timestamp) AS "time",
  l.name || ' (' || pr.source || ') - Grid Export' AS metric,
  SUM(pr.power_exported) / 1000.0 AS "Power Exported (kW)"
FROM power_readings pr
JOIN locations l ON pr.location_id = l.id
WHERE pr.timestamp >= $__timeFrom() AND pr.timestamp <= $__timeTo()
  AND pr.power_exported IS NOT NULL
  AND l.name IN ($location)
  AND pr.source IN ($source)
GROUP BY date_trunc('minute', pr.timestamp), l.name, pr.source
ORDER BY "time" """

SQL_BATTERY_SOC_TIMESERIES = """-- One point per physical battery per minute (dedupe duplicate inserts / fetch cycles)
-- Identity: battery_bank_id, else battery.index, else Tesla energy_site_id (one Grafana location, multiple sites),
-- else row id. ROW_NUMBER is scoped per (location, minute, energy_site_id) for multi-battery legacy rows.
WITH deduped AS (
  SELECT DISTINCT ON (
    br.location_id,
    date_trunc('minute', br.timestamp),
    COALESCE(
      br.battery_bank_id::text,
      br.raw_data->'battery'->>'index',
      NULLIF(BTRIM(br.raw_data->>'energy_site_id'), ''),
      br.id::text
    )
  )
    br.id,
    br.timestamp,
    br.location_id,
    br.source,
    br.state_of_charge,
    br.battery_bank_id,
    br.raw_data
  FROM battery_readings br
  WHERE br.timestamp >= $__timeFrom() AND br.timestamp <= $__timeTo()
    AND br.state_of_charge IS NOT NULL
  ORDER BY
    br.location_id,
    date_trunc('minute', br.timestamp),
    COALESCE(
      br.battery_bank_id::text,
      br.raw_data->'battery'->>'index',
      NULLIF(BTRIM(br.raw_data->>'energy_site_id'), ''),
      br.id::text
    ),
    br.timestamp DESC
)
SELECT
  d.timestamp AS "time",
  l.name || ' (' || d.source || ') ' || CASE
    WHEN NULLIF(BTRIM(d.raw_data->>'energy_site_id'), '') IS NOT NULL
    THEN '[' || RIGHT(BTRIM(d.raw_data->>'energy_site_id'), 6) || '] '
    ELSE ''
  END || '- System ' || (
    COALESCE(
      (bb.battery_index + 1),
      CASE
        WHEN d.raw_data#>>'{battery,index}' IS NOT NULL
        THEN (d.raw_data#>>'{battery,index}')::integer + 1
        ELSE NULL
      END,
      ROW_NUMBER() OVER (
        PARTITION BY
          d.location_id,
          date_trunc('minute', d.timestamp),
          COALESCE(NULLIF(BTRIM(d.raw_data->>'energy_site_id'), ''), '')
        ORDER BY d.id
      )
    )
  )::text AS metric,
  d.state_of_charge AS "State of Charge (%)"
FROM deduped d
JOIN locations l ON d.location_id = l.id
LEFT JOIN battery_banks bb ON bb.id = d.battery_bank_id
WHERE l.name IN ($location)
ORDER BY "time", l.name, metric"""

SQL_BATTERY_CHARGING = """SELECT
  date_trunc('minute', br.timestamp) AS "time",
  l.name || ' (' || br.source || ') - Charging' AS metric,
  SUM(COALESCE(br.power_charging, 0)) / 1000.0 AS "Power (kW)"
FROM battery_readings br
JOIN locations l ON br.location_id = l.id
WHERE br.timestamp >= $__timeFrom() AND br.timestamp <= $__timeTo()
  AND br.power_charging IS NOT NULL
  AND l.name IN ($location)
GROUP BY date_trunc('minute', br.timestamp), l.name, br.source
UNION ALL
SELECT
  date_trunc('minute', br.timestamp) AS "time",
  l.name || ' (' || br.source || ') - Discharging' AS metric,
  -SUM(COALESCE(br.power_discharging, 0)) / 1000.0 AS "Power (kW)"
FROM battery_readings br
JOIN locations l ON br.location_id = l.id
WHERE br.timestamp >= $__timeFrom() AND br.timestamp <= $__timeTo()
  AND br.power_discharging IS NOT NULL
  AND l.name IN ($location)
GROUP BY date_trunc('minute', br.timestamp), l.name, br.source
ORDER BY "time" """

SQL_PRODUCTION_VS_CONSUMPTION = """SELECT
  date_trunc('minute', pr.timestamp) AS "time",
  l.name || ' (' || pr.source || ') - Production' AS metric,
  SUM(pr.power_produced) / 1000.0 AS "Power (kW)"
FROM power_readings pr
JOIN locations l ON pr.location_id = l.id
WHERE pr.timestamp >= $__timeFrom() AND pr.timestamp <= $__timeTo()
  AND pr.power_produced IS NOT NULL
  AND l.name IN ($location)
  AND pr.source IN ($source)
GROUP BY date_trunc('minute', pr.timestamp), l.name, pr.source
UNION ALL
SELECT
  date_trunc('minute', pr.timestamp) AS "time",
  l.name || ' (' || pr.source || ') - Consumption' AS metric,
  SUM(pr.power_consumed) / 1000.0 AS "Power (kW)"
FROM power_readings pr
JOIN locations l ON pr.location_id = l.id
WHERE pr.timestamp >= $__timeFrom() AND pr.timestamp <= $__timeTo()
  AND pr.power_consumed IS NOT NULL
  AND l.name IN ($location)
  AND pr.source IN ($source)
GROUP BY date_trunc('minute', pr.timestamp), l.name, pr.source
ORDER BY "time" """

SQL_WATER_USAGE_DAILY = """SELECT
  wr.timestamp AS "time",
  l.name || ' (' || wr.source || ')' AS metric,
  wr.usage_gallons AS "Water Usage (gal)"
FROM water_readings wr
JOIN locations l ON wr.location_id = l.id
WHERE wr.timestamp >= $__timeFrom() AND wr.timestamp <= $__timeTo()
  AND wr.usage_gallons IS NOT NULL
  AND wr.usage_period = 'day'
  AND l.name IN ($location)
ORDER BY wr.timestamp"""

# Sprinkler runs query for annotations
SQL_SPRINKLER_RUNS_ANNOTATIONS = """SELECT
  sr.start_time AS "time",
  sr.end_time AS timeend,
  (
    'Sprinkler: ' || COALESCE(sr.zone_name, 'Zone ' || sr.zone_number::text) || E'\\n' ||
    l.name || ' - ' || COALESCE(sr.zone_name, 'Zone ' || sr.zone_number::text)
    || ' (' || COALESCE(sr.duration_seconds / 60, 0) || ' min)'
  ) AS "text",
  ('sprinkler,' || sr.schedule_type) AS "tags"
FROM sprinkler_runs sr
JOIN locations l ON sr.location_id = l.id
WHERE sr.start_time <= $__timeTo()
  AND sr.end_time >= $__timeFrom()
  AND l.name IN ($location)
ORDER BY sr.start_time"""

# Default IANA zone for all_day entries in annotations.json when a row has no per-entry
# "timezone" and the file omits all_day_timezone.
DEFAULT_ANNOTATIONS_ALL_DAY_TZ = "America/New_York"


def _sql_string_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _tesla_export_day_ctes(tz_lit: str) -> str:
    """Shared CTEs: bounds → minute_power → ordered → segments (Tesla export integration)."""
    return f"""bounds AS (
  SELECT
    (date_trunc('day', CURRENT_TIMESTAMP AT TIME ZONE {tz_lit}) AT TIME ZONE {tz_lit}) AS start_utc,
    ((date_trunc('day', CURRENT_TIMESTAMP AT TIME ZONE {tz_lit}) + interval '1 day') AT TIME ZONE {tz_lit}) AS end_utc
),
minute_power AS (
  SELECT
    pr.location_id,
    date_trunc('minute', pr.timestamp) AS ts_min,
    SUM(COALESCE(pr.power_exported, 0::float8))::float8 AS export_w
  FROM power_readings pr
  JOIN locations l ON pr.location_id = l.id
  CROSS JOIN bounds b
  WHERE pr.source = 'tesla'
    AND l.name IN ($location)
    AND pr.timestamp >= b.start_utc
    AND pr.timestamp < b.end_utc
  GROUP BY pr.location_id, date_trunc('minute', pr.timestamp)
),
ordered AS (
  SELECT
    location_id,
    ts_min,
    export_w,
    LEAD(ts_min) OVER (PARTITION BY location_id ORDER BY ts_min) AS next_ts,
    LEAD(export_w) OVER (PARTITION BY location_id ORDER BY ts_min) AS next_w
  FROM minute_power
),
segments AS (
  SELECT
    o.location_id,
    CASE
      WHEN o.next_ts IS NOT NULL THEN
        EXTRACT(EPOCH FROM (o.next_ts - o.ts_min))
        * (o.export_w + COALESCE(o.next_w, o.export_w)) / 2.0
      ELSE
        EXTRACT(EPOCH FROM (
          LEAST(CURRENT_TIMESTAMP, (SELECT end_utc FROM bounds)) - o.ts_min
        )) * o.export_w
    END / 3600000.0 AS kwh
  FROM ordered o
)"""


def sql_tesla_exported_today_kwh(all_day_timezone: str) -> str:
    """Standalone query: daily Tesla grid export (kWh); used by tests and ad-hoc panels."""
    tz_lit = _sql_string_literal(all_day_timezone)
    return f"""WITH {_tesla_export_day_ctes(tz_lit)}
SELECT
  COALESCE((SELECT SUM(kwh) FROM segments), 0) AS "Exported Today (kWh)",
  {TESLA_EXPORT_GAUGE_MAX_KWH!s}::float8 AS "Max"
"""


def sql_current_production_consumption_export(all_day_timezone: str) -> str:
    """Latest production/consumption (kW) plus Tesla export today (kWh); ignores Grafana range for export."""
    tz_lit = _sql_string_literal(all_day_timezone)
    tesla_ctes = _tesla_export_day_ctes(tz_lit)
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
{tesla_ctes},
export_today AS (
  SELECT COALESCE(SUM(kwh), 0) AS export_kwh FROM segments
)
SELECT
  COALESCE((SELECT production_kw FROM production_data), 0) AS "Production (kW)",
  COALESCE((SELECT consumption_kw FROM consumption_data), 0) AS "Consumption (kW)",
  COALESCE((SELECT export_kwh FROM export_today), 0) AS "Exported Today (kWh)",
  GREATEST(
    COALESCE((SELECT production_max FROM production_data), 20),
    COALESCE((SELECT consumption_max FROM consumption_data), 20)
  ) AS "Max"
"""


def _next_calendar_day_iso(cal: str) -> str:
    d = date.fromisoformat(cal)
    return (d + timedelta(days=1)).isoformat()


def _annotation_time_sql_exprs(ann: dict[str, Any], default_all_day_tz: str) -> tuple[str, str]:
    """Return (start_sql, end_sql) as timestamptz SQL expressions."""
    if ann.get("all_day"):
        tz_lit = _sql_string_literal(ann.get("timezone") or default_all_day_tz)
        if "date" in ann:
            start_d = ann["date"]
            end_next = _next_calendar_day_iso(start_d)
        else:
            start_d = ann["date_start"]
            end_last = ann["date_end"]
            end_next = _next_calendar_day_iso(end_last)
        ts_sql = (
            f"(TIMESTAMP {_sql_string_literal(f'{start_d} 00:00:00')} " f"AT TIME ZONE {tz_lit})"
        )
        end_sql = (
            f"(TIMESTAMP {_sql_string_literal(f'{end_next} 00:00:00')} " f"AT TIME ZONE {tz_lit})"
        )
        return ts_sql, end_sql

    time_s = ann["time"]
    time_end = ann.get("time_end")
    ts_sql = f"{_sql_string_literal(time_s)}::timestamptz"
    if time_end is None:
        return ts_sql, "NULL::timestamptz"
    return ts_sql, f"{_sql_string_literal(time_end)}::timestamptz"


def build_sql_codified_annotations(
    annotations: list[dict[str, Any]],
    *,
    all_day_timezone: str,
) -> str:
    """Build a Grafana Postgres annotation query from annotation entry dicts."""
    rows: list[tuple[str, str, str, str, str, str | None]] = []
    for ann in annotations:
        ts_sql, end_sql = _annotation_time_sql_exprs(ann, all_day_timezone)
        title = ann["title"]
        text = ann.get("text") or ""
        tags = ann.get("tags") or ""
        locs = ann.get("locations")
        if locs:
            for loc in locs:
                rows.append((ts_sql, end_sql, title, text, tags, loc))
        else:
            rows.append((ts_sql, end_sql, title, text, tags, None))

    if not rows:
        return 'SELECT NULL::timestamptz AS "time" WHERE false'

    filter_by_location = any(ann.get("locations") for ann in annotations)

    parts: list[str] = []
    for ts_sql, end_sql, title, text, tags, site in rows:
        if filter_by_location:
            site_sql = "NULL" if site is None else _sql_string_literal(site)
            parts.append(
                "("
                f"{ts_sql}, "
                f"{end_sql}, "
                f"{_sql_string_literal(title)}, "
                f"{_sql_string_literal(text)}, "
                f"{_sql_string_literal(tags)}, "
                f"{site_sql})"
            )
        else:
            parts.append(
                "("
                f"{ts_sql}, "
                f"{end_sql}, "
                f"{_sql_string_literal(title)}, "
                f"{_sql_string_literal(text)}, "
                f"{_sql_string_literal(tags)})"
            )

    values = ",\n  ".join(parts)
    if filter_by_location:
        from_clause = f"""FROM (VALUES
  {values}
) AS v(ts, ts_end, title, body, tag, site)"""
        location_clause = "\n  AND (v.site IS NULL OR v.site IN ($location))"
    else:
        from_clause = f"""FROM (VALUES
  {values}
) AS v(ts, ts_end, title, body, tag)"""
        location_clause = ""

    return f"""SELECT
  v.ts AS "time",
  v.ts_end AS timeend,
  (v.title || E'\\n' || COALESCE(v.body, '')) AS "text",
  v.tag AS "tags"
{from_clause}
WHERE v.ts <= $__timeTo()
  AND COALESCE(v.ts_end, v.ts) >= $__timeFrom(){location_clause}"""


# Sprinkler runs as time series (for overlay on water usage chart)
SQL_SPRINKLER_RUNS_TIMESERIES = """-- Generate time series for sprinkler runs
-- Creates entries at start and end of each run to show as vertical bands
SELECT
  sr.start_time AS "time",
  l.name || ' - ' || COALESCE(sr.zone_name, 'Zone ' || sr.zone_number::text) || ' (Start)' AS metric,
  1 AS "Sprinkler Running"
FROM sprinkler_runs sr
JOIN locations l ON sr.location_id = l.id
WHERE sr.start_time <= $__timeTo()
  AND sr.end_time >= $__timeFrom()
  AND l.name IN ($location)
UNION ALL
SELECT
  sr.end_time AS "time",
  l.name || ' - ' || COALESCE(sr.zone_name, 'Zone ' || sr.zone_number::text) || ' (End)' AS metric,
  0 AS "Sprinkler Running"
FROM sprinkler_runs sr
JOIN locations l ON sr.location_id = l.id
WHERE sr.start_time <= $__timeTo()
  AND sr.end_time >= $__timeFrom()
  AND l.name IN ($location)
ORDER BY "time" """

SQL_ENPHASE_ENERGY_TODAY = """SELECT
  pr.timestamp AS "time",
  l.name || ' (' || pr.source || ')' AS metric,
  CAST(pr.raw_data_energy_today AS DOUBLE PRECISION) / 1000.0 AS "Energy Today (kWh)"
FROM power_readings pr
JOIN locations l ON pr.location_id = l.id
WHERE pr.timestamp >= $__timeFrom() AND pr.timestamp <= $__timeTo()
  AND pr.source = 'enphase'
  AND pr.raw_data_energy_today IS NOT NULL
  AND pr.raw_data_energy_today != ''
  AND l.name IN ($location)
ORDER BY pr.timestamp"""


SQL_CURRENT_PROPANE_LEVEL = """SELECT
  COALESCE(pr.tank_level_percent, 0) AS "Tank Level (%)",
  COALESCE(pr.tank_level_gallons, 0) AS "Gallons",
  COALESCE(pr.capacity_gallons, 500) AS "Capacity"
FROM propane_readings pr
JOIN locations l ON pr.location_id = l.id
WHERE l.name IN ($location)
  AND pr.timestamp = (
    SELECT MAX(pr2.timestamp)
    FROM propane_readings pr2
    WHERE pr2.location_id = pr.location_id
  )
LIMIT 1"""

SQL_PROPANE_LEVEL_TIMESERIES = """SELECT * FROM (
  -- Historical data points
  SELECT
    pr.timestamp AS "time",
    l.name || ' - ' || COALESCE(pr.device_id, 'Tank') AS metric,
    pr.tank_level_percent AS "Tank Level (%)"
  FROM propane_readings pr
  JOIN locations l ON pr.location_id = l.id
  WHERE pr.timestamp >= $__timeFrom() AND pr.timestamp <= $__timeTo()
    AND pr.tank_level_percent IS NOT NULL
    AND l.name IN ($location)

  UNION ALL

  -- Extend latest reading to NOW so the line reaches present time
  SELECT
    NOW() AS "time",
    l.name || ' - ' || COALESCE(pr.device_id, 'Tank') AS metric,
    pr.tank_level_percent AS "Tank Level (%)"
  FROM propane_readings pr
  JOIN locations l ON pr.location_id = l.id
  WHERE pr.tank_level_percent IS NOT NULL
    AND l.name IN ($location)
    AND pr.timestamp = (
      SELECT MAX(pr2.timestamp)
      FROM propane_readings pr2
      WHERE pr2.location_id = pr.location_id
        AND (pr2.device_id = pr.device_id OR (pr2.device_id IS NULL AND pr.device_id IS NULL))
        AND pr2.tank_level_percent IS NOT NULL
    )
) AS combined
ORDER BY "time"
"""

SQL_PROPANE_GALLONS_TIMESERIES = """SELECT * FROM (
  -- Historical data points
  SELECT
    pr.timestamp AS "time",
    l.name || ' - ' || COALESCE(pr.device_id, 'Tank') AS metric,
    pr.tank_level_gallons AS "Gallons"
  FROM propane_readings pr
  JOIN locations l ON pr.location_id = l.id
  WHERE pr.timestamp >= $__timeFrom() AND pr.timestamp <= $__timeTo()
    AND pr.tank_level_gallons IS NOT NULL
    AND l.name IN ($location)

  UNION ALL

  -- Extend latest reading to NOW so the line reaches present time
  SELECT
    NOW() AS "time",
    l.name || ' - ' || COALESCE(pr.device_id, 'Tank') AS metric,
    pr.tank_level_gallons AS "Gallons"
  FROM propane_readings pr
  JOIN locations l ON pr.location_id = l.id
  WHERE pr.tank_level_gallons IS NOT NULL
    AND l.name IN ($location)
    AND pr.timestamp = (
      SELECT MAX(pr2.timestamp)
      FROM propane_readings pr2
      WHERE pr2.location_id = pr.location_id
        AND (pr2.device_id = pr.device_id OR (pr2.device_id IS NULL AND pr.device_id IS NULL))
        AND pr2.tank_level_gallons IS NOT NULL
    )
) AS combined
ORDER BY "time"
"""

SQL_CURRENT_WATER_USAGE = """SELECT
  COALESCE(SUM(wr.usage_gallons), 0) AS "Water Usage (gal)"
FROM water_readings wr
JOIN locations l ON wr.location_id = l.id
WHERE wr.usage_period = 'day'
  AND l.name IN ($location)
  AND wr.timestamp = (
    SELECT MAX(wr2.timestamp)
    FROM water_readings wr2
    WHERE wr2.location_id = wr.location_id
      AND wr2.usage_period = 'day'
  )"""

SQL_WATER_USAGE_STATS = """SELECT
  -- Water Usage Today: Get the latest reading for today's date (one per location, then sum)
  COALESCE((
    SELECT SUM(latest_today.usage_gallons)
    FROM (
      SELECT DISTINCT ON (wr.location_id) wr.location_id, wr.usage_gallons
      FROM water_readings wr
      JOIN locations l ON wr.location_id = l.id
      WHERE wr.usage_period = 'day'
        AND l.name IN ($location)
        AND DATE(wr.timestamp) = CURRENT_DATE
        AND wr.usage_gallons IS NOT NULL
      ORDER BY wr.location_id, wr.timestamp DESC
    ) latest_today
  ), 0) AS "Water Usage Today (gal)",
  -- Water Usage This Month: Sum latest daily reading per location per day for current month
  COALESCE((
    SELECT SUM(latest_daily.usage_gallons)
    FROM (
      SELECT DISTINCT ON (wr.location_id, DATE(wr.timestamp)) wr.location_id, DATE(wr.timestamp) as reading_date, wr.usage_gallons
      FROM water_readings wr
      JOIN locations l ON wr.location_id = l.id
      WHERE wr.usage_period = 'day'
        AND l.name IN ($location)
        AND DATE_TRUNC('month', wr.timestamp) = DATE_TRUNC('month', CURRENT_DATE)
        AND wr.usage_gallons IS NOT NULL
      ORDER BY wr.location_id, DATE(wr.timestamp), wr.timestamp DESC
    ) latest_daily
  ), 0) AS "Water Usage This Month (gal)",
  -- Sprinkler Days: Count distinct days with sprinkler runs this month
  COALESCE((
    SELECT COUNT(DISTINCT DATE(sr.start_time))
    FROM sprinkler_runs sr
    JOIN locations l ON sr.location_id = l.id
    WHERE l.name IN ($location)
      AND DATE_TRUNC('month', sr.start_time) = DATE_TRUNC('month', CURRENT_DATE)
  ), 0) AS "Sprinkler Days"
"""

SQL_ENERGY_IMPORT_EXPORT = """SELECT
  date_trunc('minute', pr.timestamp) AS "time",
  l.name || ' (' || pr.source || ') - Energy Imported' AS metric,
  SUM(pr.energy_imported_kwh) AS "Energy Imported (kWh)"
FROM power_readings pr
JOIN locations l ON pr.location_id = l.id
WHERE pr.timestamp >= $__timeFrom() AND pr.timestamp <= $__timeTo()
  AND pr.energy_imported_kwh IS NOT NULL
  AND l.name IN ($location)
  AND pr.source IN ($source)
GROUP BY date_trunc('minute', pr.timestamp), l.name, pr.source
UNION ALL
SELECT
  date_trunc('minute', pr.timestamp) AS "time",
  l.name || ' (' || pr.source || ') - Energy Exported' AS metric,
  SUM(pr.energy_exported_kwh) AS "Energy Exported (kWh)"
FROM power_readings pr
JOIN locations l ON pr.location_id = l.id
WHERE pr.timestamp >= $__timeFrom() AND pr.timestamp <= $__timeTo()
  AND pr.energy_exported_kwh IS NOT NULL
  AND l.name IN ($location)
  AND pr.source IN ($source)
GROUP BY date_trunc('minute', pr.timestamp), l.name, pr.source
ORDER BY "time" """

# =============================================================================
# Enphase Local Gateway Queries
# =============================================================================


def sql_enphase_local_current(gateway_index: int) -> str:
    """Generate SQL for current readings from a specific gateway index (0-based).

    Ranks gateways globally (across all locations) by location name + serial.
    """
    row_num = gateway_index + 1
    return f"""-- Gateway {row_num}: Get the latest reading for this gateway
WITH gateway_list AS (
  SELECT DISTINCT location_id, gateway_serial
  FROM enphase_local_readings elr
  JOIN locations l ON elr.location_id = l.id
  WHERE l.name IN ($location)
),
ranked_gateways AS (
  SELECT
    gl.location_id,
    gl.gateway_serial,
    DENSE_RANK() OVER (ORDER BY l.name, gl.gateway_serial) as gateway_rank
  FROM gateway_list gl
  JOIN locations l ON gl.location_id = l.id
),
target_gateway AS (
  SELECT location_id, gateway_serial
  FROM ranked_gateways
  WHERE gateway_rank = {row_num}
),
latest_reading AS (
  SELECT elr.location_id, elr.gateway_serial, MAX(elr.timestamp) as latest_ts
  FROM enphase_local_readings elr
  JOIN target_gateway tg ON elr.location_id = tg.location_id AND elr.gateway_serial = tg.gateway_serial
  GROUP BY elr.location_id, elr.gateway_serial
)
SELECT
  l.name || ' - ' || elr.gateway_serial AS "Gateway",
  COALESCE(elr.power_produced / 1000.0, 0) AS "Production (kW)",
  COALESCE(elr.power_consumed / 1000.0, 0) AS "Consumption (kW)",
  COALESCE(elr.power_net / 1000.0, 0) AS "Net (kW)"
FROM enphase_local_readings elr
JOIN latest_reading lr
  ON elr.location_id = lr.location_id
  AND elr.gateway_serial = lr.gateway_serial
  AND elr.timestamp = lr.latest_ts
JOIN locations l ON elr.location_id = l.id
ORDER BY l.name"""


def sql_enphase_local_energy_today(gateway_index: int) -> str:
    """Generate SQL for energy today from a specific gateway index (0-based).

    Calculates energy by integrating power readings over time for today.
    Energy (kWh) = sum of (Power (W) × time_interval (h)) / 1000
    Ranks gateways globally (across all locations) by location name + serial.
    """
    row_num = gateway_index + 1
    return f"""-- Gateway {row_num}: Calculate energy today by integrating power readings
WITH gateway_list AS (
  SELECT DISTINCT location_id, gateway_serial
  FROM enphase_local_readings elr
  JOIN locations l ON elr.location_id = l.id
  WHERE l.name IN ($location)
),
ranked_gateways AS (
  SELECT
    gl.location_id,
    gl.gateway_serial,
    DENSE_RANK() OVER (ORDER BY l.name, gl.gateway_serial) as gateway_rank
  FROM gateway_list gl
  JOIN locations l ON gl.location_id = l.id
),
target_gateway AS (
  SELECT location_id, gateway_serial
  FROM ranked_gateways
  WHERE gateway_rank = {row_num}
),
today_readings AS (
  SELECT
    elr.location_id,
    l.name as location_name,
    elr.gateway_serial,
    elr.timestamp,
    elr.power_produced,
    elr.power_consumed,
    LAG(elr.timestamp) OVER (PARTITION BY elr.location_id, elr.gateway_serial ORDER BY elr.timestamp) as prev_timestamp
  FROM enphase_local_readings elr
  JOIN target_gateway tg ON elr.location_id = tg.location_id AND elr.gateway_serial = tg.gateway_serial
  JOIN locations l ON elr.location_id = l.id
  WHERE elr.timestamp >= date_trunc('day', NOW())
),
energy_increments AS (
  SELECT
    location_id,
    location_name,
    gateway_serial,
    -- Energy = Power (W) × Time (hours) / 1000 = kWh
    COALESCE(power_produced, 0) * EXTRACT(EPOCH FROM (timestamp - prev_timestamp)) / 3600.0 / 1000.0 as produced_kwh,
    COALESCE(power_consumed, 0) * EXTRACT(EPOCH FROM (timestamp - prev_timestamp)) / 3600.0 / 1000.0 as consumed_kwh
  FROM today_readings
  WHERE prev_timestamp IS NOT NULL
)
SELECT
  location_name || ' - ' || gateway_serial AS "Gateway",
  ROUND(CAST(SUM(produced_kwh) AS NUMERIC), 2) AS "Produced (kWh)",
  ROUND(CAST(SUM(consumed_kwh) AS NUMERIC), 2) AS "Consumed (kWh)"
FROM energy_increments
GROUP BY location_id, location_name, gateway_serial
ORDER BY location_name"""


SQL_ENPHASE_LOCAL_PRODUCTION_VS_CONSUMPTION = """SELECT
  elr.timestamp AS "time",
  l.name || ' (' || elr.gateway_serial || ') - Production' AS metric,
  elr.power_produced / 1000.0 AS "Power (kW)"
FROM enphase_local_readings elr
JOIN locations l ON elr.location_id = l.id
WHERE elr.timestamp >= $__timeFrom() AND elr.timestamp <= $__timeTo()
  AND elr.power_produced IS NOT NULL
  AND l.name IN ($location)
UNION ALL
SELECT
  elr.timestamp AS "time",
  l.name || ' (' || elr.gateway_serial || ') - Consumption' AS metric,
  elr.power_consumed / 1000.0 AS "Power (kW)"
FROM enphase_local_readings elr
JOIN locations l ON elr.location_id = l.id
WHERE elr.timestamp >= $__timeFrom() AND elr.timestamp <= $__timeTo()
  AND elr.power_consumed IS NOT NULL
  AND l.name IN ($location)
ORDER BY "time" """

SQL_ENPHASE_LOCAL_NET_POWER = """SELECT
  elr.timestamp AS "time",
  l.name || ' (' || elr.gateway_serial || ')' AS metric,
  elr.power_net / 1000.0 AS "Net Power (kW)"
FROM enphase_local_readings elr
JOIN locations l ON elr.location_id = l.id
WHERE elr.timestamp >= $__timeFrom() AND elr.timestamp <= $__timeTo()
  AND elr.power_net IS NOT NULL
  AND l.name IN ($location)
ORDER BY "time" """

SQL_ENPHASE_LOCAL_GRID_VOLTAGE = """SELECT
  elr.timestamp AS "time",
  l.name || ' (' || elr.gateway_serial || ') - L1' AS metric,
  elr.grid_voltage_l1 AS "Voltage (V)"
FROM enphase_local_readings elr
JOIN locations l ON elr.location_id = l.id
WHERE elr.timestamp >= $__timeFrom() AND elr.timestamp <= $__timeTo()
  AND elr.grid_voltage_l1 IS NOT NULL
  AND l.name IN ($location)
UNION ALL
SELECT
  elr.timestamp AS "time",
  l.name || ' (' || elr.gateway_serial || ') - L2' AS metric,
  elr.grid_voltage_l2 AS "Voltage (V)"
FROM enphase_local_readings elr
JOIN locations l ON elr.location_id = l.id
WHERE elr.timestamp >= $__timeFrom() AND elr.timestamp <= $__timeTo()
  AND elr.grid_voltage_l2 IS NOT NULL
  AND l.name IN ($location)
ORDER BY "time" """

SQL_ENPHASE_LOCAL_GRID_FREQUENCY = """SELECT
  elr.timestamp AS "time",
  l.name || ' (' || elr.gateway_serial || ')' AS metric,
  elr.grid_frequency AS "Frequency (Hz)"
FROM enphase_local_readings elr
JOIN locations l ON elr.location_id = l.id
WHERE elr.timestamp >= $__timeFrom() AND elr.timestamp <= $__timeTo()
  AND elr.grid_frequency IS NOT NULL
  AND l.name IN ($location)
ORDER BY "time" """

# Pool/Spa queries (iAqualink)
SQL_CURRENT_POOL_TEMPS = """SELECT
  COALESCE(poolr.pool_temp, 0) AS "Pool Temp (°F)",
  COALESCE(poolr.spa_temp, 0) AS "Spa Temp (°F)",
  COALESCE(poolr.air_temp, 0) AS "Air Temp (°F)",
  poolr.pool_pump AS "Pool Pump",
  poolr.spa_pump AS "Spa Pump",
  poolr.pool_heater AS "Pool Heater",
  poolr.spa_heater AS "Spa Heater"
FROM pool_readings poolr
JOIN locations l ON poolr.location_id = l.id
WHERE l.name IN ($location)
  AND poolr.timestamp = (
    SELECT MAX(pr2.timestamp)
    FROM pool_readings pr2
    WHERE pr2.location_id = poolr.location_id
  )
LIMIT 1"""

SQL_POOL_TEMPS_TIMESERIES = """SELECT
  poolr.timestamp AS "time",
  l.name || ' - Pool' AS metric,
  poolr.pool_temp AS "Temperature (°F)"
FROM pool_readings poolr
JOIN locations l ON poolr.location_id = l.id
WHERE poolr.timestamp >= $__timeFrom() AND poolr.timestamp <= $__timeTo()
  AND poolr.pool_temp IS NOT NULL
  AND l.name IN ($location)
UNION ALL
SELECT
  poolr.timestamp AS "time",
  l.name || ' - Spa' AS metric,
  poolr.spa_temp AS "Temperature (°F)"
FROM pool_readings poolr
JOIN locations l ON poolr.location_id = l.id
WHERE poolr.timestamp >= $__timeFrom() AND poolr.timestamp <= $__timeTo()
  AND poolr.spa_temp IS NOT NULL
  AND l.name IN ($location)
UNION ALL
SELECT
  poolr.timestamp AS "time",
  l.name || ' - Air' AS metric,
  poolr.air_temp AS "Temperature (°F)"
FROM pool_readings poolr
JOIN locations l ON poolr.location_id = l.id
WHERE poolr.timestamp >= $__timeFrom() AND poolr.timestamp <= $__timeTo()
  AND poolr.air_temp IS NOT NULL
  AND l.name IN ($location)
ORDER BY "time" """

SQL_POOL_PUMP_STATUS = """SELECT
  poolr.timestamp AS "time",
  l.name || ' - Pool Pump' AS metric,
  CASE WHEN poolr.pool_pump THEN 1 ELSE 0 END AS "Status"
FROM pool_readings poolr
JOIN locations l ON poolr.location_id = l.id
WHERE poolr.timestamp >= $__timeFrom() AND poolr.timestamp <= $__timeTo()
  AND l.name IN ($location)
UNION ALL
SELECT
  poolr.timestamp AS "time",
  l.name || ' - Spa Pump' AS metric,
  CASE WHEN poolr.spa_pump THEN 1 ELSE 0 END AS "Status"
FROM pool_readings poolr
JOIN locations l ON poolr.location_id = l.id
WHERE poolr.timestamp >= $__timeFrom() AND poolr.timestamp <= $__timeTo()
  AND l.name IN ($location)
ORDER BY "time" """

SQL_POOL_HEATER_STATUS = """SELECT
  poolr.timestamp AS "time",
  l.name || ' - Pool Heater' AS metric,
  CASE WHEN poolr.pool_heater THEN 1 ELSE 0 END AS "Status"
FROM pool_readings poolr
JOIN locations l ON poolr.location_id = l.id
WHERE poolr.timestamp >= $__timeFrom() AND poolr.timestamp <= $__timeTo()
  AND l.name IN ($location)
UNION ALL
SELECT
  poolr.timestamp AS "time",
  l.name || ' - Spa Heater' AS metric,
  CASE WHEN poolr.spa_heater THEN 1 ELSE 0 END AS "Status"
FROM pool_readings poolr
JOIN locations l ON poolr.location_id = l.id
WHERE poolr.timestamp >= $__timeFrom() AND poolr.timestamp <= $__timeTo()
  AND l.name IN ($location)
ORDER BY "time" """

# System stats queries
SQL_CURRENT_SYSTEM_STATS = """SELECT
  sr.cpu_percent AS "CPU (%)",
  sr.memory_percent AS "Memory (%)",
  sr.disk_percent AS "Disk (%)"
FROM system_readings sr
WHERE sr.timestamp = (SELECT MAX(timestamp) FROM system_readings)
LIMIT 1"""

SQL_SYSTEM_STATS_TIMESERIES = """SELECT
  sr.timestamp AS "time",
  'CPU' AS metric,
  sr.cpu_percent AS "Usage (%)"
FROM system_readings sr
WHERE sr.timestamp >= $__timeFrom() AND sr.timestamp <= $__timeTo()
UNION ALL
SELECT
  sr.timestamp AS "time",
  'Memory' AS metric,
  sr.memory_percent AS "Usage (%)"
FROM system_readings sr
WHERE sr.timestamp >= $__timeFrom() AND sr.timestamp <= $__timeTo()
UNION ALL
SELECT
  sr.timestamp AS "time",
  'Disk' AS metric,
  sr.disk_percent AS "Usage (%)"
FROM system_readings sr
WHERE sr.timestamp >= $__timeFrom() AND sr.timestamp <= $__timeTo()
ORDER BY "time" """

SQL_CPU_TIMESERIES = """SELECT
  sr.timestamp AS "time",
  sr.cpu_percent AS "CPU (%)"
FROM system_readings sr
WHERE sr.timestamp >= $__timeFrom() AND sr.timestamp <= $__timeTo()
ORDER BY "time" """

SQL_MEMORY_TIMESERIES = """SELECT
  sr.timestamp AS "time",
  sr.memory_percent AS "Memory (%)"
FROM system_readings sr
WHERE sr.timestamp >= $__timeFrom() AND sr.timestamp <= $__timeTo()
ORDER BY "time" """

SQL_DISK_TIMESERIES = """SELECT
  sr.timestamp AS "time",
  sr.disk_percent AS "Disk (%)"
FROM system_readings sr
WHERE sr.timestamp >= $__timeFrom() AND sr.timestamp <= $__timeTo()
ORDER BY "time" """

SQL_API_CALLS_BY_INTEGRATION = """SELECT
  frs.started_at AS "time",
  key AS metric,
  (value->>'calls')::INTEGER AS "API Calls"
FROM fetch_run_summaries frs,
  jsonb_each(frs.integrations_summary)
WHERE frs.started_at >= $__timeFrom() AND frs.started_at <= $__timeTo()
  AND frs.integrations_summary IS NOT NULL
ORDER BY "time" """

# Water gallons cumulative over dashboard time range (daily readings only).
# Use one reading per location per day (latest timestamp) so we don't double-count
# when multiple fetches per day each report "usage today".
SQL_WATER_GALLONS_CUMULATIVE = """WITH latest_per_day AS (
  SELECT DISTINCT ON (wr.location_id, DATE(wr.timestamp))
    wr.location_id,
    DATE(wr.timestamp) AS day,
    wr.usage_gallons AS g
  FROM water_readings wr
  JOIN locations l ON wr.location_id = l.id
  WHERE wr.timestamp >= $__timeFrom() AND wr.timestamp <= $__timeTo()
    AND wr.usage_gallons IS NOT NULL
    AND wr.usage_period = 'day'
    AND l.name IN ($location)
  ORDER BY wr.location_id, DATE(wr.timestamp), wr.timestamp DESC
),
daily_totals AS (
  SELECT day, SUM(g) AS total_gallons
  FROM latest_per_day
  GROUP BY day
)
SELECT
  day::timestamptz AS "time",
  SUM(total_gallons) OVER (ORDER BY day) AS "Cumulative (gal)"
FROM daily_totals
ORDER BY "time" """

SQL_SPAN_GRID_POWER = """SELECT
  spr.timestamp AS "time",
  COALESCE(spt.panel_name, spr.panel_serial) AS metric,
  spr.instant_grid_power_w / 1000.0 AS "Grid Power (kW)"
FROM span_panel_readings spr
JOIN locations l ON spr.location_id = l.id
LEFT JOIN span_panel_tokens spt ON spr.panel_serial = spt.panel_serial
WHERE spr.timestamp >= $__timeFrom() AND spr.timestamp <= $__timeTo()
  AND l.name IN ($location)
ORDER BY "time" """

SQL_SPAN_FEEDTHROUGH_POWER = """SELECT
  spr.timestamp AS "time",
  COALESCE(spt.panel_name, spr.panel_serial) AS metric,
  spr.feedthrough_power_w / 1000.0 AS "Feedthrough Power (kW)"
FROM span_panel_readings spr
JOIN locations l ON spr.location_id = l.id
LEFT JOIN span_panel_tokens spt ON spr.panel_serial = spt.panel_serial
WHERE spr.timestamp >= $__timeFrom() AND spr.timestamp <= $__timeTo()
  AND l.name IN ($location)
ORDER BY "time" """

SQL_SPAN_CURRENT_GRID_POWER = """WITH latest AS (
  SELECT panel_serial, MAX(timestamp) as latest_time
  FROM span_panel_readings spr
  JOIN locations l ON spr.location_id = l.id
  WHERE l.name IN ($location)
  GROUP BY panel_serial
)
SELECT
  COALESCE(spt.panel_name, spr.panel_serial) AS metric,
  spr.instant_grid_power_w / 1000.0 AS "Grid Power (kW)"
FROM span_panel_readings spr
JOIN latest ON spr.panel_serial = latest.panel_serial AND spr.timestamp = latest.latest_time
LEFT JOIN span_panel_tokens spt ON spr.panel_serial = spt.panel_serial"""

SQL_SPAN_PANEL_STATUS = """WITH latest AS (
  SELECT panel_serial, MAX(timestamp) as latest_time
  FROM span_panel_readings spr
  JOIN locations l ON spr.location_id = l.id
  WHERE l.name IN ($location)
  GROUP BY panel_serial
)
SELECT
  COALESCE(spt.panel_name, spr.panel_serial) AS "Panel",
  spr.main_relay_state AS "Main Relay",
  spr.dsm_grid_state AS "Grid State",
  spr.door_state AS "Door",
  CASE
    WHEN spr.eth0_link THEN 'Ethernet'
    WHEN spr.wlan_link THEN 'WiFi'
    WHEN spr.wwan_link THEN 'Cellular'
    ELSE 'None'
  END AS "Network",
  spr.firmware_version AS "Firmware",
  ROUND(spr.uptime_seconds / 86400.0, 1) || ' days' AS "Uptime"
FROM span_panel_readings spr
JOIN latest ON spr.panel_serial = latest.panel_serial AND spr.timestamp = latest.latest_time
LEFT JOIN span_panel_tokens spt ON spr.panel_serial = spt.panel_serial"""


def sql_span_battery_soc(panel_index: int) -> str:
    """Generate SQL for Span battery state of charge by panel index.

    Returns time_series format with metric column for dynamic series naming.
    """
    panel_num = panel_index + 1
    return f"""-- Span Battery {panel_num}: Get the latest reading for panel at index {panel_index}
-- Uses ROW_NUMBER to identify panels by order of panel_serial
-- Returns time_series format so metric column becomes the series name
WITH latest AS (
  SELECT panel_serial, MAX(timestamp) as latest_time
  FROM span_panel_readings spr
  JOIN locations l ON spr.location_id = l.id
  WHERE l.name IN ($location)
  GROUP BY panel_serial
),
panel_rankings AS (
  SELECT
    spr.panel_serial,
    spr.timestamp as "time",
    COALESCE(spt.panel_name, spr.panel_serial) AS metric,
    spr.battery_soe_percent AS "Battery SOC",
    ROW_NUMBER() OVER (ORDER BY spr.panel_serial) as panel_rn
  FROM span_panel_readings spr
  JOIN latest ON spr.panel_serial = latest.panel_serial AND spr.timestamp = latest.latest_time
  LEFT JOIN span_panel_tokens spt ON spr.panel_serial = spt.panel_serial
)
SELECT "time", metric, "Battery SOC"
FROM panel_rankings
WHERE panel_rn = {panel_num}"""


SQL_SPAN_TOP_CIRCUITS_BY_POWER = """WITH latest AS (
  SELECT panel_serial, MAX(timestamp) as latest_time
  FROM span_circuit_readings scr
  JOIN locations l ON scr.location_id = l.id
  WHERE l.name IN ($location)
  GROUP BY panel_serial
),
ranked AS (
  SELECT
    scr.circuit_name AS metric,
    ABS(scr.instant_power_w) AS "Power (W)",
    ROW_NUMBER() OVER (ORDER BY ABS(scr.instant_power_w) DESC) as rn
  FROM span_circuit_readings scr
  JOIN latest ON scr.panel_serial = latest.panel_serial AND scr.timestamp = latest.latest_time
)
SELECT metric, "Power (W)"
FROM ranked
WHERE rn <= 10
ORDER BY "Power (W)" DESC"""

SQL_SPAN_CIRCUIT_POWER_TIMESERIES = """SELECT
  scr.timestamp AS "time",
  scr.circuit_name AS metric,
  scr.instant_power_w AS "Power (W)"
FROM span_circuit_readings scr
JOIN locations l ON scr.location_id = l.id
WHERE scr.timestamp >= $__timeFrom() AND scr.timestamp <= $__timeTo()
  AND l.name IN ($location)
ORDER BY "time" """

SQL_SPAN_TOTAL_CIRCUIT_POWER = """SELECT
  scr.timestamp AS "time",
  COALESCE(spt.panel_name, scr.panel_serial) AS metric,
  SUM(scr.instant_power_w) / 1000.0 AS "Total Circuit Power (kW)"
FROM span_circuit_readings scr
JOIN locations l ON scr.location_id = l.id
LEFT JOIN span_panel_tokens spt ON scr.panel_serial = spt.panel_serial
WHERE scr.timestamp >= $__timeFrom() AND scr.timestamp <= $__timeTo()
  AND l.name IN ($location)
GROUP BY scr.timestamp, scr.panel_serial, spt.panel_name
ORDER BY "time" """

SQL_SPAN_CIRCUIT_ENERGY_DAILY = """WITH daily_energy AS (
  SELECT
    DATE(scr.timestamp) as day,
    scr.circuit_name,
    MAX(scr.import_energy_wh) - MIN(scr.import_energy_wh) AS energy_wh
  FROM span_circuit_readings scr
  JOIN locations l ON scr.location_id = l.id
  WHERE scr.timestamp >= $__timeFrom() AND scr.timestamp <= $__timeTo()
    AND l.name IN ($location)
  GROUP BY DATE(scr.timestamp), scr.circuit_name
)
SELECT
  day AS "time",
  circuit_name AS metric,
  energy_wh / 1000.0 AS "Energy (kWh)"
FROM daily_energy
ORDER BY day, energy_wh DESC"""

SQL_SPAN_TOP_ENERGY_CONSUMERS = """WITH energy_totals AS (
  SELECT
    scr.circuit_id,
    scr.circuit_name,
    MAX(scr.import_energy_wh) - MIN(scr.import_energy_wh) AS energy_wh
  FROM span_circuit_readings scr
  JOIN locations l ON scr.location_id = l.id
  WHERE scr.timestamp >= $__timeFrom() AND scr.timestamp <= $__timeTo()
    AND l.name IN ($location)
  GROUP BY scr.circuit_id, scr.circuit_name
)
SELECT
  circuit_name AS metric,
  energy_wh / 1000.0 AS "Energy (kWh)"
FROM energy_totals
WHERE energy_wh > 0
ORDER BY energy_wh DESC
LIMIT 10"""

SQL_SPAN_CIRCUIT_TABLE = """WITH latest AS (
  SELECT panel_serial, MAX(timestamp) as latest_time
  FROM span_circuit_readings scr
  JOIN locations l ON scr.location_id = l.id
  WHERE l.name IN ($location)
  GROUP BY panel_serial
)
SELECT
  scr.circuit_name AS "Circuit",
  ROUND(ABS(scr.instant_power_w)::numeric, 1) AS "Power (W)",
  ROUND((scr.import_energy_wh / 1000.0)::numeric, 2) AS "Import (kWh)",
  ROUND((scr.export_energy_wh / 1000.0)::numeric, 2) AS "Export (kWh)",
  scr.relay_state AS "Relay",
  scr.priority AS "Priority"
FROM span_circuit_readings scr
JOIN latest ON scr.panel_serial = latest.panel_serial AND scr.timestamp = latest.latest_time
ORDER BY ABS(scr.instant_power_w) DESC"""


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


def sql_target(sql: str, ref_id: str = "A", format_mode: str = "time_series") -> Target:
    """Create a SQL target for Grafana panels."""
    return SqlTarget(
        rawSql=sql,
        refId=ref_id,
        format=format_mode,
        datasource=DATASOURCE,
    )


def postgres_annotation_layer(name: str, sql: str, icon_color: str) -> dict[str, Any]:
    """Dashboard annotation query for the Postgres datasource.

    Panel queries use rawQuery=true + rawSql; annotation queries historically used the SQL
    string in ``rawQuery`` directly. Newer Grafana resolves ``target.rawSql``. We set all
    compatible shapes so the query actually runs.
    """
    return {
        "datasource": DATASOURCE,
        "enable": True,
        "hide": False,
        "iconColor": icon_color,
        "name": name,
        "rawQuery": sql,
        "query": sql,
        "target": {
            "editorMode": "code",
            "format": "table",
            "rawQuery": True,
            "rawSql": sql,
            "refId": "AnnotationQuery",
        },
    }


def power_gauge(
    title: str,
    sql: str,
    pos: GridPos,
    field_name: str,
    thresholds: list[dict[str, Any]],
    panel_id: int,
    *,
    unit: str = "kwatt",
    description: str = "",
) -> dict[str, Any]:
    """Create a gauge panel for power readings with dynamic max from data."""
    panel: dict[str, Any] = {
        "id": panel_id,
        "gridPos": {"h": pos.h, "w": pos.w, "x": pos.x, "y": pos.y},
        "type": "gauge",
        "title": title,
        "targets": [
            {
                "datasource": DATASOURCE,
                "editorMode": "code",
                "format": "table",
                "rawQuery": True,
                "rawSql": sql,
                "refId": "A",
            }
        ],
        "fieldConfig": {
            "defaults": {
                "color": {"mode": "thresholds"},
                "mappings": [],
                "min": 0,
                "thresholds": {
                    "mode": "percentage",
                    "steps": thresholds,
                },
                "unit": unit,
            },
            "overrides": [
                {
                    "matcher": {"id": "byName", "options": field_name},
                    "properties": [
                        {"id": "displayName", "value": field_name},
                    ],
                },
                {
                    "matcher": {"id": "byName", "options": "Max"},
                    "properties": [
                        {
                            "id": "custom.hideFrom",
                            "value": {"tooltip": True, "viz": True, "legend": False},
                        },
                    ],
                },
            ],
        },
        "options": {
            "minVizHeight": 75,
            "minVizWidth": 75,
            "orientation": "auto",
            "reduceOptions": {
                "values": False,
                "calcs": ["lastNotNull"],
                "fields": "",
            },
            "showThresholdLabels": False,
            "showThresholdMarkers": True,
            "sizing": "auto",
        },
        "transformations": [
            {
                "id": "configFromData",
                "options": {
                    "configRefId": "A",
                    "mappings": [{"fieldName": "Max", "handlerKey": "max"}],
                },
            },
        ],
    }
    if description:
        panel["description"] = description
    return panel


def combined_power_gauge(
    title: str,
    sql: str,
    pos: GridPos,
    panel_id: int,
    production_thresholds: list[dict[str, Any]],
    consumption_thresholds: list[dict[str, Any]],
    export_thresholds: list[dict[str, Any]],
    description: str = "",
) -> dict[str, Any]:
    """Create a combined gauge panel: Production, Consumption, and Tesla export today (kWh)."""
    panel: dict[str, Any] = {
        "id": panel_id,
        "gridPos": {"h": pos.h, "w": pos.w, "x": pos.x, "y": pos.y},
        "type": "gauge",
        "title": title,
        "targets": [
            {
                "datasource": DATASOURCE,
                "editorMode": "code",
                "format": "table",
                "rawQuery": True,
                "rawSql": sql,
                "refId": "A",
            }
        ],
        "fieldConfig": {
            "defaults": {
                "color": {"mode": "thresholds"},
                "mappings": [],
                "min": 0,
                "thresholds": {
                    "mode": "percentage",
                    "steps": production_thresholds,
                },
                "unit": "kwatt",
            },
            "overrides": [
                {
                    "matcher": {"id": "byName", "options": "Production (kW)"},
                    "properties": [
                        {"id": "displayName", "value": "Production (kW)"},
                        {
                            "id": "thresholds",
                            "value": {
                                "mode": "percentage",
                                "steps": production_thresholds,
                            },
                        },
                    ],
                },
                {
                    "matcher": {"id": "byName", "options": "Consumption (kW)"},
                    "properties": [
                        {"id": "displayName", "value": "Consumption (kW)"},
                        {
                            "id": "thresholds",
                            "value": {
                                "mode": "percentage",
                                "steps": consumption_thresholds,
                            },
                        },
                    ],
                },
                {
                    "matcher": {"id": "byName", "options": "Exported Today (kWh)"},
                    "properties": [
                        {"id": "displayName", "value": "Exported Today (kWh)"},
                        {"id": "unit", "value": "kwatth"},
                        {"id": "max", "value": TESLA_EXPORT_GAUGE_MAX_KWH},
                        {
                            "id": "thresholds",
                            "value": {
                                "mode": "percentage",
                                "steps": export_thresholds,
                            },
                        },
                    ],
                },
                {
                    "matcher": {"id": "byName", "options": "Max"},
                    "properties": [
                        {
                            "id": "custom.hideFrom",
                            "value": {"tooltip": True, "viz": True, "legend": False},
                        },
                    ],
                },
            ],
        },
        "options": {
            "minVizHeight": 75,
            "minVizWidth": 75,
            "orientation": "auto",
            "reduceOptions": {
                "values": False,
                "calcs": ["lastNotNull"],
                "fields": "",
            },
            "showThresholdLabels": False,
            "showThresholdMarkers": True,
            "sizing": "auto",
        },
        "transformations": [
            {
                "id": "configFromData",
                "options": {
                    "configRefId": "A",
                    "mappings": [{"fieldName": "Max", "handlerKey": "max"}],
                },
            },
            {
                "id": "organize",
                "options": {
                    "excludeByName": {
                        "Max": True,
                    },
                    "indexByName": {
                        "Production (kW)": 0,
                        "Consumption (kW)": 1,
                        "Exported Today (kWh)": 2,
                    },
                    "renameByName": {},
                },
            },
        ],
    }
    if description:
        panel["description"] = description
    return panel


def power_timeseries(
    title: str,
    sql: str,
    pos: GridPos,
    panel_id: int,
    description: str = "",
    legend_calcs: list[str] | None = None,
    overrides: list[dict] | None = None,
    custom_options: dict | None = None,
    decimals: int | None = None,
    base_color: str = GREEN,
) -> dict[str, Any]:
    """Create a timeseries panel for power data."""
    if legend_calcs is None:
        legend_calcs = LEGEND_CALCS_STANDARD

    field_config: dict[str, Any] = {
        "defaults": {
            "color": {"mode": "palette-classic"},
            "custom": {
                "axisCenteredZero": False,
                "axisColorMode": "text",
                "axisLabel": "",
                "axisPlacement": "auto",
                "drawStyle": "line",
                "fillOpacity": 10,
                "lineInterpolation": "linear",
                "lineWidth": 1,
                "pointSize": 5,
                "showPoints": "never",
                "spanNulls": True,
                **(custom_options or {}),
            },
            "mappings": [],
            "thresholds": {
                "mode": "absolute",
                "steps": [{"color": base_color, "value": None}],
            },
            "unit": "kwatt",
        },
        "overrides": overrides or [],
    }

    if decimals is not None:
        field_config["defaults"]["decimals"] = decimals

    panel: dict[str, Any] = {
        "id": panel_id,
        "gridPos": {"h": pos.h, "w": pos.w, "x": pos.x, "y": pos.y},
        "type": "timeseries",
        "title": title,
        "targets": [
            {
                "datasource": DATASOURCE,
                "editorMode": "code",
                "format": "time_series",
                "rawQuery": True,
                "rawSql": sql,
                "refId": "A",
            }
        ],
        "fieldConfig": field_config,
        "options": {
            "tooltip": {"mode": "multi", "sort": "none"},
            "legend": {
                "displayMode": "table",
                "placement": "bottom",
                "showLegend": True,
                "calcs": legend_calcs,
            },
        },
    }

    if description:
        panel["description"] = description

    return panel


def stat_panel(
    title: str,
    sql: str,
    pos: GridPos,
    panel_id: int,
    thresholds: list[dict[str, Any]],
    unit: str = "percent",
    min_val: int | None = 0,
    max_val: int | None = 100,
    color_mode: str = "background",
    graph_mode: str = "area",
    description: str = "",
    overrides: list[dict] | None = None,
    text_mode: str = "auto",
    query_format: str = "table",
) -> dict[str, Any]:
    """Create a stat panel.

    Args:
        text_mode: Display mode - "auto", "value", "value_and_name", "name", or "none"
        query_format: Query format - "table" or "time_series". Use "time_series" with
            a metric column to show the metric value as the series name.
    """
    field_config: dict[str, Any] = {
        "defaults": {
            "color": {"mode": "thresholds"},
            "mappings": [],
            "thresholds": {
                "mode": "absolute",
                "steps": thresholds,
            },
            "unit": unit,
        },
    }

    if min_val is not None:
        field_config["defaults"]["min"] = min_val
    if max_val is not None:
        field_config["defaults"]["max"] = max_val
    if overrides:
        field_config["overrides"] = overrides

    panel: dict[str, Any] = {
        "id": panel_id,
        "gridPos": {"h": pos.h, "w": pos.w, "x": pos.x, "y": pos.y},
        "type": "stat",
        "title": title,
        "targets": [
            {
                "datasource": DATASOURCE,
                "editorMode": "code",
                "format": query_format,
                "rawQuery": True,
                "rawSql": sql,
                "refId": "A",
            }
        ],
        "fieldConfig": field_config,
        "options": {
            "colorMode": color_mode,
            "graphMode": graph_mode,
            "justifyMode": "auto",
            "orientation": "auto",
            "reduceOptions": {"values": False, "calcs": ["lastNotNull"], "fields": ""},
            "textMode": text_mode,
        },
    }

    if description:
        panel["description"] = description

    return panel


def bar_gauge_panel(
    title: str,
    sql: str,
    pos: GridPos,
    panel_id: int,
    unit: str = "kwatt",
    description: str = "",
    overrides: list[dict] | None = None,
    query_format: str = "time_series",
) -> dict[str, Any]:
    """Create a bar gauge panel."""
    # For table format, use each row as a separate bar; for time_series, reduce to calcs
    use_all_values = query_format == "table"
    return {
        "id": panel_id,
        "gridPos": {"h": pos.h, "w": pos.w, "x": pos.x, "y": pos.y},
        "type": "bargauge",
        "title": title,
        "description": description,
        "targets": [
            {
                "datasource": DATASOURCE,
                "editorMode": "code",
                "format": query_format,
                "rawQuery": True,
                "rawSql": sql,
                "refId": "A",
            }
        ],
        "fieldConfig": {
            "defaults": {
                "color": {"mode": "continuous-GrYlRd"},
                "mappings": [],
                "thresholds": {
                    "mode": "absolute",
                    "steps": [
                        {"color": GREEN, "value": 0},
                        {"color": RED, "value": 80},
                    ],
                },
                "unit": unit,
            },
            "overrides": overrides or [],
        },
        "options": {
            "displayMode": "lcd",
            "legend": {
                "calcs": [],
                "displayMode": "list",
                "placement": "bottom",
                "showLegend": False,
            },
            "maxVizHeight": 300,
            "minVizHeight": 16,
            "minVizWidth": 8,
            "namePlacement": "auto",
            "orientation": "horizontal",
            "reduceOptions": {
                "calcs": ["lastNotNull"],
                "fields": "",
                "values": use_all_values,
            },
            "showUnfilled": True,
            "sizing": "auto",
            "valueMode": "color",
        },
        "pluginVersion": "12.3.1",
    }


def color_override(pattern: str, color: str) -> dict:
    """Create a color override for a regex pattern."""
    return {
        "matcher": {"id": "byRegexp", "options": pattern},
        "properties": [{"id": "color", "value": {"fixedColor": color, "mode": "fixed"}}],
    }


# =============================================================================
# DASHBOARD PANELS
# =============================================================================


def create_panels(all_day_timezone: str = DEFAULT_ANNOTATIONS_ALL_DAY_TZ) -> list[dict[str, Any]]:
    """Create all dashboard panels."""
    panels = []

    # Row 1: Production, Consumption, and Tesla export today (single panel, three gauges)
    panels.append(
        combined_power_gauge(
            title="Current Production + Consumption",
            sql=sql_current_production_consumption_export(all_day_timezone),
            pos=GridPos(h=5, w=12, x=0, y=0),
            panel_id=9,
            production_thresholds=[
                threshold(RED),
                threshold(ORANGE, 10),
                threshold(YELLOW, 25),
                threshold(GREEN, 50),
            ],
            consumption_thresholds=[
                threshold(GREEN),
                threshold(YELLOW, 50),
                threshold(ORANGE, 75),
                threshold(RED, 90),
            ],
            export_thresholds=[
                threshold(RED),
                threshold(ORANGE, 15),
                threshold(YELLOW, 35),
                threshold(GREEN, 60),
            ],
            description=(
                "Exported Today (kWh): Tesla `power_exported` integrated from local midnight to "
                "now (IANA zone from annotations.json `all_day_timezone`, default "
                "America/New_York). Ignores the dashboard time range; always Tesla, not the Data "
                "Source variable."
            ),
        )
    )

    # Row 2: Battery State of Charge (3x3 panels) + Net Power (3x3)
    battery_thresholds = [
        threshold(RED),
        threshold(YELLOW, 20),
        threshold(GREEN, 80),
    ]

    panels.append(
        stat_panel(
            title="Battery SoC (1)",
            sql=sql_battery_soc(0),
            pos=GridPos(h=3, w=3, x=0, y=5),
            panel_id=11,
            thresholds=battery_thresholds,
        )
    )

    panels.append(
        stat_panel(
            title="Battery SoC (2)",
            sql=sql_battery_soc(1),
            pos=GridPos(h=3, w=3, x=3, y=5),
            panel_id=13,
            thresholds=battery_thresholds,
        )
    )

    panels.append(
        stat_panel(
            title="Net Power (Current)",
            sql=SQL_NET_POWER,
            pos=GridPos(h=3, w=3, x=6, y=5),
            panel_id=12,
            thresholds=[
                threshold(RED),
                threshold(YELLOW, -1),
                threshold(GREEN, 0),
            ],
            unit="kwatt",
            min_val=None,
            max_val=None,
        )
    )

    panels.append(
        stat_panel(
            title="Water Usage (Current)",
            sql=SQL_CURRENT_WATER_USAGE,
            pos=GridPos(h=3, w=3, x=9, y=5),
            panel_id=20,
            thresholds=[
                threshold(GREEN),
                threshold(YELLOW, 100),
                threshold(ORANGE, 200),
                threshold(RED, 300),
            ],
            unit="gal",
            min_val=None,
            max_val=None,
        )
    )

    # Row 3: Actual vs Theoretical + Production vs Consumption
    actual_vs_theoretical_panel = bar_gauge_panel(
        title="Production (Actual) vs. Theoretical",
        sql=SQL_ACTUAL_VS_THEORETICAL,
        pos=GridPos(h=8, w=12, x=0, y=8),
        panel_id=3,
        description="Compare actual power production (kW) with theoretical production based on Tempest measured irradiance (W/m²).",
        overrides=[
            {
                "matcher": {"id": "byRegexp", "options": ".*Theoretical.*"},
                "properties": [{"id": "unit", "value": "kwatt"}],
            },
            {
                "matcher": {"id": "byRegexp", "options": ".*Actual.*"},
                "properties": [{"id": "unit", "value": "kwatt"}],
            },
        ],
    )
    # Add transformation to ensure Theoretical Production appears first
    # Sort by metric name, but prioritize "Theoretical" by using a computed sort field
    actual_vs_theoretical_panel["transformations"] = [
        {
            "id": "sortBy",
            "options": {
                "fields": {},
                "sort": [
                    {
                        "desc": False,
                        "field": "metric",
                    }
                ],
            },
        },
    ]
    panels.append(actual_vs_theoretical_panel)

    panels.append(
        power_timeseries(
            title="Production vs Consumption",
            sql=SQL_PRODUCTION_VS_CONSUMPTION,
            pos=GridPos(h=8, w=12, x=12, y=0),
            panel_id=7,
            description="Shows production (green) and consumption (red) as a 100% stacked area chart, showing the relative proportion of each.",
            legend_calcs=["lastNotNull", "max", "min", "mean"],
            custom_options={
                "fillOpacity": 80,
                "stacking": {"group": "A", "mode": "percent"},
                "lineInterpolation": "stepBefore",
            },
            overrides=[
                color_override("Production", GREEN),
                color_override("Consumption", RED),
            ],
        )
    )

    # Row 4: Power Production/Consumption Timeseries
    panels.append(
        power_timeseries(
            title="Power Production by Site",
            sql=SQL_POWER_PRODUCTION,
            pos=GridPos(h=8, w=12, x=0, y=16),
            panel_id=1,
        )
    )

    # Power Consumption by Site with Enphase Production overlay
    consumption_panel = {
        "id": 2,
        "gridPos": {"h": 8, "w": 12, "x": 12, "y": 8},
        "type": "timeseries",
        "title": "Power Consumption by Site",
        "targets": [
            {
                "datasource": DATASOURCE,
                "editorMode": "code",
                "format": "time_series",
                "rawQuery": True,
                "rawSql": SQL_POWER_CONSUMPTION,
                "refId": "A",
            },
            {
                "datasource": DATASOURCE,
                "editorMode": "code",
                "format": "time_series",
                "rawQuery": True,
                "rawSql": SQL_ENPHASE_POWER_PRODUCTION,
                "refId": "B",
            },
        ],
        "fieldConfig": {
            "defaults": {
                "color": {"mode": "palette-classic"},
                "custom": {
                    "axisCenteredZero": False,
                    "axisColorMode": "text",
                    "axisLabel": "",
                    "axisPlacement": "auto",
                    "drawStyle": "line",
                    "fillOpacity": 10,
                    "lineInterpolation": "linear",
                    "lineWidth": 1,
                    "pointSize": 5,
                    "showPoints": "never",
                    "spanNulls": False,
                },
                "mappings": [],
                "thresholds": {
                    "mode": "absolute",
                    "steps": [{"color": RED, "value": None}],
                },
                "unit": "kwatt",
                "decimals": 3,
            },
            "overrides": [
                color_override(".*Production.*", GREEN),
                color_override(".*tesla.*", RED),
                color_override(".*enphase\\)$", ORANGE),
            ],
        },
        "options": {
            "tooltip": {"mode": "multi", "sort": "none"},
            "legend": {
                "displayMode": "table",
                "placement": "bottom",
                "showLegend": True,
                "calcs": LEGEND_CALCS_STANDARD,
            },
        },
    }
    panels.append(consumption_panel)

    # Row 4: Grid Import/Export - paired with Power Production by Site
    panels.append(
        power_timeseries(
            title="Grid Import/Export by Site",
            sql=SQL_GRID_IMPORT_EXPORT,
            pos=GridPos(h=8, w=12, x=12, y=16),
            panel_id=4,
            description="Grid Import (red) = power drawn from grid. Grid Export (green) = power sent to grid. Both shown as positive values for clarity.",
            legend_calcs=LEGEND_CALCS_WITH_SUM,
            custom_options={"drawStyle": "bars", "fillOpacity": 80, "barAlignment": 0},
            overrides=[
                color_override(".*Import.*", RED),
                color_override(".*Export.*", GREEN),
            ],
        )
    )

    # Row 5: Enphase Energy Today + Energy Import/Export
    panels.append(
        power_timeseries(
            title="Enphase - Energy Today",
            sql=SQL_ENPHASE_ENERGY_TODAY,
            pos=GridPos(h=8, w=12, x=0, y=24),
            panel_id=14,
            description="Daily cumulative energy production from Enphase systems",
            custom_options={"fillOpacity": 20, "lineWidth": 2},
        )
    )
    # Fix unit for energy panels
    panels[-1]["fieldConfig"]["defaults"]["unit"] = "kWh"

    panels.append(
        power_timeseries(
            title="Energy Import/Export by Site",
            sql=SQL_ENERGY_IMPORT_EXPORT,
            pos=GridPos(h=8, w=12, x=12, y=24),
            panel_id=18,
            description="Cumulative energy imported from grid (red) and exported to grid (green) in kWh. These are cumulative values from Enphase telemetry.",
            legend_calcs=LEGEND_CALCS_WITH_SUM,
            custom_options={"lineInterpolation": "smooth", "fillOpacity": 20},
            overrides=[
                color_override(".*Imported.*", RED),
                color_override(".*Exported.*", GREEN),
            ],
        )
    )
    panels[-1]["fieldConfig"]["defaults"]["unit"] = "kWh"
    panels[-1]["fieldConfig"]["defaults"]["thresholds"]["steps"] = [
        {"color": TRANSPARENT, "value": None}
    ]

    # Row 7: Battery SOC Timeseries
    panels.append(
        {
            "id": 5,
            "gridPos": {"h": 8, "w": 12, "x": 0, "y": 32},
            "type": "timeseries",
            "title": "Battery State of Charge by Site",
            "description": "Per battery per minute; deduped. Multiple Tesla energy_site IDs under one location show as [suffix] in the legend. Legacy rows without energy_site_id may still split by row id.",
            "targets": [
                {
                    "datasource": DATASOURCE,
                    "editorMode": "code",
                    "format": "time_series",
                    "rawQuery": True,
                    "rawSql": SQL_BATTERY_SOC_TIMESERIES,
                    "refId": "A",
                }
            ],
            "fieldConfig": {
                "defaults": {
                    "color": {"mode": "palette-classic"},
                    "custom": {"drawStyle": "line", "fillOpacity": 20},
                    "max": 100,
                    "min": 0,
                    "thresholds": {
                        "mode": "absolute",
                        "steps": [
                            {"color": RED, "value": None},
                            {"color": YELLOW, "value": 20},
                            {"color": GREEN, "value": 80},
                        ],
                    },
                    "unit": "percent",
                }
            },
            "options": {
                "tooltip": {"mode": "multi"},
                "legend": {
                    "displayMode": "table",
                    "placement": "bottom",
                    "showLegend": True,
                    "calcs": LEGEND_CALCS_BATTERY,
                },
            },
        }
    )

    # Water Usage (next to Battery SOC) with Sprinkler Run overlay
    water_usage_panel = {
        "id": 19,
        "gridPos": {"h": 8, "w": 12, "x": 12, "y": 40},
        "type": "timeseries",
        "title": "Water Usage (Daily Cumulative)",
        "description": "Cumulative water usage for the day in gallons. Resets at midnight. "
        "Shaded areas indicate sprinkler/irrigation runs from Rachio.",
        "targets": [
            {
                "datasource": DATASOURCE,
                "editorMode": "code",
                "format": "time_series",
                "rawQuery": True,
                "rawSql": SQL_WATER_USAGE_DAILY,
                "refId": "A",
            },
            {
                "datasource": DATASOURCE,
                "editorMode": "code",
                "format": "time_series",
                "rawQuery": True,
                "rawSql": SQL_SPRINKLER_RUNS_TIMESERIES,
                "refId": "B",
            },
        ],
        "fieldConfig": {
            "defaults": {
                "color": {"mode": "palette-classic"},
                "custom": {
                    "axisCenteredZero": False,
                    "axisColorMode": "text",
                    "axisLabel": "",
                    "axisPlacement": "auto",
                    "drawStyle": "line",
                    "fillOpacity": 20,
                    "lineInterpolation": "linear",
                    "lineWidth": 2,
                    "pointSize": 5,
                    "showPoints": "never",
                    "spanNulls": False,
                },
                "mappings": [],
                "thresholds": {
                    "mode": "absolute",
                    "steps": [{"color": BLUE, "value": None}],
                },
                "unit": "gal",
            },
            "overrides": [
                # Style water usage series (Flume total)
                color_override("flume\\)", BLUE),
                # Style sprinkler zone series - use right axis, fill to show "running" periods
                # These are named like "FL - Zone Name (Start)" and "FL - Zone Name (End)"
                {
                    "matcher": {"id": "byRegexp", "options": ".*\\(Start\\)$|.*\\(End\\)$"},
                    "properties": [
                        {"id": "color", "value": {"fixedColor": ORANGE, "mode": "fixed"}},
                        {"id": "custom.axisPlacement", "value": "right"},
                        {"id": "custom.axisLabel", "value": "Sprinkler Zone"},
                        {"id": "custom.drawStyle", "value": "bars"},
                        {"id": "custom.fillOpacity", "value": 30},
                        {"id": "custom.lineWidth", "value": 0},
                        {"id": "unit", "value": "short"},
                        {"id": "min", "value": 0},
                        {"id": "max", "value": 1},
                        {"id": "custom.axisSoftMax", "value": 1},
                        {
                            "id": "mappings",
                            "value": [
                                {"type": "value", "options": {"0": {"text": "Off"}}},
                                {"type": "value", "options": {"1": {"text": "Running"}}},
                            ],
                        },
                    ],
                },
            ],
        },
        "options": {
            "tooltip": {"mode": "multi", "sort": "none"},
            "legend": {
                "displayMode": "table",
                "placement": "bottom",
                "showLegend": True,
                "calcs": LEGEND_CALCS_STANDARD,
            },
        },
    }
    # Water Usage Stats Panel (combined - to the left of Water Usage chart)
    water_stats_panel = {
        "id": 36,
        "gridPos": {"h": 8, "w": 12, "x": 0, "y": 40},
        "type": "stat",
        "title": "Water Usage Stats",
        "description": "Water usage statistics and sprinkler activity for current day/month",
        "targets": [
            {
                "datasource": DATASOURCE,
                "editorMode": "code",
                "format": "table",
                "rawQuery": True,
                "rawSql": SQL_WATER_USAGE_STATS,
                "refId": "A",
            }
        ],
        "fieldConfig": {
            "defaults": {
                "color": {"mode": "thresholds"},
                "mappings": [],
                "thresholds": {
                    "mode": "absolute",
                    "steps": [{"color": GREEN, "value": None}],
                },
            },
            "overrides": [
                {
                    "matcher": {"id": "byName", "options": "Water Usage Today (gal)"},
                    "properties": [
                        {
                            "id": "thresholds",
                            "value": {
                                "mode": "absolute",
                                "steps": [
                                    {"color": GREEN, "value": None},
                                    {"color": YELLOW, "value": 100},
                                    {"color": ORANGE, "value": 200},
                                    {"color": RED, "value": 300},
                                ],
                            },
                        },
                        {"id": "unit", "value": "gal"},
                        {"id": "displayName", "value": "Water Usage Today"},
                    ],
                },
                {
                    "matcher": {"id": "byName", "options": "Water Usage This Month (gal)"},
                    "properties": [
                        {
                            "id": "thresholds",
                            "value": {
                                "mode": "absolute",
                                "steps": [
                                    {"color": GREEN, "value": None},
                                    {"color": YELLOW, "value": 2000},
                                    {"color": ORANGE, "value": 4000},
                                    {"color": RED, "value": 6000},
                                ],
                            },
                        },
                        {"id": "unit", "value": "gal"},
                        {"id": "displayName", "value": "Water Usage This Month"},
                    ],
                },
                {
                    "matcher": {"id": "byName", "options": "Sprinkler Days"},
                    "properties": [
                        {
                            "id": "thresholds",
                            "value": {
                                "mode": "absolute",
                                "steps": [{"color": GREEN, "value": None}],
                            },
                        },
                        {"id": "unit", "value": "short"},
                        {"id": "displayName", "value": "Sprinkler Days This Month"},
                    ],
                },
            ],
        },
        "options": {
            "colorMode": "value",
            "graphMode": "area",
            "justifyMode": "auto",
            "orientation": "auto",
            "reduceOptions": {"values": False, "calcs": ["lastNotNull"], "fields": ""},
            "textMode": "auto",
            "text": {},
        },
    }
    panels.append(water_stats_panel)

    panels.append(water_usage_panel)

    panels.append(
        power_timeseries(
            title="Battery Charging/Discharging by Site",
            sql=SQL_BATTERY_CHARGING,
            pos=GridPos(h=8, w=12, x=12, y=32),
            panel_id=6,
            description="Positive = charging, Negative = discharging",
            custom_options={"axisCenteredZero": True, "fillOpacity": 10},
            overrides=[
                color_override("Charging", BLUE),
                color_override("Discharging", ORANGE),
            ],
            base_color=BLUE,
        )
    )

    # ==========================================================================
    # Solar Local (Enphase Gateway) Section
    # ==========================================================================

    # Solar Local Section Row Header
    panels.append(
        {
            "id": 40,
            "gridPos": {"h": 1, "w": 24, "x": 0, "y": 48},
            "type": "row",
            "title": "Solar Local (Enphase Gateways)",
            "collapsed": False,
        }
    )

    # Helper function for gateway stat panel field config
    def get_gateway_stat_field_config() -> dict:
        return {
            "defaults": {
                "color": {"mode": "thresholds"},
                "mappings": [],
                "thresholds": {
                    "mode": "absolute",
                    "steps": [{"color": GREEN, "value": None}],
                },
                "unit": "kwatt",
            },
            "overrides": [
                {
                    "matcher": {"id": "byName", "options": "Production (kW)"},
                    "properties": [
                        {
                            "id": "thresholds",
                            "value": {
                                "mode": "absolute",
                                "steps": [
                                    {"color": RED, "value": None},
                                    {"color": YELLOW, "value": 1},
                                    {"color": GREEN, "value": 3},
                                ],
                            },
                        },
                    ],
                },
                {
                    "matcher": {"id": "byName", "options": "Consumption (kW)"},
                    "properties": [
                        {
                            "id": "thresholds",
                            "value": {
                                "mode": "absolute",
                                "steps": [
                                    {"color": GREEN, "value": None},
                                    {"color": YELLOW, "value": 5},
                                    {"color": ORANGE, "value": 10},
                                    {"color": RED, "value": 15},
                                ],
                            },
                        },
                    ],
                },
                {
                    "matcher": {"id": "byName", "options": "Net (kW)"},
                    "properties": [
                        {
                            "id": "thresholds",
                            "value": {
                                "mode": "absolute",
                                "steps": [
                                    {"color": RED, "value": None},
                                    {"color": YELLOW, "value": -1},
                                    {"color": GREEN, "value": 0},
                                ],
                            },
                        },
                    ],
                },
                {
                    "matcher": {"id": "byName", "options": "Gateway"},
                    "properties": [
                        {
                            "id": "custom.hideFrom",
                            "value": {"tooltip": True, "viz": True, "legend": True},
                        },
                    ],
                },
            ],
        }

    def get_gateway_energy_field_config() -> dict:
        """Field config for gateway energy today panel (produced + consumed)."""
        return {
            "defaults": {
                "color": {"mode": "thresholds"},
                "mappings": [],
                "thresholds": {
                    "mode": "absolute",
                    "steps": [{"color": GREEN, "value": None}],
                },
                "unit": "kWh",
            },
            "overrides": [
                {
                    "matcher": {"id": "byName", "options": "Produced (kWh)"},
                    "properties": [
                        {
                            "id": "thresholds",
                            "value": {
                                "mode": "absolute",
                                "steps": [
                                    {"color": RED, "value": None},
                                    {"color": YELLOW, "value": 10},
                                    {"color": GREEN, "value": 30},
                                ],
                            },
                        },
                    ],
                },
                {
                    "matcher": {"id": "byName", "options": "Consumed (kWh)"},
                    "properties": [
                        {
                            "id": "thresholds",
                            "value": {
                                "mode": "absolute",
                                "steps": [
                                    {"color": GREEN, "value": None},
                                    {"color": YELLOW, "value": 30},
                                    {"color": ORANGE, "value": 50},
                                    {"color": RED, "value": 80},
                                ],
                            },
                        },
                    ],
                },
                {
                    "matcher": {"id": "byName", "options": "Gateway"},
                    "properties": [
                        {
                            "id": "custom.hideFrom",
                            "value": {"tooltip": True, "viz": True, "legend": True},
                        },
                    ],
                },
            ],
        }

    # Current Production/Consumption from Local Gateway 1
    panels.append(
        {
            "id": 41,
            "gridPos": {"h": 6, "w": 6, "x": 0, "y": 49},
            "type": "stat",
            "title": "Gateway 1 - Current",
            "description": "Current production and consumption from first local Enphase gateway. "
            "Includes consumption data not available via cloud API.",
            "targets": [
                {
                    "datasource": DATASOURCE,
                    "editorMode": "code",
                    "format": "table",
                    "rawQuery": True,
                    "rawSql": sql_enphase_local_current(0),
                    "refId": "A",
                }
            ],
            "fieldConfig": get_gateway_stat_field_config(),
            "options": {
                "colorMode": "background",
                "graphMode": "none",
                "justifyMode": "auto",
                "orientation": "horizontal",
                "reduceOptions": {"values": False, "calcs": ["lastNotNull"], "fields": ""},
                "textMode": "auto",
            },
        }
    )

    # Energy Today from Local Gateway 1
    panels.append(
        {
            "id": 42,
            "gridPos": {"h": 6, "w": 6, "x": 6, "y": 49},
            "type": "stat",
            "title": "Gateway 1 - Energy Today",
            "description": "Energy produced and consumed today from first local Enphase gateway.",
            "targets": [
                {
                    "datasource": DATASOURCE,
                    "editorMode": "code",
                    "format": "table",
                    "rawQuery": True,
                    "rawSql": sql_enphase_local_energy_today(0),
                    "refId": "A",
                }
            ],
            "fieldConfig": get_gateway_energy_field_config(),
            "options": {
                "colorMode": "background",
                "graphMode": "none",
                "justifyMode": "auto",
                "orientation": "horizontal",
                "reduceOptions": {"values": False, "calcs": ["lastNotNull"], "fields": ""},
                "textMode": "auto",
            },
        }
    )

    # Current Production/Consumption from Local Gateway 2
    panels.append(
        {
            "id": 46,
            "gridPos": {"h": 6, "w": 6, "x": 12, "y": 49},
            "type": "stat",
            "title": "Gateway 2 - Current",
            "description": "Current production and consumption from second local Enphase gateway. "
            "Shows no data if only one gateway is configured.",
            "targets": [
                {
                    "datasource": DATASOURCE,
                    "editorMode": "code",
                    "format": "table",
                    "rawQuery": True,
                    "rawSql": sql_enphase_local_current(1),
                    "refId": "A",
                }
            ],
            "fieldConfig": get_gateway_stat_field_config(),
            "options": {
                "colorMode": "background",
                "graphMode": "none",
                "justifyMode": "auto",
                "orientation": "horizontal",
                "reduceOptions": {"values": False, "calcs": ["lastNotNull"], "fields": ""},
                "textMode": "auto",
            },
        }
    )

    # Energy Today from Local Gateway 2
    panels.append(
        {
            "id": 47,
            "gridPos": {"h": 6, "w": 6, "x": 18, "y": 49},
            "type": "stat",
            "title": "Gateway 2 - Energy Today",
            "description": "Energy produced and consumed today from second local Enphase gateway. "
            "Shows no data if only one gateway is configured.",
            "targets": [
                {
                    "datasource": DATASOURCE,
                    "editorMode": "code",
                    "format": "table",
                    "rawQuery": True,
                    "rawSql": sql_enphase_local_energy_today(1),
                    "refId": "A",
                }
            ],
            "fieldConfig": get_gateway_energy_field_config(),
            "options": {
                "colorMode": "background",
                "graphMode": "none",
                "justifyMode": "auto",
                "orientation": "horizontal",
                "reduceOptions": {"values": False, "calcs": ["lastNotNull"], "fields": ""},
                "textMode": "auto",
            },
        }
    )

    # Production vs Consumption from Local Gateways (time series)
    panels.append(
        power_timeseries(
            title="Local Gateways - Production vs Consumption",
            sql=SQL_ENPHASE_LOCAL_PRODUCTION_VS_CONSUMPTION,
            pos=GridPos(h=6, w=24, x=0, y=55),
            panel_id=43,
            description="Production and consumption from all local Enphase gateways over time. "
            "Shows real consumption data not available from cloud API. Each gateway is shown separately.",
            legend_calcs=["lastNotNull", "max", "min", "mean"],
            overrides=[
                color_override("Production", GREEN),
                color_override("Consumption", RED),
            ],
        )
    )

    # Grid Voltage from Local Gateways
    panels.append(
        {
            "id": 44,
            "gridPos": {"h": 6, "w": 12, "x": 0, "y": 61},
            "type": "timeseries",
            "title": "Local Gateways - Grid Voltage",
            "description": "Grid voltage per phase from local Enphase gateways. Each gateway shows L1 and L2 separately.",
            "targets": [
                {
                    "datasource": DATASOURCE,
                    "editorMode": "code",
                    "format": "time_series",
                    "rawQuery": True,
                    "rawSql": SQL_ENPHASE_LOCAL_GRID_VOLTAGE,
                    "refId": "A",
                }
            ],
            "fieldConfig": {
                "defaults": {
                    "color": {"mode": "palette-classic"},
                    "custom": {
                        "axisCenteredZero": False,
                        "axisColorMode": "text",
                        "axisLabel": "Voltage (V)",
                        "axisPlacement": "auto",
                        "drawStyle": "line",
                        "fillOpacity": 5,
                        "lineInterpolation": "smooth",
                        "lineWidth": 2,
                        "pointSize": 5,
                        "showPoints": "never",
                        "spanNulls": True,
                    },
                    "mappings": [],
                    "min": 110,
                    "max": 130,
                    "thresholds": {
                        "mode": "absolute",
                        "steps": [
                            {"color": RED, "value": None},
                            {"color": YELLOW, "value": 114},
                            {"color": GREEN, "value": 117},
                            {"color": YELLOW, "value": 126},
                            {"color": RED, "value": 128},
                        ],
                    },
                    "unit": "volt",
                },
                "overrides": [
                    color_override(".*L1$", BLUE),
                    color_override(".*L2$", ORANGE),
                ],
            },
            "options": {
                "tooltip": {"mode": "multi", "sort": "none"},
                "legend": {
                    "displayMode": "table",
                    "placement": "bottom",
                    "showLegend": True,
                    "calcs": ["lastNotNull", "min", "max", "mean"],
                },
            },
        }
    )

    # Net Power from Local Gateways
    panels.append(
        power_timeseries(
            title="Local Gateways - Net Power (Grid Import/Export)",
            sql=SQL_ENPHASE_LOCAL_NET_POWER,
            pos=GridPos(h=6, w=12, x=12, y=61),
            panel_id=45,
            description="Net power from local Enphase gateways. "
            "Positive = importing from grid, Negative = exporting to grid. Each gateway shown separately.",
            legend_calcs=["lastNotNull", "max", "min", "mean"],
            custom_options={"axisCenteredZero": True, "fillOpacity": 20},
            overrides=[
                {
                    "matcher": {"id": "byRegexp", "options": ".*"},
                    "properties": [
                        {
                            "id": "thresholds",
                            "value": {
                                "mode": "absolute",
                                "steps": [
                                    {"color": GREEN, "value": None},
                                    {"color": YELLOW, "value": 0},
                                    {"color": RED, "value": 5},
                                ],
                            },
                        },
                    ],
                },
            ],
        )
    )

    # ==========================================================================
    # Pool Section (Row Header + Panels)
    # ==========================================================================

    # Pool Section Row Header
    panels.append(
        {
            "id": 29,
            "gridPos": {"h": 1, "w": 24, "x": 0, "y": 67},
            "type": "row",
            "title": "Pool",
            "collapsed": False,
        }
    )

    # Pool/Spa Equipment Status
    panels.append(
        {
            "id": 26,
            "gridPos": {"h": 8, "w": 6, "x": 0, "y": 68},
            "type": "stat",
            "title": "Pool Equipment Status",
            "description": "Current status of pool/spa pumps, heaters, and temperatures. "
            "Temps show n/a when the corresponding pump is off.",
            "targets": [
                {
                    "datasource": DATASOURCE,
                    "editorMode": "code",
                    "format": "table",
                    "rawQuery": True,
                    "rawSql": SQL_CURRENT_POOL_TEMPS,
                    "refId": "A",
                }
            ],
            "fieldConfig": {
                "defaults": {
                    "color": {"mode": "thresholds"},
                    "mappings": [
                        {"type": "value", "options": {"true": {"text": "ON", "color": GREEN}}},
                        {"type": "value", "options": {"false": {"text": "OFF", "color": RED}}},
                    ],
                    "thresholds": {
                        "mode": "absolute",
                        "steps": [{"color": RED, "value": None}],
                    },
                    "unit": "none",
                },
                "overrides": [
                    # Pool Temp: show n/a for 0, use fahrenheit unit
                    {
                        "matcher": {"id": "byName", "options": "Pool Temp (°F)"},
                        "properties": [
                            {"id": "unit", "value": "fahrenheit"},
                            {
                                "id": "mappings",
                                "value": [
                                    {
                                        "type": "value",
                                        "options": {"0": {"text": "n/a", "color": "text"}},
                                    },
                                ],
                            },
                            {
                                "id": "thresholds",
                                "value": {
                                    "mode": "absolute",
                                    "steps": [
                                        {"color": BLUE, "value": None},
                                        {"color": GREEN, "value": 70},
                                        {"color": YELLOW, "value": 85},
                                        {"color": ORANGE, "value": 90},
                                    ],
                                },
                            },
                        ],
                    },
                    # Spa Temp: show n/a for 0, use fahrenheit unit
                    {
                        "matcher": {"id": "byName", "options": "Spa Temp (°F)"},
                        "properties": [
                            {"id": "unit", "value": "fahrenheit"},
                            {
                                "id": "mappings",
                                "value": [
                                    {
                                        "type": "value",
                                        "options": {"0": {"text": "n/a", "color": "text"}},
                                    },
                                ],
                            },
                            {
                                "id": "thresholds",
                                "value": {
                                    "mode": "absolute",
                                    "steps": [
                                        {"color": BLUE, "value": None},
                                        {"color": GREEN, "value": 98},
                                        {"color": YELLOW, "value": 102},
                                        {"color": ORANGE, "value": 104},
                                    ],
                                },
                            },
                        ],
                    },
                    # Air Temp: use fahrenheit unit (no n/a mapping needed)
                    {
                        "matcher": {"id": "byName", "options": "Air Temp (°F)"},
                        "properties": [
                            {"id": "unit", "value": "fahrenheit"},
                            {
                                "id": "thresholds",
                                "value": {
                                    "mode": "absolute",
                                    "steps": [
                                        {"color": BLUE, "value": None},
                                        {"color": GREEN, "value": 60},
                                        {"color": YELLOW, "value": 85},
                                        {"color": ORANGE, "value": 95},
                                        {"color": RED, "value": 100},
                                    ],
                                },
                            },
                        ],
                    },
                ],
            },
            "options": {
                "colorMode": "background",
                "graphMode": "none",
                "justifyMode": "auto",
                "orientation": "horizontal",
                "reduceOptions": {"values": False, "calcs": ["lastNotNull"], "fields": ""},
                "textMode": "auto",
            },
        }
    )

    # Pool/Spa Temperatures Timeseries
    panels.append(
        {
            "id": 27,
            "gridPos": {"h": 8, "w": 12, "x": 6, "y": 68},
            "type": "timeseries",
            "title": "Pool/Spa Temperatures Over Time",
            "description": "Historical pool, spa, and air temperatures",
            "targets": [
                {
                    "datasource": DATASOURCE,
                    "editorMode": "code",
                    "format": "time_series",
                    "rawQuery": True,
                    "rawSql": SQL_POOL_TEMPS_TIMESERIES,
                    "refId": "A",
                }
            ],
            "fieldConfig": {
                "defaults": {
                    "color": {"mode": "palette-classic"},
                    "custom": {
                        "axisCenteredZero": False,
                        "axisColorMode": "text",
                        "axisLabel": "Temperature (°F)",
                        "axisPlacement": "auto",
                        "drawStyle": "line",
                        "fillOpacity": 10,
                        "lineInterpolation": "smooth",
                        "lineWidth": 2,
                        "pointSize": 5,
                        "showPoints": "never",
                        "spanNulls": True,
                    },
                    "mappings": [],
                    "thresholds": {
                        "mode": "absolute",
                        "steps": [{"color": BLUE, "value": None}],
                    },
                    "unit": "fahrenheit",
                },
                "overrides": [
                    color_override(".*Pool$", BLUE),
                    color_override(".*Spa$", ORANGE),
                    color_override(".*Air$", GREEN),
                ],
            },
            "options": {
                "tooltip": {"mode": "multi", "sort": "none"},
                "legend": {
                    "displayMode": "table",
                    "placement": "bottom",
                    "showLegend": True,
                    "calcs": ["lastNotNull", "min", "max", "mean"],
                },
            },
        }
    )

    # Pool Pump/Heater Status Timeseries (0/1 Off/On)
    panels.append(
        {
            "id": 28,
            "gridPos": {"h": 8, "w": 6, "x": 18, "y": 68},
            "type": "timeseries",
            "title": "Pool Pump & Heater Status",
            "description": "Shows when pool/spa pumps and heaters were running (0=Off, 1=On)",
            "targets": [
                {
                    "datasource": DATASOURCE,
                    "editorMode": "code",
                    "format": "time_series",
                    "rawQuery": True,
                    "rawSql": SQL_POOL_PUMP_STATUS,
                    "refId": "A",
                },
                {
                    "datasource": DATASOURCE,
                    "editorMode": "code",
                    "format": "time_series",
                    "rawQuery": True,
                    "rawSql": SQL_POOL_HEATER_STATUS,
                    "refId": "B",
                },
            ],
            "fieldConfig": {
                "defaults": {
                    "color": {"mode": "palette-classic"},
                    "custom": {
                        "axisCenteredZero": False,
                        "axisColorMode": "text",
                        "axisLabel": "",
                        "axisPlacement": "auto",
                        "drawStyle": "line",
                        "fillOpacity": 30,
                        "lineInterpolation": "stepAfter",
                        "lineWidth": 2,
                        "pointSize": 5,
                        "showPoints": "never",
                        "spanNulls": False,
                        "stacking": {"group": "A", "mode": "none"},
                    },
                    "mappings": [
                        {"type": "value", "options": {"0": {"text": "Off"}}},
                        {"type": "value", "options": {"1": {"text": "On"}}},
                    ],
                    "min": 0,
                    "max": 1,
                    "thresholds": {
                        "mode": "absolute",
                        "steps": [
                            {"color": RED, "value": None},
                            {"color": GREEN, "value": 1},
                        ],
                    },
                    "unit": "none",
                },
                "overrides": [
                    color_override(".*Pool Pump$", BLUE),
                    color_override(".*Pool Heater$", LIGHT_BLUE),
                    color_override(".*Spa Pump$", ORANGE),
                    color_override(".*Spa Heater$", DARK_ORANGE),
                ],
            },
            "options": {
                "tooltip": {"mode": "multi", "sort": "none"},
                "legend": {
                    "displayMode": "table",
                    "placement": "bottom",
                    "showLegend": True,
                    "calcs": ["lastNotNull"],
                },
            },
        }
    )

    # ==========================================================================
    # Propane Section (Row Header + Panels)
    # ==========================================================================

    # Propane Section Row Header
    panels.append(
        {
            "id": 30,
            "gridPos": {"h": 1, "w": 24, "x": 0, "y": 76},
            "type": "row",
            "title": "Propane",
            "collapsed": False,
        }
    )

    # Propane Tank Level gauge
    panels.append(
        {
            "id": 21,
            "gridPos": {"h": 8, "w": 6, "x": 0, "y": 77},
            "type": "gauge",
            "title": "Propane Tank Level",
            "targets": [
                {
                    "datasource": DATASOURCE,
                    "editorMode": "code",
                    "format": "table",
                    "rawQuery": True,
                    "rawSql": SQL_CURRENT_PROPANE_LEVEL,
                    "refId": "A",
                }
            ],
            "fieldConfig": {
                "defaults": {
                    "color": {"mode": "thresholds"},
                    "mappings": [],
                    "min": 0,
                    "max": 100,
                    "thresholds": {
                        "mode": "absolute",
                        "steps": [
                            {"color": RED, "value": None},
                            {"color": ORANGE, "value": 15},
                            {"color": YELLOW, "value": 25},
                            {"color": GREEN, "value": 40},
                        ],
                    },
                    "unit": "percent",
                },
                "overrides": [
                    {
                        "matcher": {"id": "byName", "options": "Gallons"},
                        "properties": [
                            {
                                "id": "custom.hideFrom",
                                "value": {"tooltip": False, "viz": True, "legend": False},
                            },
                        ],
                    },
                    {
                        "matcher": {"id": "byName", "options": "Capacity"},
                        "properties": [
                            {
                                "id": "custom.hideFrom",
                                "value": {"tooltip": False, "viz": True, "legend": False},
                            },
                        ],
                    },
                ],
            },
            "options": {
                "minVizHeight": 75,
                "minVizWidth": 75,
                "orientation": "auto",
                "reduceOptions": {
                    "values": False,
                    "calcs": ["lastNotNull"],
                    "fields": "/^Tank Level \\(%\\)$/",
                },
                "showThresholdLabels": False,
                "showThresholdMarkers": True,
                "sizing": "auto",
                "text": {
                    "titleSize": 14,
                    "valueSize": 32,
                },
            },
        }
    )

    # Propane Gallons stat
    panels.append(
        stat_panel(
            title="Propane (Gallons)",
            sql=SQL_CURRENT_PROPANE_LEVEL,
            pos=GridPos(h=8, w=6, x=6, y=77),
            panel_id=22,
            thresholds=[
                threshold(RED),
                threshold(ORANGE, 75),
                threshold(YELLOW, 125),
                threshold(GREEN, 200),
            ],
            unit="gal",
            min_val=0,
            max_val=None,
            color_mode="value",
            graph_mode="none",
            description="Current propane tank level in gallons",
            overrides=[
                {
                    "matcher": {"id": "byName", "options": "Tank Level (%)"},
                    "properties": [
                        {
                            "id": "custom.hideFrom",
                            "value": {"tooltip": True, "viz": True, "legend": True},
                        },
                    ],
                },
                {
                    "matcher": {"id": "byName", "options": "Capacity"},
                    "properties": [
                        {
                            "id": "custom.hideFrom",
                            "value": {"tooltip": False, "viz": True, "legend": True},
                        },
                    ],
                },
            ],
        )
    )

    # Propane Tank Level Timeseries
    panels.append(
        {
            "id": 23,
            "gridPos": {"h": 8, "w": 12, "x": 12, "y": 77},
            "type": "timeseries",
            "title": "Propane Tank Level Over Time",
            "description": "Propane tank level percentage over time. Tank updates approximately every 6-24 hours.",
            "targets": [
                {
                    "datasource": DATASOURCE,
                    "editorMode": "code",
                    "format": "time_series",
                    "rawQuery": True,
                    "rawSql": SQL_PROPANE_LEVEL_TIMESERIES,
                    "refId": "A",
                }
            ],
            "fieldConfig": {
                "defaults": {
                    "color": {"mode": "thresholds"},
                    "custom": {
                        "axisCenteredZero": False,
                        "axisColorMode": "text",
                        "axisLabel": "",
                        "axisPlacement": "auto",
                        "drawStyle": "line",
                        "fillOpacity": 20,
                        "lineInterpolation": "stepAfter",
                        "lineWidth": 2,
                        "pointSize": 5,
                        "showPoints": "always",
                        "spanNulls": True,
                    },
                    "mappings": [],
                    "min": 0,
                    "max": 100,
                    "thresholds": {
                        "mode": "absolute",
                        "steps": [
                            {"color": RED, "value": None},
                            {"color": ORANGE, "value": 15},
                            {"color": YELLOW, "value": 25},
                            {"color": GREEN, "value": 40},
                        ],
                    },
                    "unit": "percent",
                },
                "overrides": [],
            },
            "options": {
                "tooltip": {"mode": "multi", "sort": "none"},
                "legend": {
                    "displayMode": "table",
                    "placement": "bottom",
                    "showLegend": True,
                    "calcs": ["lastNotNull", "min", "max", "mean"],
                },
            },
        }
    )

    # ==========================================================================
    # System Section (Row Header + Panels)
    # ==========================================================================

    # System Section Row Header
    panels.append(
        {
            "id": 31,
            "gridPos": {"h": 1, "w": 24, "x": 0, "y": 85},
            "type": "row",
            "title": "System",
            "collapsed": False,
        }
    )

    # Current System Stats (CPU + Memory)
    panels.append(
        {
            "id": 32,
            "gridPos": {"h": 6, "w": 6, "x": 0, "y": 86},
            "type": "stat",
            "title": "System Status",
            "description": "Current CPU, memory, and disk usage of the home-monitor service",
            "targets": [
                {
                    "datasource": DATASOURCE,
                    "editorMode": "code",
                    "format": "table",
                    "rawQuery": True,
                    "rawSql": SQL_CURRENT_SYSTEM_STATS,
                    "refId": "A",
                }
            ],
            "fieldConfig": {
                "defaults": {
                    "color": {"mode": "thresholds"},
                    "mappings": [],
                    "min": 0,
                    "max": 100,
                    "thresholds": {
                        "mode": "absolute",
                        "steps": [
                            {"color": GREEN, "value": None},
                            {"color": YELLOW, "value": 50},
                            {"color": ORANGE, "value": 75},
                            {"color": RED, "value": 90},
                        ],
                    },
                    "unit": "percent",
                },
                "overrides": [],
            },
            "options": {
                "colorMode": "background",
                "graphMode": "none",
                "justifyMode": "auto",
                "orientation": "horizontal",
                "reduceOptions": {"values": False, "calcs": ["lastNotNull"], "fields": ""},
                "textMode": "auto",
            },
        }
    )

    # CPU Usage Timeseries
    panels.append(
        {
            "id": 33,
            "gridPos": {"h": 6, "w": 6, "x": 6, "y": 86},
            "type": "timeseries",
            "title": "CPU Usage Over Time",
            "description": "CPU usage of the home-monitor service over time",
            "targets": [
                {
                    "datasource": DATASOURCE,
                    "editorMode": "code",
                    "format": "time_series",
                    "rawQuery": True,
                    "rawSql": SQL_CPU_TIMESERIES,
                    "refId": "A",
                }
            ],
            "fieldConfig": {
                "defaults": {
                    "color": {"fixedColor": BLUE, "mode": "fixed"},
                    "custom": {
                        "axisCenteredZero": False,
                        "axisColorMode": "text",
                        "axisLabel": "",
                        "axisPlacement": "auto",
                        "drawStyle": "line",
                        "fillOpacity": 10,
                        "lineInterpolation": "smooth",
                        "lineWidth": 2,
                        "pointSize": 5,
                        "showPoints": "never",
                        "spanNulls": False,
                    },
                    "mappings": [],
                    "min": 0,
                    "max": 100,
                    "thresholds": {
                        "mode": "absolute",
                        "steps": [
                            {"color": GREEN, "value": None},
                            {"color": YELLOW, "value": 50},
                            {"color": ORANGE, "value": 75},
                            {"color": RED, "value": 90},
                        ],
                    },
                    "unit": "percent",
                },
                "overrides": [],
            },
            "options": {
                "tooltip": {"mode": "multi", "sort": "none"},
                "legend": {
                    "displayMode": "table",
                    "placement": "bottom",
                    "showLegend": True,
                    "calcs": ["lastNotNull", "min", "max", "mean"],
                },
            },
        }
    )

    # Memory Usage Timeseries
    panels.append(
        {
            "id": 34,
            "gridPos": {"h": 6, "w": 6, "x": 12, "y": 86},
            "type": "timeseries",
            "title": "Memory Usage Over Time",
            "description": "Memory usage of the home-monitor service over time",
            "targets": [
                {
                    "datasource": DATASOURCE,
                    "editorMode": "code",
                    "format": "time_series",
                    "rawQuery": True,
                    "rawSql": SQL_MEMORY_TIMESERIES,
                    "refId": "A",
                }
            ],
            "fieldConfig": {
                "defaults": {
                    "color": {"fixedColor": ORANGE, "mode": "fixed"},
                    "custom": {
                        "axisCenteredZero": False,
                        "axisColorMode": "text",
                        "axisLabel": "",
                        "axisPlacement": "auto",
                        "drawStyle": "line",
                        "fillOpacity": 10,
                        "lineInterpolation": "smooth",
                        "lineWidth": 2,
                        "pointSize": 5,
                        "showPoints": "never",
                        "spanNulls": False,
                    },
                    "mappings": [],
                    "min": 0,
                    "max": 100,
                    "thresholds": {
                        "mode": "absolute",
                        "steps": [
                            {"color": GREEN, "value": None},
                            {"color": YELLOW, "value": 50},
                            {"color": ORANGE, "value": 75},
                            {"color": RED, "value": 90},
                        ],
                    },
                    "unit": "percent",
                },
                "overrides": [],
            },
            "options": {
                "tooltip": {"mode": "multi", "sort": "none"},
                "legend": {
                    "displayMode": "table",
                    "placement": "bottom",
                    "showLegend": True,
                    "calcs": ["lastNotNull", "min", "max", "mean"],
                },
            },
        }
    )

    # Disk Usage Timeseries
    panels.append(
        {
            "id": 35,
            "gridPos": {"h": 6, "w": 6, "x": 18, "y": 86},
            "type": "timeseries",
            "title": "Disk Usage Over Time",
            "description": "Disk usage of the home-monitor service over time",
            "targets": [
                {
                    "datasource": DATASOURCE,
                    "editorMode": "code",
                    "format": "time_series",
                    "rawQuery": True,
                    "rawSql": SQL_DISK_TIMESERIES,
                    "refId": "A",
                }
            ],
            "fieldConfig": {
                "defaults": {
                    "color": {"fixedColor": GREEN, "mode": "fixed"},
                    "custom": {
                        "axisCenteredZero": False,
                        "axisColorMode": "text",
                        "axisLabel": "",
                        "axisPlacement": "auto",
                        "drawStyle": "line",
                        "fillOpacity": 10,
                        "lineInterpolation": "smooth",
                        "lineWidth": 2,
                        "pointSize": 5,
                        "showPoints": "never",
                        "spanNulls": False,
                    },
                    "mappings": [],
                    "min": 0,
                    "max": 100,
                    "thresholds": {
                        "mode": "absolute",
                        "steps": [
                            {"color": GREEN, "value": None},
                            {"color": YELLOW, "value": 50},
                            {"color": ORANGE, "value": 75},
                            {"color": RED, "value": 90},
                        ],
                    },
                    "unit": "percent",
                },
                "overrides": [],
            },
            "options": {
                "tooltip": {"mode": "multi", "sort": "none"},
                "legend": {
                    "displayMode": "table",
                    "placement": "bottom",
                    "showLegend": True,
                    "calcs": ["lastNotNull", "min", "max", "mean"],
                },
            },
        }
    )

    # ==========================================================================
    # Span Panel Section (Row Header + Panels)
    # ==========================================================================

    # Span Section Row Header
    panels.append(
        {
            "id": 80,
            "gridPos": {"h": 1, "w": 24, "x": 0, "y": 92},
            "type": "row",
            "title": "Span Electrical Panel(s)",
            "collapsed": False,
        }
    )

    # Row 1: Panel Overview - Grid Power, Feedthrough, Status, Battery
    panels.append(
        power_timeseries(
            title="Span Grid Power",
            sql=SQL_SPAN_GRID_POWER,
            pos=GridPos(h=8, w=8, x=0, y=93),
            panel_id=81,
            description="Total grid power measured by Span panel (positive = importing, negative = exporting).",
            legend_calcs=LEGEND_CALCS_STANDARD,
        )
    )

    panels.append(
        power_timeseries(
            title="Span Feedthrough Power",
            sql=SQL_SPAN_FEEDTHROUGH_POWER,
            pos=GridPos(h=8, w=8, x=8, y=93),
            panel_id=82,
            description="Power flowing through non-Span breakers (not individually monitored).",
            legend_calcs=LEGEND_CALCS_STANDARD,
        )
    )

    # Panel Status Table
    panels.append(
        {
            "id": 83,
            "gridPos": {"h": 4, "w": 8, "x": 16, "y": 93},
            "type": "table",
            "title": "Panel Status",
            "targets": [
                {
                    "datasource": DATASOURCE,
                    "editorMode": "code",
                    "format": "table",
                    "rawQuery": True,
                    "rawSql": SQL_SPAN_PANEL_STATUS,
                    "refId": "A",
                },
            ],
            "fieldConfig": {
                "defaults": {
                    "color": {"mode": "thresholds"},
                    "custom": {"align": "auto", "displayMode": "auto"},
                    "mappings": [],
                    "thresholds": {
                        "mode": "absolute",
                        "steps": [{"color": GREEN, "value": None}],
                    },
                },
                "overrides": [
                    {
                        "matcher": {"id": "byName", "options": "Main Relay"},
                        "properties": [
                            {
                                "id": "mappings",
                                "value": [
                                    {
                                        "options": {"CLOSED": {"color": GREEN, "text": "CLOSED"}},
                                        "type": "value",
                                    },
                                    {
                                        "options": {"OPEN": {"color": RED, "text": "OPEN"}},
                                        "type": "value",
                                    },
                                ],
                            },
                        ],
                    },
                    {
                        "matcher": {"id": "byName", "options": "Door"},
                        "properties": [
                            {
                                "id": "mappings",
                                "value": [
                                    {
                                        "options": {"CLOSED": {"color": GREEN, "text": "CLOSED"}},
                                        "type": "value",
                                    },
                                    {
                                        "options": {"OPEN": {"color": YELLOW, "text": "OPEN"}},
                                        "type": "value",
                                    },
                                ],
                            },
                        ],
                    },
                ],
            },
            "options": {
                "showHeader": True,
                "cellHeight": "sm",
                "footer": {"show": False},
            },
        }
    )

    # Battery SOC (if available) - two panels for multiple Span panels
    # Uses text_mode="value_and_name" to display the panel name from the database
    span_battery_thresholds = [
        threshold(RED),
        threshold(YELLOW, 20),
        threshold(GREEN, 80),
    ]

    panels.append(
        stat_panel(
            title="Battery SOC",
            sql=sql_span_battery_soc(0),
            pos=GridPos(h=4, w=4, x=16, y=97),
            panel_id=84,
            thresholds=span_battery_thresholds,
            unit="percent",
            min_val=0,
            max_val=100,
            text_mode="value_and_name",
            query_format="time_series",
        )
    )

    panels.append(
        stat_panel(
            title="Battery SOC",
            sql=sql_span_battery_soc(1),
            pos=GridPos(h=4, w=4, x=20, y=97),
            panel_id=95,
            thresholds=span_battery_thresholds,
            unit="percent",
            min_val=0,
            max_val=100,
            text_mode="value_and_name",
            query_format="time_series",
        )
    )

    # Row 2: Circuit Power Distribution
    panels.append(
        {
            "id": 85,
            "gridPos": {"h": 8, "w": 6, "x": 0, "y": 101},
            "type": "bargauge",
            "title": "Top 10 Circuits by Power",
            "description": "Top 10 circuits by absolute power consumption.",
            "targets": [
                {
                    "datasource": DATASOURCE,
                    "editorMode": "code",
                    "format": "table",
                    "rawQuery": True,
                    "rawSql": SQL_SPAN_TOP_CIRCUITS_BY_POWER,
                    "refId": "A",
                }
            ],
            "transformations": [
                {
                    "id": "rowsToFields",
                    "options": {
                        "mappings": [
                            {"fieldName": "metric", "handlerKey": "field.name"},
                            {"fieldName": "Power (W)", "handlerKey": "field.value"},
                        ]
                    },
                }
            ],
            "fieldConfig": {
                "defaults": {
                    "color": {"mode": "continuous-GrYlRd"},
                    "mappings": [],
                    "thresholds": {
                        "mode": "absolute",
                        "steps": [
                            {"color": GREEN, "value": 0},
                            {"color": YELLOW, "value": 1000},
                            {"color": ORANGE, "value": 3000},
                            {"color": RED, "value": 5000},
                        ],
                    },
                    "min": 0,
                    "max": 10000,
                    "unit": "watt",
                },
                "overrides": [],
            },
            "options": {
                "displayMode": "lcd",
                "legend": {
                    "calcs": [],
                    "displayMode": "list",
                    "placement": "bottom",
                    "showLegend": False,
                },
                "maxVizHeight": 300,
                "minVizHeight": 16,
                "minVizWidth": 8,
                "namePlacement": "auto",
                "orientation": "horizontal",
                "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": True},
                "showUnfilled": True,
                "sizing": "auto",
                "valueMode": "color",
            },
            "pluginVersion": "12.3.1",
        }
    )

    panels.append(
        power_timeseries(
            title="Total Circuit Power",
            sql=SQL_SPAN_TOTAL_CIRCUIT_POWER,
            pos=GridPos(h=8, w=6, x=6, y=101),
            panel_id=86,
            description="Sum of all circuit power readings over time.",
            legend_calcs=LEGEND_CALCS_STANDARD,
        )
    )

    # Top Energy Consumers Pie Chart
    panels.append(
        {
            "id": 87,
            "gridPos": {"h": 8, "w": 12, "x": 12, "y": 101},
            "type": "piechart",
            "title": "Top Energy Consumers",
            "description": "Top 10 circuits by total energy consumption in selected time range.",
            "targets": [
                {
                    "datasource": DATASOURCE,
                    "editorMode": "code",
                    "format": "table",
                    "rawQuery": True,
                    "rawSql": SQL_SPAN_TOP_ENERGY_CONSUMERS,
                    "refId": "A",
                },
            ],
            "transformations": [
                {
                    "id": "rowsToFields",
                    "options": {
                        "mappings": [
                            {"fieldName": "metric", "handlerKey": "field.name"},
                            {"fieldName": "Energy (kWh)", "handlerKey": "field.value"},
                        ]
                    },
                }
            ],
            "fieldConfig": {
                "defaults": {
                    "color": {"mode": "palette-classic"},
                    "mappings": [],
                    "unit": "kWh",
                },
                "overrides": [],
            },
            "options": {
                "legend": {
                    "displayMode": "table",
                    "placement": "right",
                    "showLegend": True,
                    "values": ["value", "percent"],
                },
                "pieType": "pie",
                "reduceOptions": {
                    "calcs": ["lastNotNull"],
                    "fields": "",
                    "values": True,
                },
                "tooltip": {"mode": "single", "sort": "none"},
            },
        }
    )

    # Row 3: Circuit Details Table
    panels.append(
        {
            "id": 88,
            "gridPos": {"h": 10, "w": 24, "x": 0, "y": 109},
            "type": "table",
            "title": "Circuit Details",
            "description": "Current status and readings for all circuits.",
            "targets": [
                {
                    "datasource": DATASOURCE,
                    "editorMode": "code",
                    "format": "table",
                    "rawQuery": True,
                    "rawSql": SQL_SPAN_CIRCUIT_TABLE,
                    "refId": "A",
                },
            ],
            "fieldConfig": {
                "defaults": {
                    "color": {"mode": "thresholds"},
                    "custom": {"align": "auto", "displayMode": "auto"},
                    "mappings": [],
                    "thresholds": {
                        "mode": "absolute",
                        "steps": [{"color": GREEN, "value": None}],
                    },
                },
                "overrides": [
                    {
                        "matcher": {"id": "byName", "options": "Power (W)"},
                        "properties": [
                            {"id": "unit", "value": "watt"},
                            {
                                "id": "custom.displayMode",
                                "value": "gradient-gauge",
                            },
                            {"id": "min", "value": 0},
                            {"id": "max", "value": 5000},
                        ],
                    },
                    {
                        "matcher": {"id": "byName", "options": "Import (kWh)"},
                        "properties": [
                            {"id": "unit", "value": "kWh"},
                        ],
                    },
                    {
                        "matcher": {"id": "byName", "options": "Export (kWh)"},
                        "properties": [
                            {"id": "unit", "value": "kWh"},
                        ],
                    },
                    {
                        "matcher": {"id": "byName", "options": "Relay"},
                        "properties": [
                            {
                                "id": "mappings",
                                "value": [
                                    {
                                        "options": {"CLOSED": {"color": GREEN, "text": "ON"}},
                                        "type": "value",
                                    },
                                    {
                                        "options": {"OPEN": {"color": RED, "text": "OFF"}},
                                        "type": "value",
                                    },
                                ],
                            },
                        ],
                    },
                    {
                        "matcher": {"id": "byName", "options": "Priority"},
                        "properties": [
                            {
                                "id": "mappings",
                                "value": [
                                    {
                                        "options": {
                                            "MUST_HAVE": {"color": RED, "text": "Must Have"}
                                        },
                                        "type": "value",
                                    },
                                    {
                                        "options": {
                                            "NICE_TO_HAVE": {
                                                "color": YELLOW,
                                                "text": "Nice to Have",
                                            }
                                        },
                                        "type": "value",
                                    },
                                    {
                                        "options": {
                                            "NOT_ESSENTIAL": {
                                                "color": GREEN,
                                                "text": "Not Essential",
                                            }
                                        },
                                        "type": "value",
                                    },
                                ],
                            },
                        ],
                    },
                ],
            },
            "options": {
                "showHeader": True,
                "cellHeight": "sm",
                "footer": {"show": False},
                "sortBy": [{"displayName": "Power (W)", "desc": True}],
            },
        }
    )

    # ==========================================================================
    # Debug Section (Row Header + Panels)
    # ==========================================================================

    # Debug Section Row Header
    panels.append(
        {
            "id": 39,
            "gridPos": {"h": 1, "w": 24, "x": 0, "y": 119},
            "type": "row",
            "title": "Debug",
            "collapsed": False,
        }
    )

    # API Calls by Integration
    panels.append(
        {
            "id": 40,
            "gridPos": {"h": 8, "w": 12, "x": 0, "y": 120},
            "type": "timeseries",
            "title": "API Calls by Integration",
            "description": "API calls per integration from fetch run summaries. Limited by retained run history (last 500 runs; dashboard time range applies).",
            "targets": [
                {
                    "datasource": DATASOURCE,
                    "editorMode": "code",
                    "format": "time_series",
                    "rawQuery": True,
                    "rawSql": SQL_API_CALLS_BY_INTEGRATION,
                    "refId": "A",
                }
            ],
            "fieldConfig": {
                "defaults": {
                    "color": {"mode": "palette-classic"},
                    "custom": {
                        "axisCenteredZero": False,
                        "axisColorMode": "text",
                        "axisLabel": "",
                        "axisPlacement": "auto",
                        "drawStyle": "bars",
                        "fillOpacity": 80,
                        "lineInterpolation": "linear",
                        "lineWidth": 1,
                        "pointSize": 5,
                        "showPoints": "never",
                        "spanNulls": False,
                        "stacking": {"group": "A", "mode": "normal"},
                    },
                    "mappings": [],
                    "min": 0,
                    "thresholds": {
                        "mode": "absolute",
                        "steps": [{"color": GREEN, "value": None}],
                    },
                    "unit": "none",
                    "decimals": 0,
                },
                "overrides": [],
            },
            "options": {
                "tooltip": {"mode": "multi", "sort": "desc"},
                "legend": {
                    "displayMode": "table",
                    "placement": "right",
                    "showLegend": True,
                    "calcs": ["sum", "lastNotNull"],
                },
            },
        }
    )

    # Water Gallons Cumulative (Debug) - total over selected time range
    panels.append(
        {
            "id": 41,
            "gridPos": {"h": 8, "w": 12, "x": 12, "y": 120},
            "type": "timeseries",
            "title": "Water Gallons (Cumulative)",
            "description": "Cumulative water usage over the selected time range. Uses daily readings. Legend 'Last' = total gallons for the range.",
            "targets": [
                {
                    "datasource": DATASOURCE,
                    "editorMode": "code",
                    "format": "time_series",
                    "rawQuery": True,
                    "rawSql": SQL_WATER_GALLONS_CUMULATIVE,
                    "refId": "A",
                }
            ],
            "fieldConfig": {
                "defaults": {
                    "color": {"mode": "palette-classic"},
                    "custom": {
                        "axisCenteredZero": False,
                        "axisColorMode": "text",
                        "axisLabel": "",
                        "axisPlacement": "auto",
                        "drawStyle": "line",
                        "fillOpacity": 20,
                        "lineInterpolation": "linear",
                        "lineWidth": 2,
                        "pointSize": 5,
                        "showPoints": "auto",
                        "spanNulls": False,
                    },
                    "mappings": [],
                    "min": 0,
                    "thresholds": {
                        "mode": "absolute",
                        "steps": [{"color": BLUE, "value": None}],
                    },
                    "unit": "gal",
                    "decimals": 1,
                },
                "overrides": [],
            },
            "options": {
                "tooltip": {"mode": "single", "sort": "none"},
                "legend": {
                    "displayMode": "table",
                    "placement": "right",
                    "showLegend": True,
                    "calcs": ["lastNotNull", "max", "mean"],
                },
            },
        }
    )

    return panels


# =============================================================================
# DASHBOARD CREATION
# =============================================================================


def create_dashboard() -> dict[str, Any]:
    """Create the complete Grafana dashboard."""
    anno_cfg = load_dashboard_annotations_config()
    annotations = anno_cfg["annotations"]
    all_day_tz = anno_cfg.get("all_day_timezone") or DEFAULT_ANNOTATIONS_ALL_DAY_TZ
    codified_sql = build_sql_codified_annotations(annotations, all_day_timezone=all_day_tz)

    return {
        "uid": DASHBOARD_UID,
        "title": DASHBOARD_TITLE,
        "description": (
            "Optional purple Annotations load from annotations.json (gitignored) or "
            "ANNOTATIONS_CONFIG_PATH; see annotations.example.json in the repo. "
            "Entries with locations[] only show when those sites are selected in Site. "
            "Zoom the time picker to include your event dates."
        ),
        "tags": [
            "power",
            "solar",
            "battery",
            "production",
            "consumption",
            "water",
            "irrigation",
            "propane",
            "pool",
            "span",
            "system",
        ],
        "style": "dark",
        "timezone": "browser",
        "schemaVersion": 38,
        "version": 1,
        "refresh": "30s",
        "time": {"from": "now-24h", "to": "now"},
        "timepicker": {
            "refresh_intervals": [
                "5s",
                "10s",
                "30s",
                "1m",
                "5m",
                "15m",
                "30m",
                "1h",
                "2h",
                "1d",
            ],
            "time_options": ["5m", "15m", "1h", "6h", "12h", "24h", "2d", "7d", "30d"],
        },
        "templating": {
            "list": [
                {
                    "name": "location",
                    "type": "query",
                    "label": "Site",
                    "datasource": DATASOURCE,
                    "definition": "SELECT name FROM locations ORDER BY name",
                    "query": "SELECT name FROM locations ORDER BY name",
                    "current": {"selected": False, "text": "All", "value": "$__all"},
                    "options": [],
                    "includeAll": True,
                    "multi": True,
                    "allValue": None,
                },
                {
                    "name": "source",
                    "type": "query",
                    "label": "Data Source",
                    "datasource": DATASOURCE,
                    "definition": "SELECT DISTINCT source FROM power_readings ORDER BY source",
                    "query": "SELECT DISTINCT source FROM power_readings ORDER BY source",
                    "current": {"selected": False, "text": "All", "value": "$__all"},
                    "options": [],
                    "includeAll": True,
                    "multi": True,
                    "allValue": None,
                },
            ]
        },
        "panels": create_panels(all_day_timezone=all_day_tz),
        "annotations": {
            "list": [
                {
                    "builtIn": 1,
                    "datasource": {"type": "grafana", "uid": "-- Grafana --"},
                    "enable": True,
                    "hide": True,
                    "iconColor": "rgba(0, 211, 255, 1)",
                    "name": "Annotations & Alerts",
                    "type": "dashboard",
                },
                postgres_annotation_layer(
                    "Annotations",
                    codified_sql,
                    "purple",
                ),
                postgres_annotation_layer(
                    "Sprinkler Runs",
                    SQL_SPRINKLER_RUNS_ANNOTATIONS,
                    "green",
                ),
            ]
        },
        "editable": True,
        "fiscalYearStartMonth": 0,
        "graphTooltip": 1,
        "links": [],
        "liveNow": False,
        "weekStart": "",
    }


def main():
    """Generate and write the dashboard JSON file."""
    dashboard = create_dashboard()
    output_path = "grafana/provisioning/dashboards/home-monitor.json"

    with open(output_path, "w") as f:
        json.dump(dashboard, f, indent=2)

    print(f"Dashboard generated successfully: {output_path}")


if __name__ == "__main__":
    main()
