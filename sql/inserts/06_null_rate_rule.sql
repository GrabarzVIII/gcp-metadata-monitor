-- Run in the main project's metadata location. Replace MAIN_PROJECT in both paths.
-- Replace source project/table, created_at, customer_id, and the data-team profile ID.
-- created_at must be TIMESTAMP; EU must match the source dataset location.
-- Alerts above a 5% NULL rate; an empty window also alerts via no_data_policy.
-- FALSE keeps the rule disabled. Sequential reruns skip an existing rule ID.
DECLARE new_rule_id STRING DEFAULT 'orders-customer-null-rate';

INSERT INTO `MAIN_PROJECT.monitoring.monitoring_rules` (
  rule_id, rule_name, rule_version, project_id, service_type,
  resource_name, resource_url, location, check_type, check_config,
  comparison_operator, threshold_value, window_minutes,
  severity, no_data_policy, target_profiles, pipeline_id, owner, is_active
)
SELECT
  new_rule_id,
  'Missing customer IDs in orders',
  1,
  'your-source-project',
  'BIGQUERY',
  'warehouse.orders',
  'https://console.cloud.google.com/bigquery?project=your-source-project',
  'EU',
  'NULL_RATE',
  JSON '{"table":"your-source-project.warehouse.orders","timestamp_column":"created_at","column":"customer_id"}',
  'GT',
  0.05,
  1440,
  'WARNING',
  'ALERT',
  ['data-team'],
  'orders-pipeline',
  'Data Platform',
  FALSE
WHERE NOT EXISTS (
  SELECT 1 FROM `MAIN_PROJECT.monitoring.monitoring_rules` AS existing
  WHERE existing.rule_id = new_rule_id
);
