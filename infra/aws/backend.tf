# Remote state: S3 holds both the state object and the state lock
# (`use_lockfile = true`, native S3 locking, Terraform >= 1.10).
#
# Backend blocks cannot reference variables, so the bucket name below is
# hardcoded. The bucket was created ONCE by hand (three CLI commands —
# see bootstrap/README.md) before the first `terraform init`.
terraform {
  backend "s3" {
    bucket       = "family-media-bot-tfstate" # created in bootstrap/README.md
    key          = "family-media-bot/terraform.tfstate"
    region       = "us-east-1"
    use_lockfile = true
    encrypt      = true
  }
}
