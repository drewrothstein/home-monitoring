-- Read-only report of duplicate propane_readings rows.
-- A duplicate is a row sharing (location_id, device_id, timestamp) with an earlier row.
-- Used by `make db-dedupe-propane-remote` / `db-dedupe-propane-local` (dry run).
SELECT source,
       COUNT(*) AS total_rows,
       COUNT(DISTINCT (location_id, device_id, timestamp)) AS distinct_readings,
       COUNT(*) - COUNT(DISTINCT (location_id, device_id, timestamp)) AS duplicate_rows
FROM propane_readings
GROUP BY source
ORDER BY source;
