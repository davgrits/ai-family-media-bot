# Private GKE nodes have no public IPs. Cloud NAT provides the only required
# internet egress (Telegram); Private Google Access keeps Google API traffic on
# Google's network where supported.
resource "google_compute_network" "main" {
  name                    = var.project_name
  auto_create_subnetworks = false
  depends_on              = [google_project_service.required["compute.googleapis.com"]]
}

resource "google_compute_subnetwork" "gke" {
  name                     = "${var.project_name}-gke"
  region                   = var.region
  network                  = google_compute_network.main.id
  ip_cidr_range            = var.network_cidr
  private_ip_google_access = true

  secondary_ip_range {
    range_name    = "pods"
    ip_cidr_range = var.pods_cidr
  }

  secondary_ip_range {
    range_name    = "services"
    ip_cidr_range = var.services_cidr
  }
}

resource "google_compute_router" "nat" {
  name    = "${var.project_name}-nat"
  region  = var.region
  network = google_compute_network.main.id
}

resource "google_compute_router_nat" "egress" {
  name                               = "${var.project_name}-egress"
  router                             = google_compute_router.nat.name
  region                             = google_compute_router.nat.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"
}
