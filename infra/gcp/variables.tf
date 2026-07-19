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

variable "web_machine_type" {
  description = "Always-on web-pool machine type. e2-medium fits GKE system services, KEDA, and the web pod on one node."
  type        = string
  default     = "e2-medium"
}

variable "web_max_nodes" {
  description = "Maximum always-on web nodes. One e2-medium is cheaper than two e2-small nodes after boot disks."
  type        = number
  default     = 1

  validation {
    condition     = var.web_max_nodes >= 1
    error_message = "web_max_nodes must be at least one."
  }
}

variable "worker_machine_type" {
  description = "Spot worker-pool machine type. Generation work is retryable."
  type        = string
  default     = "e2-standard-2"
}

variable "worker_max_nodes" {
  description = "Maximum Spot worker nodes; caps spend during a queue flood."
  type        = number
  default     = 5

  validation {
    condition     = var.worker_max_nodes >= 1
    error_message = "worker_max_nodes must be at least one."
  }
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
