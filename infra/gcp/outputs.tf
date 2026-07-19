output "cluster_name" {
  description = "GKE cluster name."
  value       = google_container_cluster.main.name
}

output "project_id" {
  description = "GCP project ID that owns this stack."
  value       = var.project_id
}

output "cluster_location" {
  description = "GKE zonal or regional location."
  value       = google_container_cluster.main.location
}

output "region" {
  description = "Region used by Artifact Registry, GCS, and networking."
  value       = var.region
}

output "artifact_registry_repository" {
  description = "Artifact Registry Docker repository URL prefix."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.app.repository_id}"
}

output "jobs_topic_name" {
  description = "Pub/Sub topic used by the web tier to publish jobs."
  value       = google_pubsub_topic.jobs.name
}

output "jobs_subscription_name" {
  description = "Pub/Sub subscription consumed by workers and observed by KEDA."
  value       = google_pubsub_subscription.jobs.name
}

output "media_bucket_name" {
  description = "Private GCS bucket for generated media."
  value       = google_storage_bucket.media.name
}

output "app_google_service_account_email" {
  description = "GSA to annotate on app/family-media-bot for Workload Identity."
  value       = google_service_account.app.email
}

output "keda_google_service_account_email" {
  description = "GSA to annotate on keda/keda-operator for Workload Identity."
  value       = google_service_account.keda.email
}
