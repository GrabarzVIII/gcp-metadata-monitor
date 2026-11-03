-- Run this script in BigQuery after editing these four values.
-- The query processing location must match metadata_location.
-- Existing tables are preserved; this script is not a destructive migration.
DECLARE metadata_project STRING DEFAULT 'your-monitoring-project';
DECLARE metadata_dataset STRING DEFAULT 'monitoring';
DECLARE metadata_location STRING DEFAULT 'EU';
DECLARE history_retention_days INT64 DEFAULT 90;

ASSERT REGEXP_CONTAINS(metadata_project, r'^[a-z][a-z0-9-]{4,61}[a-z0-9]$')
  AS 'Invalid metadata project ID';
ASSERT REGEXP_CONTAINS(metadata_dataset, r'^[A-Za-z_][A-Za-z0-9_]*$')
  AS 'Invalid metadata dataset ID';
ASSERT REGEXP_CONTAINS(metadata_location, r'^[A-Za-z0-9-]+$')
  AS 'Invalid BigQuery location';
ASSERT history_retention_days >= 7 AS 'Keep at least seven days of history';

EXECUTE IMMEDIATE FORMAT(
  "CREATE SCHEMA IF NOT EXISTS `%s.%s` OPTIONS(location='%s')",
  metadata_project, metadata_dataset, metadata_location
);

EXECUTE IMMEDIATE FORMAT("""
CREATE TABLE IF NOT EXISTS `%s.%s.notification_profiles` (
  profile_id STRING NOT NULL,
  email_addresses ARRAY<STRING>,
  only_problems BOOL DEFAULT FALSE,
  is_active BOOL DEFAULT FALSE
)
""", metadata_project, metadata_dataset);

EXECUTE IMMEDIATE FORMAT("""
CREATE TABLE IF NOT EXISTS `%s.%s.monitoring_rules` (
  rule_id STRING NOT NULL,
  rule_name STRING NOT NULL,
  rule_version INT64 DEFAULT 1,
  project_id STRING NOT NULL,
  service_type STRING NOT NULL,
  resource_name STRING NOT NULL,
  resource_id STRING,
  resource_url STRING,
  location STRING DEFAULT 'EU',
  check_type STRING NOT NULL,
  log_filter STRING,
  kpi_query STRING,
  check_config JSON,
  comparison_operator STRING NOT NULL,
  threshold_value FLOAT64 NOT NULL,
  threshold_upper FLOAT64,
  window_minutes INT64 DEFAULT 1440,
  severity STRING DEFAULT 'WARNING',
  no_data_policy STRING DEFAULT 'NO_DATA',
  target_profiles ARRAY<STRING>,
  pipeline_id STRING,
  owner STRING,
  runbook_url STRING,
  is_active BOOL DEFAULT FALSE
)
""", metadata_project, metadata_dataset);

EXECUTE IMMEDIATE FORMAT("""
CREATE TABLE IF NOT EXISTS `%s.%s.monitoring_results` (
  run_id STRING,
  rule_id STRING,
  rule_name STRING,
  rule_version INT64,
  project_id STRING,
  service_type STRING,
  resource_name STRING,
  resource_url STRING,
  check_type STRING,
  status STRING,
  value FLOAT64,
  comparison_operator STRING,
  threshold_value FLOAT64,
  threshold_upper FLOAT64,
  severity STRING,
  window_start TIMESTAMP,
  window_end TIMESTAMP,
  checked_at TIMESTAMP,
  duration_ms INT64,
  message STRING,
  target_profiles ARRAY<STRING>,
  owner STRING,
  pipeline_id STRING,
  runbook_url STRING,
  rule_snapshot STRING
)
PARTITION BY DATE(checked_at)
CLUSTER BY project_id, rule_id, status
OPTIONS(partition_expiration_days=%d)
""", metadata_project, metadata_dataset, history_retention_days);

EXECUTE IMMEDIATE FORMAT("""
CREATE TABLE IF NOT EXISTS `%s.%s.monitoring_runs` (
  run_id STRING,
  started_at TIMESTAMP,
  window_end TIMESTAMP,
  finished_at TIMESTAMP,
  status STRING,
  rule_count INT64,
  problem_count INT64,
  configuration_errors ARRAY<STRING>,
  notifications STRING,
  preview BOOL
)
PARTITION BY DATE(started_at)
CLUSTER BY status
OPTIONS(partition_expiration_days=%d)
""", metadata_project, metadata_dataset, history_retention_days);
