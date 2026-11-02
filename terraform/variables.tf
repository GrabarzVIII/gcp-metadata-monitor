variable "project_id" {
  type        = string
  description = "Existing project hosting the monitoring service."
}

variable "region" {
  type    = string
  default = "europe-west1"
}

variable "image" {
  type        = string
  description = "Built container image URI, preferably pinned to a digest."
}

variable "dataset_id" {
  type    = string
  default = "monitoring"
}

variable "bq_location" {
  type    = string
  default = "EU"
}

variable "monitored_projects" {
  type        = set(string)
  default     = []
  description = "Projects in which the monitor can read logs."
}

variable "source_datasets" {
  type = list(object({
    project_id = string
    dataset_id = string
  }))
  default     = []
  description = "Source datasets to grant read-only access; no project-wide dataViewer."
}

variable "schedule" {
  type    = string
  default = "0 8 * * *"
}

variable "timezone" {
  type    = string
  default = "Europe/Warsaw"
}

variable "scheduler_paused" {
  type    = bool
  default = true
}

variable "notifications_enabled" {
  type    = bool
  default = false
}

variable "smtp_host" {
  type    = string
  default = ""
}

variable "smtp_port" {
  type    = number
  default = 587
}

variable "smtp_user" {
  type    = string
  default = ""
}

variable "smtp_from" {
  type    = string
  default = ""
}

variable "smtp_security" {
  type    = string
  default = "starttls"
  validation {
    condition     = contains(["starttls", "ssl"], var.smtp_security)
    error_message = "Use starttls or ssl."
  }
}

variable "smtp_secret_version" {
  type        = string
  default     = "latest"
  description = "Secret version to mount after uploading the SMTP password outside Terraform."
}

variable "maximum_bytes_billed" {
  type    = number
  default = 1073741824
}

variable "history_retention_days" {
  type    = number
  default = 90
  validation {
    condition     = var.history_retention_days >= 7
    error_message = "Keep at least seven days of history."
  }
}
