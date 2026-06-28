"""Parameterized SQL queries for report aggregation."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from home_monitor.database import get_connection
from home_monitor.metrics.power import sql_power_energy_kwh


def _fetch_scalar(sql: str, params: dict) -> float:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            if not row or row[0] is None:
                return 0.0
            return float(row[0])


def _fetch_rows(sql: str, params: dict) -> List[Dict[str, Any]]:
    from psycopg2.extras import RealDictCursor

    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]


def fetch_power_energy_totals(
    location_id: int,
    start_utc: datetime,
    end_utc: datetime,
) -> Dict[str, float]:
    params = {"location_id": location_id, "start_utc": start_utc, "end_utc": end_utc}
    return {
        "production_kwh": _fetch_scalar(
            sql_power_energy_kwh(location_id, start_utc, end_utc, "power_produced"), params
        ),
        "consumption_kwh": _fetch_scalar(
            sql_power_energy_kwh(location_id, start_utc, end_utc, "power_consumed"), params
        ),
        "import_kwh": _fetch_scalar(
            sql_power_energy_kwh(location_id, start_utc, end_utc, "power_imported"), params
        ),
        "export_kwh": _fetch_scalar(
            sql_power_energy_kwh(location_id, start_utc, end_utc, "power_exported"), params
        ),
    }


def fetch_power_peaks(
    location_id: int,
    start_utc: datetime,
    end_utc: datetime,
) -> Dict[str, float]:
    sql = """
    WITH minute_power AS (
      SELECT
        date_trunc('minute', timestamp) AS ts_min,
        SUM(COALESCE(power_produced, 0)) / 1000.0 AS production_kw,
        SUM(COALESCE(power_consumed, 0)) / 1000.0 AS consumption_kw
      FROM power_readings
      WHERE location_id = %(location_id)s
        AND timestamp >= %(start_utc)s
        AND timestamp < %(end_utc)s
      GROUP BY date_trunc('minute', timestamp)
    )
    SELECT
      COALESCE(MAX(production_kw), 0) AS max_production_kw,
      COALESCE(MAX(consumption_kw), 0) AS max_consumption_kw
    FROM minute_power
    """
    params = {"location_id": location_id, "start_utc": start_utc, "end_utc": end_utc}
    rows = _fetch_rows(sql, params)
    if not rows:
        return {"max_production_kw": 0.0, "max_consumption_kw": 0.0}
    return {
        "max_production_kw": float(rows[0]["max_production_kw"]),
        "max_consumption_kw": float(rows[0]["max_consumption_kw"]),
    }


def fetch_trailing_daily_averages(
    location_id: int,
    start_utc: datetime,
    end_utc: datetime,
    tz_name: str,
) -> Dict[str, float]:
    """Average per-day energy totals across the trailing window (days with data only)."""
    breakdown = fetch_daily_breakdown(location_id, start_utc, end_utc, tz_name)
    keys = ("production_kwh", "consumption_kwh", "import_kwh", "export_kwh")
    totals = {k: 0.0 for k in keys}
    days_with_data = 0
    for day in breakdown:
        has_data = (day.get("production_kwh") or 0) > 0 or (day.get("consumption_kwh") or 0) > 0
        if not has_data:
            continue
        days_with_data += 1
        for k in keys:
            totals[k] += day.get(k) or 0.0

    if days_with_data == 0:
        return {f"avg_{k}": 0.0 for k in keys} | {"days": 0}

    averages = {f"avg_{k}": totals[k] / days_with_data for k in keys}
    averages["days"] = days_with_data
    return averages


def fetch_battery_metrics(
    location_id: int,
    start_utc: datetime,
    end_utc: datetime,
) -> Optional[Dict[str, float]]:
    """
    Aggregate battery state of charge across all Powerwalls / Tesla energy sites.

    Each Tesla ``energy_site`` reports a single aggregate state of charge (only the
    index-0 bank receives readings), and a location can have multiple energy sites
    (e.g. FL) whose readings are not timestamp-aligned. We therefore:

    1. Compute each energy site's nameplate capacity (sum of its banks' capacity).
    2. Bucket readings into 15-minute bins and average each site's SOC per bin.
    3. Combine sites within a bin as a capacity-weighted SOC, only using bins where
       every site reported, so the combined series is consistent over time.

    Stored energy is derived from ``SOC x nameplate capacity`` because the Tesla
    live-status payload does not include absolute stored energy (``energy_charged``
    / ``energy_discharged`` are not populated). When capacities are unknown the
    weighting collapses to a simple average and stored-energy figures are omitted.
    """
    sql = """
    WITH site_caps AS (
      SELECT energy_site_id, SUM(capacity_kwh) AS cap
      FROM battery_banks
      WHERE location_id = %(location_id)s
      GROUP BY energy_site_id
    ),
    weights AS (
      SELECT energy_site_id,
             cap,
             COALESCE(NULLIF(cap, 0), 1.0) AS weight
      FROM site_caps
    ),
    n_sites AS (SELECT COUNT(*) AS n FROM weights),
    binned AS (
      SELECT bb.energy_site_id,
             date_bin('15 minutes', br.timestamp, TIMESTAMP '2000-01-01') AS bucket,
             AVG(br.state_of_charge) AS soc
      FROM battery_readings br
      JOIN battery_banks bb ON bb.id = br.battery_bank_id
      WHERE br.location_id = %(location_id)s
        AND br.timestamp >= %(start_utc)s
        AND br.timestamp < %(end_utc)s
        AND br.state_of_charge IS NOT NULL
      GROUP BY bb.energy_site_id, bucket
    ),
    combined AS (
      SELECT b.bucket,
             SUM(b.soc / 100.0 * w.weight) AS stored,
             SUM(w.weight) AS weight,
             SUM(b.soc / 100.0 * w.cap) AS stored_kwh,
             COUNT(DISTINCT b.energy_site_id) AS sites_present
      FROM binned b
      JOIN weights w ON w.energy_site_id = b.energy_site_id
      GROUP BY b.bucket
    ),
    aligned AS (
      SELECT * FROM combined WHERE sites_present = (SELECT n FROM n_sites)
    )
    SELECT
      (SELECT stored / weight * 100 FROM aligned ORDER BY bucket DESC LIMIT 1) AS end_soc,
      (SELECT stored / weight * 100 FROM aligned ORDER BY bucket ASC LIMIT 1) AS start_soc,
      MIN(stored / weight * 100) AS min_soc,
      MAX(stored / weight * 100) AS max_soc,
      (SELECT stored_kwh FROM aligned ORDER BY bucket DESC LIMIT 1)
        - (SELECT stored_kwh FROM aligned ORDER BY bucket ASC LIMIT 1) AS net_stored_kwh,
      (SELECT SUM(cap) FROM site_caps) AS total_capacity_kwh
    FROM aligned
    """
    params = {"location_id": location_id, "start_utc": start_utc, "end_utc": end_utc}
    rows = _fetch_rows(sql, params)
    if not rows or rows[0]["end_soc"] is None:
        return None

    row = rows[0]
    end_soc = float(row["end_soc"])
    total_capacity = row["total_capacity_kwh"]
    has_capacity = total_capacity is not None and float(total_capacity) > 0

    # Per-site end SOC so the combined card can show a breakdown (e.g. "83% / 20%")
    # when a location spans multiple Tesla energy sites (e.g. FL). Ordered by
    # energy_site_id for stable left-to-right ordering across reports.
    site_sql = """
    SELECT DISTINCT ON (bb.energy_site_id)
      bb.energy_site_id,
      br.state_of_charge AS end_soc
    FROM battery_readings br
    JOIN battery_banks bb ON bb.id = br.battery_bank_id
    WHERE br.location_id = %(location_id)s
      AND br.timestamp >= %(start_utc)s
      AND br.timestamp < %(end_utc)s
      AND br.state_of_charge IS NOT NULL
    ORDER BY bb.energy_site_id, br.timestamp DESC
    """
    site_rows = _fetch_rows(site_sql, params)
    site_socs = [float(r["end_soc"]) for r in site_rows if r["end_soc"] is not None]

    return {
        "end_soc_pct": end_soc,
        "start_soc_pct": float(row["start_soc"]) if row["start_soc"] is not None else end_soc,
        "min_soc_pct": float(row["min_soc"]) if row["min_soc"] is not None else end_soc,
        "max_soc_pct": float(row["max_soc"]) if row["max_soc"] is not None else end_soc,
        "net_stored_kwh": (
            float(row["net_stored_kwh"])
            if has_capacity and row["net_stored_kwh"] is not None
            else None
        ),
        "total_capacity_kwh": float(total_capacity) if has_capacity else None,
        "site_socs": site_socs,
    }


def fetch_daily_breakdown(
    location_id: int,
    start_utc: datetime,
    end_utc: datetime,
    tz_name: str,
) -> List[Dict[str, Any]]:
    """Daily energy totals for charting (weekly+ reports)."""
    tz = ZoneInfo(tz_name)
    start_local = start_utc.astimezone(tz).date()
    end_local = end_utc.astimezone(tz).date()
    days: List[Dict[str, Any]] = []
    current = start_local
    while current < end_local:
        day_start = datetime(current.year, current.month, current.day, tzinfo=tz).astimezone(
            ZoneInfo("UTC")
        )
        next_day = current + timedelta(days=1)
        day_end = datetime(next_day.year, next_day.month, next_day.day, tzinfo=tz).astimezone(
            ZoneInfo("UTC")
        )
        totals = fetch_power_energy_totals(location_id, day_start, day_end)
        days.append(
            {
                "date": current.isoformat(),
                **totals,
            }
        )
        current = next_day
    return days


def fetch_hourly_power_profile(
    location_id: int,
    start_utc: datetime,
    end_utc: datetime,
    tz_name: str,
) -> List[Dict[str, Any]]:
    """Average production/consumption kW by hour-of-day (local timezone) for daily charts."""
    sql = """
    SELECT
      EXTRACT(HOUR FROM timestamp AT TIME ZONE %(tz)s)::int AS hour,
      AVG(COALESCE(power_produced, 0)) / 1000.0 AS production_kw,
      AVG(COALESCE(power_consumed, 0)) / 1000.0 AS consumption_kw
    FROM power_readings
    WHERE location_id = %(location_id)s
      AND timestamp >= %(start_utc)s
      AND timestamp < %(end_utc)s
    GROUP BY EXTRACT(HOUR FROM timestamp AT TIME ZONE %(tz)s)
    ORDER BY hour
    """
    params = {
        "location_id": location_id,
        "start_utc": start_utc,
        "end_utc": end_utc,
        "tz": tz_name,
    }
    rows = _fetch_rows(sql, params)
    by_hour = {int(r["hour"]): r for r in rows}

    def hour_label(h: int) -> str:
        if h == 0:
            return "12a"
        if h < 12:
            return f"{h}a"
        if h == 12:
            return "12p"
        return f"{h - 12}p"

    profile = []
    for hour in range(24):
        row = by_hour.get(hour)
        profile.append(
            {
                "hour": hour,
                "hour_label": hour_label(hour),
                "production_kw": float(row["production_kw"]) if row else 0.0,
                "consumption_kw": float(row["consumption_kw"]) if row else 0.0,
            }
        )
    return profile


def fetch_water_metrics(
    location_id: int,
    start_utc: datetime,
    end_utc: datetime,
) -> Optional[Dict[str, Any]]:
    sql = """
    WITH latest_daily AS (
      SELECT DISTINCT ON (DATE(timestamp))
        DATE(timestamp) AS day,
        usage_gallons
      FROM water_readings
      WHERE location_id = %(location_id)s
        AND usage_period = 'day'
        AND timestamp >= %(start_utc)s
        AND timestamp < %(end_utc)s
        AND usage_gallons IS NOT NULL
      ORDER BY DATE(timestamp), timestamp DESC
    )
    SELECT
      COALESCE(SUM(usage_gallons), 0) AS total_gallons,
      COALESCE(AVG(usage_gallons), 0) AS avg_daily_gallons,
      COUNT(*) AS days_with_data
    FROM latest_daily
    """
    params = {"location_id": location_id, "start_utc": start_utc, "end_utc": end_utc}
    rows = _fetch_rows(sql, params)
    if not rows or rows[0]["days_with_data"] == 0:
        return None

    sprinkler_sql = """
    SELECT COUNT(DISTINCT DATE(start_time)) AS sprinkler_days
    FROM sprinkler_runs
    WHERE location_id = %(location_id)s
      AND start_time >= %(start_utc)s
      AND start_time < %(end_utc)s
    """
    spr_rows = _fetch_rows(sprinkler_sql, params)
    sprinkler_days = int(spr_rows[0]["sprinkler_days"]) if spr_rows else 0

    row = rows[0]
    return {
        "total_gallons": float(row["total_gallons"]),
        "avg_daily_gallons": float(row["avg_daily_gallons"]),
        "sprinkler_days": sprinkler_days,
    }


def fetch_propane_metrics(
    location_id: int,
    start_utc: datetime,
    end_utc: datetime,
) -> Optional[Dict[str, Any]]:
    sql = """
    SELECT
      (SELECT tank_level_percent FROM propane_readings
       WHERE location_id = %(location_id)s AND timestamp < %(start_utc)s
       ORDER BY timestamp DESC LIMIT 1) AS start_pct,
      (SELECT tank_level_percent FROM propane_readings
       WHERE location_id = %(location_id)s AND timestamp >= %(start_utc)s
         AND timestamp < %(end_utc)s
       ORDER BY timestamp ASC LIMIT 1) AS period_start_pct,
      (SELECT tank_level_percent FROM propane_readings
       WHERE location_id = %(location_id)s AND timestamp >= %(start_utc)s
         AND timestamp < %(end_utc)s
       ORDER BY timestamp DESC LIMIT 1) AS end_pct,
      (SELECT tank_level_gallons FROM propane_readings
       WHERE location_id = %(location_id)s AND timestamp >= %(start_utc)s
         AND timestamp < %(end_utc)s
       ORDER BY timestamp DESC LIMIT 1) AS end_gallons
    """
    params = {"location_id": location_id, "start_utc": start_utc, "end_utc": end_utc}
    rows = _fetch_rows(sql, params)
    if not rows:
        return None
    row = rows[0]
    start_pct = row["period_start_pct"] or row["start_pct"]
    end_pct = row["end_pct"]
    if start_pct is None and end_pct is None:
        return None
    return {
        "start_pct": float(start_pct) if start_pct is not None else None,
        "end_pct": float(end_pct) if end_pct is not None else None,
        "end_gallons": float(row["end_gallons"]) if row["end_gallons"] is not None else None,
    }


def fetch_pool_metrics(
    location_id: int,
    start_utc: datetime,
    end_utc: datetime,
) -> Optional[Dict[str, Any]]:
    sql = """
    SELECT
      AVG(pool_temp) FILTER (WHERE pool_temp IS NOT NULL) AS avg_pool_temp_f,
      AVG(spa_temp) FILTER (WHERE spa_temp IS NOT NULL) AS avg_spa_temp_f,
      AVG(air_temp) FILTER (WHERE air_temp IS NOT NULL) AS avg_air_temp_f,
      COUNT(*) FILTER (WHERE pool_pump) AS pump_samples_on,
      COUNT(*) FILTER (WHERE pool_heater) AS heater_samples_on,
      COUNT(*) AS total_samples
    FROM pool_readings
    WHERE location_id = %(location_id)s
      AND timestamp >= %(start_utc)s
      AND timestamp < %(end_utc)s
    """
    params = {"location_id": location_id, "start_utc": start_utc, "end_utc": end_utc}
    rows = _fetch_rows(sql, params)
    if not rows or rows[0]["total_samples"] == 0:
        return None
    row = rows[0]
    interval_minutes = 5
    return {
        "avg_pool_temp_f": float(row["avg_pool_temp_f"]) if row["avg_pool_temp_f"] else None,
        "avg_spa_temp_f": float(row["avg_spa_temp_f"]) if row["avg_spa_temp_f"] else None,
        "avg_air_temp_f": float(row["avg_air_temp_f"]) if row["avg_air_temp_f"] else None,
        "pump_hours": round(int(row["pump_samples_on"] or 0) * interval_minutes / 60.0, 1),
        "heater_hours": round(int(row["heater_samples_on"] or 0) * interval_minutes / 60.0, 1),
    }


def fetch_pool_temp_air_profile(
    location_id: int,
    start_utc: datetime,
    end_utc: datetime,
    tz_name: str,
) -> List[Dict[str, Any]]:
    """Average pool and air temperature by hour-of-day (local tz) for daily pool charts."""
    sql = """
    SELECT
      EXTRACT(HOUR FROM timestamp AT TIME ZONE %(tz)s)::int AS hour,
      AVG(pool_temp) FILTER (WHERE pool_temp IS NOT NULL) AS pool_temp_f,
      AVG(air_temp) FILTER (WHERE air_temp IS NOT NULL) AS air_temp_f
    FROM pool_readings
    WHERE location_id = %(location_id)s
      AND timestamp >= %(start_utc)s
      AND timestamp < %(end_utc)s
    GROUP BY EXTRACT(HOUR FROM timestamp AT TIME ZONE %(tz)s)
    ORDER BY hour
    """
    params = {
        "location_id": location_id,
        "start_utc": start_utc,
        "end_utc": end_utc,
        "tz": tz_name,
    }
    rows = _fetch_rows(sql, params)
    if not rows:
        return []

    by_hour = {int(r["hour"]): r for r in rows}

    def hour_label(h: int) -> str:
        if h == 0:
            return "12a"
        if h < 12:
            return f"{h}a"
        if h == 12:
            return "12p"
        return f"{h - 12}p"

    profile = []
    for hour in range(24):
        row = by_hour.get(hour)
        profile.append(
            {
                "hour": hour,
                "hour_label": hour_label(hour),
                "pool_temp_f": float(row["pool_temp_f"]) if row and row["pool_temp_f"] else None,
                "air_temp_f": float(row["air_temp_f"]) if row and row["air_temp_f"] else None,
            }
        )
    return profile


def fetch_propane_usage_gallons(
    location_id: int,
    start_utc: datetime,
    end_utc: datetime,
) -> Optional[float]:
    """Gallons of propane consumed in a window (tank-level drop). None if no data.

    Returns 0.0 when the tank level rose (a refill) so refills do not read as usage.
    """
    sql = """
    SELECT
      (SELECT tank_level_gallons FROM propane_readings
       WHERE location_id = %(location_id)s
         AND timestamp >= %(start_utc)s AND timestamp < %(end_utc)s
         AND tank_level_gallons IS NOT NULL
       ORDER BY timestamp ASC LIMIT 1) AS start_gallons,
      (SELECT tank_level_gallons FROM propane_readings
       WHERE location_id = %(location_id)s
         AND timestamp >= %(start_utc)s AND timestamp < %(end_utc)s
         AND tank_level_gallons IS NOT NULL
       ORDER BY timestamp DESC LIMIT 1) AS end_gallons
    """
    params = {"location_id": location_id, "start_utc": start_utc, "end_utc": end_utc}
    rows = _fetch_rows(sql, params)
    if not rows or rows[0]["start_gallons"] is None or rows[0]["end_gallons"] is None:
        return None
    used = float(rows[0]["start_gallons"]) - float(rows[0]["end_gallons"])
    return max(used, 0.0)


def fetch_span_top_circuits(
    location_id: int,
    start_utc: datetime,
    end_utc: datetime,
    limit: int = 5,
) -> Optional[Dict[str, Any]]:
    sql = """
    WITH circuit_energy AS (
      SELECT
        circuit_name,
        MAX(import_energy_wh) - MIN(import_energy_wh) AS import_wh,
        MAX(export_energy_wh) - MIN(export_energy_wh) AS export_wh
      FROM span_circuit_readings
      WHERE location_id = %(location_id)s
        AND timestamp >= %(start_utc)s
        AND timestamp < %(end_utc)s
        AND circuit_name IS NOT NULL
      GROUP BY circuit_id, circuit_name
      HAVING COUNT(*) > 1
    )
    SELECT
      circuit_name,
      GREATEST(COALESCE(import_wh, 0), COALESCE(export_wh, 0)) / 1000.0 AS energy_kwh
    FROM circuit_energy
    ORDER BY energy_kwh DESC
    LIMIT %(limit)s
    """
    params = {
        "location_id": location_id,
        "start_utc": start_utc,
        "end_utc": end_utc,
        "limit": limit,
    }
    rows = _fetch_rows(sql, params)
    if not rows:
        return None
    circuits = [{"name": r["circuit_name"], "energy_kwh": float(r["energy_kwh"])} for r in rows]
    return {
        "top_circuits": circuits,
        "total_circuit_kwh": sum(c["energy_kwh"] for c in circuits),
    }


def fetch_tempest_metrics(
    location_id: int,
    start_utc: datetime,
    end_utc: datetime,
) -> Optional[Dict[str, Any]]:
    sql = """
    SELECT AVG(ghi_clear_sky) AS avg_ghi
    FROM irradiance_readings
    WHERE location_id = %(location_id)s
      AND source = 'tempest'
      AND timestamp >= %(start_utc)s
      AND timestamp < %(end_utc)s
      AND ghi_clear_sky IS NOT NULL
    """
    params = {"location_id": location_id, "start_utc": start_utc, "end_utc": end_utc}
    rows = _fetch_rows(sql, params)
    if not rows or rows[0]["avg_ghi"] is None:
        return None
    return {"avg_ghi": float(rows[0]["avg_ghi"])}
