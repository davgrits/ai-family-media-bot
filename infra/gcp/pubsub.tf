# Pub/Sub's subscription is the queue. The worker receives from it, while the
# web tier publishes each Telegram update. Dead letters are retained for 14
# days. GCP's dead-letter policy requires at least five delivery attempts.
resource "google_pubsub_topic" "jobs" {
  name       = "${var.project_name}-jobs"
  depends_on = [google_project_service.required["pubsub.googleapis.com"]]
}

resource "google_pubsub_topic" "jobs_dlq" {
  name       = "${var.project_name}-jobs-dlq"
  depends_on = [google_project_service.required["pubsub.googleapis.com"]]
}

resource "google_pubsub_subscription" "jobs_dlq" {
  name                       = "${var.project_name}-jobs-dlq"
  topic                      = google_pubsub_topic.jobs_dlq.id
  message_retention_duration = "1209600s" # 14 days
}

resource "google_pubsub_subscription" "jobs" {
  name  = "${var.project_name}-jobs"
  topic = google_pubsub_topic.jobs.id

  # 300s was sized for a worst case that no longer exists: the worker bounds each
  # job at 90s and each model call at 40s. A deadline far longer than the work
  # means a crashed worker's messages sit invisible for five minutes before
  # anything else can take them — the opposite of what one warm replica needs.
  # The client caps its own lease at 60s to match.
  ack_deadline_seconds       = 60
  message_retention_duration = "86400s" # discard stale bedtime requests after one day

  # Space out redeliveries. Pub/Sub otherwise retries immediately, so a job
  # failing on a transient Vertex error burns all five attempts within seconds
  # and reaches the dead-letter topic before the outage has had time to clear.
  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.jobs_dlq.id
    max_delivery_attempts = 5
  }
}

# Pub/Sub redelivery is performed by the Google-managed Pub/Sub service agent,
# not the application identity. These two narrow grants are required for it to
# forward failed messages to the DLQ after the configured attempt count.
data "google_project" "current" {
  project_id = var.project_id
}

resource "google_pubsub_topic_iam_member" "pubsub_service_agent_dlq_publish" {
  topic      = google_pubsub_topic.jobs_dlq.name
  role       = "roles/pubsub.publisher"
  member     = "serviceAccount:service-${data.google_project.current.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
  depends_on = [google_pubsub_subscription.jobs]
}

resource "google_pubsub_subscription_iam_member" "pubsub_service_agent_jobs_consume" {
  subscription = google_pubsub_subscription.jobs.name
  role         = "roles/pubsub.subscriber"
  member       = "serviceAccount:service-${data.google_project.current.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}
