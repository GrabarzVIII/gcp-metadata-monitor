resource "google_service_account" "runtime" {
  account_id   = "gcp-monitor-runtime"
  display_name = "GCP Monitor runtime"
}

resource "google_service_account" "scheduler" {
  account_id   = "gcp-monitor-scheduler"
  display_name = "GCP Monitor scheduler invoker"
}

resource "google_project_iam_member" "query_jobs" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_bigquery_dataset_iam_member" "metadata_reader" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.metadata.dataset_id
  role       = "roles/bigquery.dataViewer"
  member     = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_bigquery_table_iam_member" "history_writer" {
  for_each   = toset(["monitoring_results", "monitoring_runs"])
  project    = var.project_id
  dataset_id = google_bigquery_dataset.metadata.dataset_id
  table_id   = google_bigquery_table.metadata[each.key].table_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_bigquery_dataset_iam_member" "source_reader" {
  for_each   = { for ds in var.source_datasets : "${ds.project_id}.${ds.dataset_id}" => ds }
  project    = each.value.project_id
  dataset_id = each.value.dataset_id
  role       = "roles/bigquery.dataViewer"
  member     = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_project_iam_member" "log_reader" {
  for_each = var.monitored_projects
  project  = each.value
  role     = "roles/logging.viewer"
  member   = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_storage_bucket_iam_member" "runtime" {
  bucket = google_storage_bucket.state.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_secret_manager_secret_iam_member" "runtime" {
  secret_id = google_secret_manager_secret.smtp.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_cloud_run_v2_service_iam_member" "scheduler" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.monitor.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler.email}"
}
