provider "aws" {
  region = var.region

  # Stamped on every resource this configuration creates: cost allocation and
  # "who owns this?" are answered without per-resource tag blocks.
  default_tags {
    tags = {
      Project   = var.project
      ManagedBy = "terraform"
    }
  }
}

# Used to build globally-unique names (S3 bucket) and account-scoped ARNs
# (Bedrock inference profiles) without hardcoding the account id in the repo.
data "aws_caller_identity" "current" {}
