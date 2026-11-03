-- Run in the main project's metadata location. Replace MAIN_PROJECT in both paths.
-- Replace source project, service name, region, URLs, and the data-team profile ID.
-- This counts matching log entries, not unique requests or failed executions.
-- FALSE keeps the rule disabled. Sequential reruns skip an existing rule ID.
DECLARE new_rule_id STRING DEFAULT 'orders-ingestion-errors';

INSERT INTO `MAIN_PROJECT.monitoring.monitoring_rules` (
  rule_id, rule_name, rule_version, project_id, service_type,
  resource_name, resource_id, resource_url, location, check_type,
  log_filter, comparison_operator, threshold_value, window_minutes,
  severity, no_data_policy, target_profiles, pipeline_id, owner, is_active
)
SELECT
  new_rule_id,
  'Orders ingestion error log entries',
  1,
  'your-source-project',
  'CLOUD_RUN',
  'orders-ingestion',
  'projects/your-source-project/locations/europe-west1/services/orders-ingestion',
  'https://console.cloud.google.com/run/detail/europe-west1/orders-ingestion/logs?project=your-source-project',
  'europe-west1',
  'LOG',
  '''resource.type="cloud_run_revision"
     AND resource.labels.service_name="orders-ingestion"
     AND resource.labels.location="europe-west1"
     AND severity>=ERROR''',
  'GT',
  0,
  1440,
  'CRITICAL',
  'NO_DATA',
  ['data-team'],
  'orders-pipeline',
  'Data Platform',
  FALSE
WHERE NOT EXISTS (
  SELECT 1 FROM `MAIN_PROJECT.monitoring.monitoring_rules` AS existing
  WHERE existing.rule_id = new_rule_id
);
