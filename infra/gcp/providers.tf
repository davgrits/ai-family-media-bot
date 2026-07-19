provider "google" {
  project = var.project_id
  region  = var.region

  default_labels = {
    project   = var.project_name
    managedby = "terraform"
  }
}
