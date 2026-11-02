output "service_url" {
  value = google_cloud_run_v2_service.monitor.uri
}

output "runtime_service_account" {
  value = google_service_account.runtime.email
}

output "metadata_dataset" {
  value = "${var.project_id}.${google_bigquery_dataset.metadata.dataset_id}"
}

output "state_bucket" {
  value = google_storage_bucket.state.name
}

output "smtp_secret" {
  value = google_secret_manager_secret.smtp.secret_id
}
