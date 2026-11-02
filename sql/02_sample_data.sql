-- Replace PROJECT_ID, DATASET_ID, SOURCE_PROJECT, SOURCE_DATASET, and the email address.
-- Samples are disabled. Review each filter and threshold before enabling a rule/profile.
INSERT INTO `PROJECT_ID.DATASET_ID.notification_profiles`
  (profile_id, email_addresses, only_problems, is_active)
VALUES ('data-team', ['team@example.com'], FALSE, FALSE);

INSERT INTO `PROJECT_ID.DATASET_ID.monitoring_rules`
  (rule_id, rule_name, project_id, service_type, resource_name, location,
   check_type, check_config, comparison_operator, threshold_value, window_minutes,
   no_data_policy, target_profiles, owner, pipeline_id, is_active)
VALUES
  ('orders-freshness', 'Orders freshness in minutes', 'SOURCE_PROJECT', 'BIGQUERY',
   'SOURCE_DATASET.orders', 'EU', 'FRESHNESS',
   JSON '{"table":"SOURCE_PROJECT.SOURCE_DATASET.orders","timestamp_column":"loaded_at"}',
   'GT', 120, 1440, 'ALERT', ['data-team'], 'Data Platform', 'orders-pipeline', FALSE),
  ('orders-count', 'Orders received in the last 24 hours', 'SOURCE_PROJECT', 'BIGQUERY',
   'SOURCE_DATASET.orders', 'EU', 'ROW_COUNT',
   JSON '{"table":"SOURCE_PROJECT.SOURCE_DATASET.orders","timestamp_column":"created_at"}',
   'LT', 100, 1440, 'NO_DATA', ['data-team'], 'Data Platform', 'orders-pipeline', FALSE);

INSERT INTO `PROJECT_ID.DATASET_ID.monitoring_rules`
  (rule_id, rule_name, project_id, service_type, resource_name, resource_url,
   check_type, log_filter, comparison_operator, threshold_value, target_profiles, is_active)
VALUES
  ('ingestion-errors', 'Ingestion error log entries', 'SOURCE_PROJECT', 'CLOUD_RUN',
   'orders-ingestion',
   'https://console.cloud.google.com/run?project=SOURCE_PROJECT',
   'LOG',
   'resource.type="cloud_run_revision" AND resource.labels.service_name="orders-ingestion" AND severity>=ERROR',
   'GT', 0, ['data-team'], FALSE);

INSERT INTO `PROJECT_ID.DATASET_ID.monitoring_rules`
  (rule_id, rule_name, project_id, service_type, resource_name, location,
   check_type, kpi_query, comparison_operator, threshold_value, target_profiles, is_active)
VALUES
  ('orders-null-rate', 'Share of orders without a customer', 'SOURCE_PROJECT', 'BIGQUERY',
   'SOURCE_DATASET.orders', 'EU', 'SQL_KPI',
   '''SELECT SAFE_DIVIDE(COUNTIF(customer_id IS NULL), COUNT(*)) AS value
      FROM `SOURCE_PROJECT.SOURCE_DATASET.orders`
      WHERE created_at >= @window_start AND created_at < @window_end''',
   'GT', 0.05, ['data-team'], FALSE);
