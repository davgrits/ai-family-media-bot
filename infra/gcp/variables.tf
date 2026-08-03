variable "project_id" {
  description = "GCP project ID that owns the GKE cluster and managed services."
  type        = string
}

variable "project_name" {
  description = "Stable, lowercase resource-name prefix. Changing it replaces named resources."
  type        = string
  default     = "ai-family-media-bot"

  validation {
    condition     = can(regex("^[a-z]([-a-z0-9]{0,61}[a-z0-9])?$", var.project_name))
    error_message = "project_name must be a lowercase GCP-compatible name (3-63 characters)."
  }
}

variable "region" {
  description = "Region for networking, Artifact Registry, GCS, and regional services."
  type        = string
  default     = "us-central1"
}

variable "cluster_location" {
  description = "GKE location. A single zone uses the GKE free-tier management credit."
  type        = string
  default     = "us-central1-a"
}

variable "network_cidr" {
  description = "Primary subnet range for GKE nodes."
  type        = string
  default     = "10.20.0.0/20"
}

variable "pods_cidr" {
  description = "Secondary range used by VPC-native GKE Pods."
  type        = string
  default     = "10.24.0.0/16"
}

variable "services_cidr" {
  description = "Secondary range used by VPC-native GKE Services."
  type        = string
  default     = "10.25.0.0/20"
}

variable "node_machine_type" {
  description = <<-EOT
    Machine type for the single node pool. e2-standard-2 allocates ~1930m CPU,
    against ~900m of GKE system requests, 300m for the web and worker pods, and
    250m of headroom for the worker's rolling-update surge. e2-medium allocates
    940m and does not fit.
  EOT
  type        = string
  default     = "e2-standard-2"
}

variable "node_disk_size_gb" {
  description = "Node boot disk. The default 100 GB is far more than two small Python containers need."
  type        = number
  default     = 50

  validation {
    condition     = var.node_disk_size_gb >= 20
    error_message = "GKE requires at least 20 GB for a node boot disk."
  }
}

variable "node_disk_type" {
  description = "pd-standard is cheaper and fast enough; nothing here is disk-bound."
  type        = string
  default     = "pd-standard"
}

variable "media_retention_days" {
  description = "Generated media retention in GCS."
  type        = number
  default     = 30
}

variable "deletion_protection" {
  description = "Protect the GKE cluster from accidental Terraform deletion. Set false only before an intentional destroy."
  type        = bool
  default     = true
}
