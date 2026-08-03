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

# One pool. Both deployments and every GKE system pod share it.
#
# There were two: an on-demand "web" pool and a tainted Spot "workers" pool that
# scaled to zero. That split existed to make scale-to-zero work, and scale-to-zero
# is gone — activating a worker from zero on Pub/Sub backlog measured 4m43s,
# because the backlog metric samples on a ~60s interval and can gap for minutes.
# With both tiers warm, a taint that repels everything and a second machine shape
# buy nothing.
#
# Sizing: ~900m CPU of GKE system requests on a single-node zonal cluster (the
# per-node DaemonSets plus every cluster singleton), 300m for the web and worker
# pods, and 250m of headroom for the worker's rolling-update surge — the chart
# uses maxSurge 1, so two workers exist briefly during a release, and a surge pod
# that cannot be scheduled leaves the rollout stuck. That is ~1450m against the
# 1930m an e2-standard-2 allocates. An e2-medium allocates 940m and does not fit.
#
# No autoscaling: a fixed node count makes the monthly bill exactly predictable
# against a finite trial credit, and there is nothing to scale for. upgrade_settings
# still gives GKE a temporary surge node during node upgrades.
resource "google_container_node_pool" "main" {
  name     = "main"
  location = var.cluster_location
  cluster  = google_container_cluster.main.name

  node_count = 1

  node_config {
    machine_type    = var.node_machine_type
    disk_size_gb    = var.node_disk_size_gb
    disk_type       = var.node_disk_type
    service_account = google_service_account.nodes.email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]

    # No Spot. Spot and "warm" contradict each other: a preempted node
    # reintroduces exactly the cold start the warm worker exists to remove.
    workload_metadata_config {
      mode = "GKE_METADATA"
    }
  }

  # Surge one node during an upgrade rather than draining in place, so a node
  # upgrade does not take the only node down with the whole bot on it.
  upgrade_settings {
    max_surge       = 1
    max_unavailable = 0
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }

  depends_on = [
    google_project_iam_member.nodes_artifact_reader,
    google_project_iam_member.nodes_log_writer,
    google_project_iam_member.nodes_metric_writer,
  ]
}
