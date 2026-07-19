# Infrastructure

Each cloud is an independent Terraform root and therefore has independent
state. Never run Terraform from this directory: select the cloud explicitly.

| Root | Platform | State backend |
|---|---|---|
| `aws/` | AWS: VPC, EKS, SQS, S3, ECR and IRSA | Existing S3 backend |
| `gcp/` | GCP: VPC, GKE, Pub/Sub, GCS, Artifact Registry and Workload Identity | GCS backend (bootstrapped separately) |

The AWS root is the existing deployed environment, moved intact from this
directory. Moving a Terraform root does not change its resource addresses or
its S3 backend key; initialise it from its new directory with
`terraform -chdir=infra/aws init`.

The GCP root intentionally has no default project id or state-bucket name.
Supply those from a non-committed `terraform.tfvars` file or your CI variables.
See `gcp/README.md` for the bootstrap and deployment sequence.
