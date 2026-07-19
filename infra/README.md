# Infrastructure

Each cloud is an independent Terraform root and therefore has independent
state. Never run Terraform from this directory: select the cloud explicitly.

| Root | Platform | State backend |
|---|---|---|
| `aws/` | AWS: VPC, EKS, SQS, S3, ECR and IRSA | S3 backend |
| `gcp/` | GCP: VPC, GKE, Pub/Sub, GCS, Artifact Registry and Workload Identity | GCS backend (bootstrapped separately) |

The AWS root is retained as a supported environment but is not currently
deployed. It was moved intact from this directory; moving a Terraform root does
not change its resource addresses or S3 backend key. Initialise it from its new
directory with `terraform -chdir=infra/aws init`.

The GCP root owns the current live environment. It intentionally has no default
project ID or state-bucket name in source control. Supply those from a
non-committed `terraform.tfvars` file or CI variables. See `gcp/README.md` for
the bootstrap and deployment sequence.
