terraform {
  # 1.10 is the floor for native S3 state locking (backend.tf: use_lockfile).
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source = "hashicorp/aws"
      # Pinned to the 5.x major: the community modules used here (VPC ~> 5.x,
      # EKS ~> 20.x) are built against provider v5. Moving to v6 is a breaking
      # upgrade to be taken deliberately, not picked up by a fresh `init`.
      version = "~> 5.0"
    }
  }
}
