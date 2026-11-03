-- Run in the main project's metadata location, after create_monitoring_tables.sql.
-- Replace MAIN_PROJECT and the metadata dataset (monitoring) in both table paths.
-- Choose a new ID and real recipients. FALSE keeps the profile disabled until reviewed.
-- Sequential reruns skip an existing ID; this does not update an existing profile.
DECLARE new_profile_id STRING DEFAULT 'data-team';

INSERT INTO `MAIN_PROJECT.monitoring.notification_profiles` (
  profile_id, email_addresses, only_problems, is_active
)
SELECT
  new_profile_id,
  ['team@example.com', 'oncall@example.com'],
  FALSE, -- TRUE sends only ALERT, ERROR, or NO_DATA results.
  FALSE
WHERE NOT EXISTS (
  SELECT 1 FROM `MAIN_PROJECT.monitoring.notification_profiles` AS existing
  WHERE existing.profile_id = new_profile_id
);
