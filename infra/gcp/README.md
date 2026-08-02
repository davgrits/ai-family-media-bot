# GCP / GKE Terraform

This is a standalone Terraform root for the GCP deployment. It creates:

- a custom VPC, zonal VPC-native GKE and Cloud NAT (private nodes; Telegram is
  the only required public egress);
- a warm on-demand web node pool and a tainted Spot worker node pool;
- Artifact Registry, a private 30-day GCS media bucket, and Pub/Sub jobs plus
  dead-letter topic/subscription;
- least-privilege Google service accounts wired to Kubernetes with Workload
  Identity.

It enables Vertex AI and grants `roles/aiplatform.user` only to the application
workload identity. The application defaults are `gemini-3.5-flash` for stories
and `gemini-2.5-flash-image` for illustrations — the latter chosen because it
accepts reference images, which is what keeps a family character recognisable
across illustrations. The image adapter also supports Imagen model IDs when the
project has Model Garden access.

## Prerequisites

- Terraform 1.10+ and the Google Cloud CLI.
- A GCP project with billing enabled. Your deploying identity needs permission
  to enable APIs and create the resources listed above.
- `gcloud auth application-default login` for local Terraform, or a CI
  identity using application-default credentials.

## Bootstrap remote state

Choose a globally unique, private bucket name. This bucket is intentionally
created before Terraform so it can hold Terraform's own state. Versioning
allows recovery from accidental state writes.

```bash
PROJECT_ID="your-gcp-project-id"
STATE_BUCKET="your-globally-unique-tfstate-bucket"

gcloud config set project "$PROJECT_ID"
gcloud services enable storage.googleapis.com
gcloud storage buckets create "gs://$STATE_BUCKET" --location=us-central1 --uniform-bucket-level-access
gcloud storage buckets update "gs://$STATE_BUCKET" --versioning
```

## Plan and apply

```bash
cd infra/gcp
cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars and set project_id; the default zonal cluster avoids
# the regional GKE management fee for this portfolio workload

terraform init -backend-config="bucket=$STATE_BUCKET"
terraform fmt -check -recursive
terraform validate
terraform plan -out=tfplan
terraform apply tfplan
```

The GKE cluster is deletion-protected by default. To destroy it intentionally,
set `deletion_protection = false`, apply that change, then run destroy.

## Outputs used by deployment

```bash
gcloud container clusters get-credentials \
  "$(terraform output -raw cluster_name)" \
  --location "$(terraform output -raw cluster_location)" \
  --project "$(terraform output -raw project_id)"
```

The values consumed by `deploy/values/prod.yaml` are
`artifact_registry_repository`, `jobs_topic_name`, `jobs_subscription_name`,
`media_bucket_name`, and `app_google_service_account_email`.

## Deploy the application

The application uses the Helm chart at
[`deploy/charts/family-media-bot`](../../deploy/charts/family-media-bot) with
[`deploy/values/prod.yaml`](../../deploy/values/prod.yaml).

After this Terraform root has been applied, follow the chart's
[deployment guide](../../deploy/charts/family-media-bot/README.md) to build and
push the container, create the Telegram secret, and install the release.
