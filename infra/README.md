# infra/ — Terraform for the AWS environment

Single Terraform root, single state file, single environment (this is a
personal project — there is no dev/prod split, so there are no `env` variables
or name suffixes). One `.tf` file per concern so each design decision is easy
to find and review.

**Hybrid style, on purpose:**

- **Community modules** for the boilerplate-heavy parts with no
  project-specific decisions: `terraform-aws-modules/vpc` and
  `terraform-aws-modules/eks`.
- **Raw resources** everywhere a decision lives: SQS tuning, S3 lifecycle,
  ECR policy, VPC endpoints, and all IAM/IRSA policy content.

## Layout

| File           | Contains |
|----------------|----------|
| `versions.tf`  | Terraform >= 1.10, AWS provider pinned `~> 5.x` |
| `providers.tf` | Region + `default_tags` (Project, ManagedBy) on everything |
| `backend.tf`   | S3 remote state + native S3 locking (`use_lockfile`; bucket created by hand — see `bootstrap/`) |
| `variables.tf` | Region, project name, instance types, worker max, Bedrock model ids |
| `vpc.tf`       | 2-AZ VPC, public+private subnets, **single** NAT gateway (cost decision) |
| `endpoints.tf` | S3 gateway endpoint + interface endpoints (bedrock-runtime, sqs, sts) — sensitive/constant traffic stays off the NAT; ECR and logs deliberately ride the NAT (FinOps, documented in-file) |
| `eks.tf`       | Cluster + 2 managed node groups: `web` (on-demand, warm) and `workers` (Spot, 0→N, tainted) |
| `sqs.tf`       | Jobs queue + DLQ (redrive after 3, 300s visibility, long polling) |
| `s3.tf`        | Private media bucket, SSE-S3, 30-day expiry (media is ephemeral) |
| `ecr.tf`       | One repo, scan on push, keep last 10 images, immutable tags |
| `irsa.tf`      | Least-privilege IRSA roles: app, keda-operator, cluster-autoscaler |
| `outputs.tf`   | Everything the k8s manifests / CI / KEDA need |

## Apply order (staged)

Nothing in the network or data plane depends on EKS, so the stack brings up in
reviewable stages. `-target` is used for the staged bring-up only; the final
full `apply` converges everything.

```bash
# 0. Bootstrap (already done, by hand): the state bucket exists and
#    backend.tf carries its name — see bootstrap/README.md. Then:
terraform init

# 1. Network: VPC + endpoints
terraform apply -target=module.vpc
terraform apply -target=aws_vpc_endpoint.s3 -target=aws_vpc_endpoint.interface

# 2. Data plane: queue, bucket, registry (independent of EKS)
terraform apply \
  -target=aws_sqs_queue.jobs -target=aws_sqs_queue.jobs_dlq \
  -target=aws_s3_bucket.media -target=aws_ecr_repository.app

# 3. EKS (the slow one, ~15 minutes)
terraform apply -target=module.eks

# 4. IRSA roles + everything remaining (full converge)
terraform apply
```

## What Terraform deliberately does NOT do

- **KEDA and cluster-autoscaler themselves** — installed later via Helm.
  Terraform only prepares their IRSA roles (`irsa.tf`); the Helm values then
  just reference the role ARNs from `outputs.tf`.
- **Kubernetes manifests** — the app deploys with the `role=web` /
  `role=worker` node selectors, the `workload=jobs:NoSchedule` toleration on
  workers, and the IRSA role annotation on the `app/family-media-bot`
  service account.
- **Secrets** — there are none to manage on the AWS side: Bedrock/S3/SQS auth
  is all IRSA. The only real secret in the system is the Telegram bot token,
  which belongs to the k8s layer.
