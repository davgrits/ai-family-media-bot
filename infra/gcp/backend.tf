# The state bucket is deliberately bootstrapped outside this root: Terraform
# needs its backend before it can create managed resources. Initialise with:
# terraform init -backend-config="bucket=<globally-unique-state-bucket>"
#
# `prefix` keeps this stack isolated from any other Terraform states in that
# bucket. GCS provides object generation preconditions, which Terraform uses
# for state locking.
terraform {
  backend "gcs" {
    prefix = "ai-family-media-bot/gcp"
  }
}
