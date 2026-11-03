-- Run in the main project's metadata location. Replace MAIN_PROJECT in both paths.
-- Replace source project/table, customer_id, created_at, and the data-team profile ID.
-- created_at must be TIMESTAMP; EU must match the source dataset location.
-- Alerts when more than 5% of window records have no customer ID.
-- The query parameters below are stored as text and supplied later by the function.
-- FALSE keeps the rule disabled. Sequential reruns skip an existing rule ID.
DECLARE new_rule_id STRING DEFAULT 'orders-missing-customer-kpi';

INSERT INTO `MAIN_PROJECT.monitoring.monitoring_rules` (
  rule_id, rule_name, rule_version, project_id, service_type,
  resource_name, resource_url, location, check_type, kpi_query,
  comparison_operator, threshold_value, window_minutes,
  severity, no_data_policy, target_profiles, pipeline_id, owner, is_active
)
SELECT
  new_rule_id,
  'Share of orders without a customer',
  1,
  'your-source-project',
  'BIGQUERY',
  'warehouse.orders',
  'https://console.cloud.google.com/bigquery?project=your-source-project',
  'EU',
  'SQL_KPI',
  '''SELECT SAFE_DIVIDE(COUNTIF(customer_id IS NULL), COUNT(*)) AS value
     FROM `your-source-project.warehouse.orders`
     WHERE created_at >= @window_start AND created_at < @window_end''',
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
