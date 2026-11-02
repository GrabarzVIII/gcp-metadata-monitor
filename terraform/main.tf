terraform {
  required_version = ">= 1.6, < 2.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

resource "google_project_service" "api" {
  for_each = toset([
    "run.googleapis.com", "cloudscheduler.googleapis.com", "bigquery.googleapis.com",
    "secretmanager.googleapis.com", "monitoring.googleapis.com"
  ])
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_bigquery_dataset" "metadata" {
  project                    = var.project_id
  dataset_id                 = var.dataset_id
  location                   = var.bq_location
  delete_contents_on_destroy = false
  depends_on                 = [google_project_service.api]
}

locals {
  tables = {
    monitoring_rules      = null
    notification_profiles = null
    monitoring_results    = "checked_at"
    monitoring_runs       = "started_at"
  }
}

resource "google_bigquery_table" "metadata" {
  for_each            = local.tables
  project             = var.project_id
  dataset_id          = google_bigquery_dataset.metadata.dataset_id
  table_id            = each.key
  schema              = file("${path.module}/../sql/schemas/${each.key}.json")
  deletion_protection = true
  clustering = each.key == "monitoring_results" ? ["project_id", "rule_id", "status"] : (
    each.key == "monitoring_runs" ? ["status"] : null
  )
  dynamic "time_partitioning" {
    for_each = each.value == null ? [] : [each.value]
    content {
      type          = "DAY"
      field         = time_partitioning.value
      expiration_ms = var.history_retention_days * 86400000
    }
  }
}

resource "google_storage_bucket" "state" {
  name                        = "${var.project_id}-monitor-state"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  lifecycle_rule {
    condition { age = 90 }
    action { type = "Delete" }
  }
}

resource "google_secret_manager_secret" "smtp" {
  secret_id = "gcp-monitor-smtp-password"
  replication {
    auto {}
  }
  depends_on = [google_project_service.api]
}

resource "google_cloud_run_v2_service" "monitor" {
  name                = "gcp-metadata-monitor"
  location            = var.region
  deletion_protection = false
  ingress             = "INGRESS_TRAFFIC_ALL"
  template {
    service_account                  = google_service_account.runtime.email
    timeout                          = "1800s"
    max_instance_request_concurrency = 1
    scaling {
      min_instance_count = 0
      max_instance_count = 1
    }
    containers {
      image = var.image
      resources {
        limits = { cpu = "1", memory = "512Mi" }
      }
      dynamic "env" {
        for_each = {
          GOOGLE_CLOUD_PROJECT  = var.project_id
          METADATA_DATASET      = var.dataset_id
          BQ_LOCATION           = var.bq_location
          STATE_BUCKET          = google_storage_bucket.state.name
          REPORT_TIMEZONE       = var.timezone
          MAXIMUM_BYTES_BILLED  = tostring(var.maximum_bytes_billed)
          SMTP_HOST             = var.smtp_host
          SMTP_PORT             = tostring(var.smtp_port)
          SMTP_USER             = var.smtp_user
          SMTP_FROM             = var.smtp_from
          SMTP_SECURITY         = var.smtp_security
          NOTIFICATIONS_ENABLED = tostring(var.notifications_enabled)
        }
        content {
          name  = env.key
          value = env.value
        }
      }
      dynamic "env" {
        for_each = var.notifications_enabled ? [1] : []
        content {
          name = "SMTP_PASS"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.smtp.secret_id
              version = var.smtp_secret_version
            }
          }
        }
      }
    }
  }
  depends_on = [
    google_project_service.api, google_secret_manager_secret_iam_member.runtime,
    google_bigquery_table.metadata, google_storage_bucket_iam_member.runtime
  ]
}

resource "google_cloud_scheduler_job" "daily" {
  name             = "gcp-monitor-daily"
  region           = var.region
  schedule         = var.schedule
  time_zone        = var.timezone
  paused           = var.scheduler_paused
  attempt_deadline = "1800s"
  retry_config {
    retry_count = 0
  }
  http_target {
    http_method = "POST"
    uri         = "${google_cloud_run_v2_service.monitor.uri}/run-monitoring"
    headers     = { "Content-Type" = "application/json" }
    body        = base64encode(jsonencode({ preview = !var.notifications_enabled }))
    oidc_token {
      service_account_email = google_service_account.scheduler.email
      audience              = google_cloud_run_v2_service.monitor.uri
    }
  }
  depends_on = [google_cloud_run_v2_service_iam_member.scheduler]
}
