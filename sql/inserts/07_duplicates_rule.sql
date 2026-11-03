-- Run in the main project's metadata location. Replace MAIN_PROJECT in both paths.
-- Replace source project/table, created_at, key_columns, and the data-team profile ID.
-- created_at must be TIMESTAMP; EU must match the source dataset location.
-- Alerts when the time window contains any excess rows for the order_id key.
-- An empty window returns zero; pair this with ROW_COUNT if absence is a problem.
-- FALSE keeps the rule disabled. Sequential reruns skip an existing rule ID.
DECLARE new_rule_id STRING DEFAULT 'orders-duplicate-ids';

INSERT INTO `MAIN_PROJECT.monitoring.monitoring_rules` (
  rule_id, rule_name, rule_version, project_id, service_type,
  resource_name, resource_url, location, check_type, check_config,
  comparison_operator, threshold_value, window_minutes,
  severity, no_data_policy, target_profiles, pipeline_id, owner, is_active
)
SELECT
  new_rule_id,
  'Duplicate order IDs',
  1,
  'your-source-project',
  'BIGQUERY',
  'warehouse.orders',
  'https://console.cloud.google.com/bigquery?project=your-source-project',
  'EU',
  'DUPLICATES',
  JSON '{"table":"your-source-project.warehouse.orders","timestamp_column":"created_at","key_columns":["order_id"]}',
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
