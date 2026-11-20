-- DESTRUCTIVE: delete duplicate propane_readings rows, keeping the earliest-inserted row
-- (lowest id) for each (location_id, device_id, timestamp).
-- Run only via `make db-dedupe-propane-remote CONFIRM=yes` after `make db-backup-remote`.
\set ON_ERROR_STOP on
BEGIN;
DELETE FROM propane_readings p
USING (
    SELECT id,
           ROW_NUMBER() OVER (
               PARTITION BY location_id, device_id, timestamp ORDER BY id
           ) AS rn
    FROM propane_readings
) d
WHERE p.id = d.id
  AND d.rn > 1;
COMMIT;
VACUUM (ANALYZE) propane_readings;
