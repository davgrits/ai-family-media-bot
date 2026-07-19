resource "google_storage_bucket" "media" {
  name                        = "${var.project_id}-${var.project_name}-media"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  lifecycle_rule {
    condition {
      age = var.media_retention_days
    }
    action {
      type = "Delete"
    }
  }

  depends_on = [google_project_service.required["storage.googleapis.com"]]
}
