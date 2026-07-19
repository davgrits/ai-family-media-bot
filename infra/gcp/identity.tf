# Separate Google service accounts mirror AWS IRSA: app pods get only their
# runtime data-plane permissions; KEDA gets read-only subscription visibility.
resource "google_service_account" "nodes" {
  account_id   = "family-media-gke-node"
  display_name = "GKE nodes for ${var.project_name}"
}

resource "google_service_account" "app" {
  account_id   = "family-media-app"
  display_name = "Application workload identity for ${var.project_name}"
}

resource "google_service_account" "keda" {
  account_id   = "family-media-keda"
  display_name = "KEDA workload identity for ${var.project_name}"
}

resource "google_project_iam_member" "nodes_artifact_reader" {
  project = var.project_id
  role    = "roles/artifactregistry.reader"
  member  = "serviceAccount:${google_service_account.nodes.email}"
}

resource "google_project_iam_member" "nodes_log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.nodes.email}"
}

resource "google_project_iam_member" "nodes_metric_writer" {
  project = var.project_id
  role    = "roles/monitoring.metricWriter"
  member  = "serviceAccount:${google_service_account.nodes.email}"
}

resource "google_storage_bucket_iam_member" "app_media" {
  bucket = google_storage_bucket.media.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.app.email}"
}

resource "google_pubsub_topic_iam_member" "app_publish" {
  topic  = google_pubsub_topic.jobs.name
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:${google_service_account.app.email}"
}

resource "google_pubsub_subscription_iam_member" "app_consume" {
  subscription = google_pubsub_subscription.jobs.name
  role         = "roles/pubsub.subscriber"
  member       = "serviceAccount:${google_service_account.app.email}"
}

# Subscriber grants message consumption but intentionally omits
# pubsub.subscriptions.get, which the non-mutating readiness check uses.
resource "google_pubsub_subscription_iam_member" "app_view" {
  subscription = google_pubsub_subscription.jobs.name
  role         = "roles/pubsub.viewer"
  member       = "serviceAccount:${google_service_account.app.email}"
}

resource "google_pubsub_subscription_iam_member" "keda_view" {
  subscription = google_pubsub_subscription.jobs.name
  role         = "roles/pubsub.viewer"
  member       = "serviceAccount:${google_service_account.keda.email}"
}

# Vertex model invocations are deliberately granted only after the GCP-native
# adapters and their explicit model IDs were added to the application.
resource "google_project_iam_member" "app_vertex_user" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.app.email}"
}

# The KEDA Pub/Sub scaler reads Cloud Monitoring subscription metrics. It does
# not receive, acknowledge, publish, or otherwise mutate application messages.
resource "google_project_iam_member" "keda_monitoring_viewer" {
  project = var.project_id
  role    = "roles/monitoring.viewer"
  member  = "serviceAccount:${google_service_account.keda.email}"
}
