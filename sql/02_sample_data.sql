-- Edit these values, then run in the metadata dataset's BigQuery location.
-- Safe to rerun: existing profiles and rules are not overwritten or duplicated.
-- New profiles and rules are disabled until explicitly reviewed and activated.
DECLARE metadata_project STRING DEFAULT 'your-monitoring-project';
DECLARE metadata_dataset STRING DEFAULT 'monitoring';
DECLARE source_project STRING DEFAULT 'your-source-project';
DECLARE source_table STRING DEFAULT 'warehouse.orders';
DECLARE source_location STRING DEFAULT 'EU';
DECLARE recipient STRING DEFAULT 'team@example.com';
DECLARE run_service STRING DEFAULT 'orders-ingestion';

ASSERT REGEXP_CONTAINS(metadata_project, r'^[a-z][a-z0-9-]{4,61}[a-z0-9]$');
ASSERT REGEXP_CONTAINS(metadata_dataset, r'^[A-Za-z_][A-Za-z0-9_]*$');
ASSERT REGEXP_CONTAINS(source_project, r'^[a-z][a-z0-9-]{4,61}[a-z0-9]$');
ASSERT REGEXP_CONTAINS(source_table, r'^[A-Za-z0-9_]+\.[A-Za-z0-9_]+$');

EXECUTE IMMEDIATE FORMAT("""
MERGE `%s.%s.notification_profiles` AS target
USING (SELECT 'data-team' AS profile_id) AS sample
ON target.profile_id = sample.profile_id
WHEN NOT MATCHED THEN INSERT (profile_id, email_addresses, only_problems, is_active)
VALUES (sample.profile_id, [@recipient], FALSE, FALSE)
""", metadata_project, metadata_dataset) USING recipient AS recipient;

CREATE TEMP TABLE example_rules AS
SELECT 'orders-count' AS rule_id, 'Orders received in the last 24 hours' AS rule_name,
       'BIGQUERY' AS service_type, source_table AS resource_name,
       'ROW_COUNT' AS check_type,
       JSON_OBJECT('table', CONCAT(source_project, '.', source_table),
                   'timestamp_column', 'created_at') AS check_config,
       CAST(NULL AS STRING) AS kpi_query, CAST(NULL AS STRING) AS log_filter,
       'LT' AS comparison_operator, 100.0 AS threshold_value, 'NO_DATA' AS no_data_policy
UNION ALL
SELECT 'orders-freshness', 'Orders freshness in minutes', 'BIGQUERY', source_table,
       'FRESHNESS',
       JSON_OBJECT('table', CONCAT(source_project, '.', source_table),
                   'timestamp_column', 'loaded_at'),
       NULL, NULL, 'GT', 120.0, 'ALERT'
UNION ALL
SELECT 'orders-null-rate', 'Share of orders without a customer', 'BIGQUERY', source_table,
       'SQL_KPI', NULL,
       FORMAT('''SELECT SAFE_DIVIDE(COUNTIF(customer_id IS NULL), COUNT(*)) AS value
                 FROM `%s.%s`
                 WHERE created_at >= @window_start AND created_at < @window_end''',
              source_project, source_table),
       NULL, 'GT', 0.05, 'NO_DATA'
UNION ALL
SELECT 'ingestion-errors', 'Ingestion error log entries', 'CLOUD_RUN', run_service,
       'LOG', NULL, NULL,
       CONCAT('resource.type="cloud_run_revision" AND resource.labels.service_name=',
              TO_JSON_STRING(run_service), ' AND severity>=ERROR'),
       'GT', 0.0, 'NO_DATA';

EXECUTE IMMEDIATE FORMAT("""
MERGE `%s.%s.monitoring_rules` AS target
USING _SESSION.example_rules AS sample
ON target.rule_id = sample.rule_id
WHEN NOT MATCHED THEN INSERT (
  rule_id, rule_name, rule_version, project_id, service_type, resource_name, resource_url,
  location, check_type, check_config, kpi_query, log_filter, comparison_operator,
  threshold_value, window_minutes, no_data_policy, target_profiles, owner, pipeline_id, is_active
)
VALUES (
  sample.rule_id, sample.rule_name, 1, @source_project, sample.service_type,
  sample.resource_name,
  CONCAT('https://console.cloud.google.com/',
         IF(sample.service_type = 'BIGQUERY', 'bigquery', 'run'), '?project=', @source_project),
  @source_location, sample.check_type, sample.check_config, sample.kpi_query, sample.log_filter,
  sample.comparison_operator, sample.threshold_value, 1440, sample.no_data_policy,
  ['data-team'], 'Data Platform', 'orders-pipeline', FALSE
)
""", metadata_project, metadata_dataset)
USING source_project AS source_project, source_location AS source_location;
