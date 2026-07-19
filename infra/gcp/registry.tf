resource "google_artifact_registry_repository" "app" {
  location      = var.region
  repository_id = var.project_name
  description   = "Container images for the family media bot"
  format        = "DOCKER"

  depends_on = [google_project_service.required["artifactregistry.googleapis.com"]]
}
