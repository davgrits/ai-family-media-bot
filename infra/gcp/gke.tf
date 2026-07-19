resource "google_container_cluster" "main" {
  name     = var.project_name
  location = var.cluster_location

  network    = google_compute_network.main.id
  subnetwork = google_compute_subnetwork.gke.id

  # Node pools are declared separately, so remove GKE's unmanaged default.
  remove_default_node_pool = true
  initial_node_count       = 1

  networking_mode = "VPC_NATIVE"

  ip_allocation_policy {
    cluster_secondary_range_name  = "pods"
    services_secondary_range_name = "services"
  }

  private_cluster_config {
    enable_private_nodes    = true
    enable_private_endpoint = false
  }

  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }

  release_channel {
    channel = "REGULAR"
  }

  addons_config {
    horizontal_pod_autoscaling {
      disabled = false
    }
  }

  logging_config {
    enable_components = ["SYSTEM_COMPONENTS", "WORKLOADS"]
  }

  monitoring_config {
    enable_components = ["SYSTEM_COMPONENTS"]
  }

  deletion_protection = var.deletion_protection

  depends_on = [
    google_project_service.required["container.googleapis.com"],
    google_compute_router_nat.egress,
  ]
}

resource "google_container_node_pool" "web" {
  name     = "web"
  location = var.cluster_location
  cluster  = google_container_cluster.main.name

  node_count = 1

  node_config {
    machine_type    = var.web_machine_type
    service_account = google_service_account.nodes.email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]

    labels = {
      role = "web"
    }

    workload_metadata_config {
      mode = "GKE_METADATA"
    }
  }

  autoscaling {
    min_node_count = 1
    max_node_count = var.web_max_nodes
  }

  depends_on = [
    google_project_iam_member.nodes_artifact_reader,
    google_project_iam_member.nodes_log_writer,
    google_project_iam_member.nodes_metric_writer,
  ]
}

resource "google_container_node_pool" "workers" {
  name     = "workers"
  location = var.cluster_location
  cluster  = google_container_cluster.main.name

  node_count = 0

  node_config {
    machine_type    = var.worker_machine_type
    service_account = google_service_account.nodes.email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]
    spot            = true

    labels = {
      role = "worker"
    }

    taint {
      key    = "workload"
      value  = "jobs"
      effect = "NO_SCHEDULE"
    }

    workload_metadata_config {
      mode = "GKE_METADATA"
    }
  }

  autoscaling {
    min_node_count = 0
    max_node_count = var.worker_max_nodes
  }

  depends_on = [
    google_project_iam_member.nodes_artifact_reader,
    google_project_iam_member.nodes_log_writer,
    google_project_iam_member.nodes_metric_writer,
  ]
}
