-- Run as an administrator after creating the tables and runtime service account.
-- Replace HOST_PROJECT and DATASET_ID everywhere, including the account email.
-- These grants are additive and do not replace existing dataset access policies.
GRANT `roles/bigquery.dataViewer`
ON SCHEMA `HOST_PROJECT.DATASET_ID`
TO 'serviceAccount:gcp-monitor-runtime@HOST_PROJECT.iam.gserviceaccount.com';

GRANT `roles/bigquery.dataEditor`
ON TABLE `HOST_PROJECT.DATASET_ID.monitoring_results`
TO 'serviceAccount:gcp-monitor-runtime@HOST_PROJECT.iam.gserviceaccount.com';

GRANT `roles/bigquery.dataEditor`
ON TABLE `HOST_PROJECT.DATASET_ID.monitoring_runs`
TO 'serviceAccount:gcp-monitor-runtime@HOST_PROJECT.iam.gserviceaccount.com';

-- Run separately for every source dataset, in that dataset's location.
-- Replace SOURCE_PROJECT and SOURCE_DATASET; keep HOST_PROJECT as the hosting project.
GRANT `roles/bigquery.dataViewer`
ON SCHEMA `SOURCE_PROJECT.SOURCE_DATASET`
TO 'serviceAccount:gcp-monitor-runtime@HOST_PROJECT.iam.gserviceaccount.com';
