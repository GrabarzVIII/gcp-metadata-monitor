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

variable "project_id" {
  type        = string
  description = "Existing hosting project with billing enabled."
}

variable "region" {
  type    = string
  default = "europe-west1"
}

resource "google_project_service" "api" {
  for_each = toset([
    "artifactregistry.googleapis.com", "cloudbuild.googleapis.com",
    "iam.googleapis.com", "logging.googleapis.com", "storage.googleapis.com"
  ])
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_artifact_registry_repository" "images" {
  project       = var.project_id
  location      = var.region
  repository_id = "gcp-monitor"
  format        = "DOCKER"
  depends_on    = [google_project_service.api]
}

resource "google_service_account" "builder" {
  account_id   = "gcp-monitor-builder"
  display_name = "GCP Monitor image builder"
  depends_on   = [google_project_service.api]
}

resource "google_artifact_registry_repository_iam_member" "builder" {
  project    = var.project_id
  location   = var.region
  repository = google_artifact_registry_repository.images.name
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.builder.email}"
}

resource "google_project_iam_member" "builder_logs" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.builder.email}"
}

resource "google_storage_bucket" "build_source" {
  name                        = "${var.project_id}-monitor-build-source"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  lifecycle_rule {
    condition { age = 7 }
    action { type = "Delete" }
  }
}

resource "google_storage_bucket_iam_member" "builder_source" {
  bucket = google_storage_bucket.build_source.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.builder.email}"
}

output "image_repository" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
}

output "build_service_account" {
  value = google_service_account.builder.id
}

output "build_source_bucket" {
  value = google_storage_bucket.build_source.name
}
