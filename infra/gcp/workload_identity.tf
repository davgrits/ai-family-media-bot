locals {
  # Kept as a map rather than flattened to a single resource: it still reads
  # clearly and leaves room for a second identity without restructuring.
  workload_identities = {
    app = { namespace = "app", service_account = "family-media-bot", gsa = google_service_account.app.email }
  }
}

resource "google_service_account_iam_member" "workload_identity" {
  for_each = local.workload_identities

  service_account_id = "projects/${var.project_id}/serviceAccounts/${each.value.gsa}"
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[${each.value.namespace}/${each.value.service_account}]"

  # The <project>.svc.id.goog pool is created with the GKE cluster. Without
  # this dependency, a first apply can race and IAM returns "Identity Pool does
  # not exist" even though the configuration is valid.
  depends_on = [google_container_cluster.main]
}
