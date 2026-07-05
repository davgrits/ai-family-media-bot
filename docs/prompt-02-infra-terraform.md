# Claude Code prompt — session 02: Terraform infra

Paste everything below the line into Claude Code, run from the repo root.

---

You are working in the repo `ai-family-media-bot`. The app payload is done and
verified (FastAPI service in `app/`, async pipeline webhook → queue → worker →
storage, swap-point interfaces for SQS/Bedrock/S3/LocalDir, contract frozen in
`docs/api-contract.md` — read it first). Your job now is the `infra/` directory:
Terraform for the AWS environment. This is the centerpiece of a DevOps
portfolio — clarity and reviewability matter more than cleverness.

## Locked decisions — do not revisit
- Region: us-east-1.
- Hybrid style: community modules for boilerplate-heavy parts
  (terraform-aws-modules/vpc/aws, terraform-aws-modules/eks/aws); RAW resources
  for everything where design decisions live: SQS, S3, ECR, IRSA roles, VPC
  endpoints.
- Single Terraform root at `infra/`, one state file, clean per-concern .tf
  files. No custom nested modules.
- SINGLE environment — this is a personal project, there is no dev/prod split.
  No `env` variable, no Env tag, no environment suffixes in names.
- Worker autoscaling will be KEDA on SQS queue depth (KEDA itself installed
  later via Helm — Terraform only prepares its IRSA role).

## Files to create under infra/
- versions.tf     — terraform >= 1.7, pinned AWS provider ~> 5.x
- providers.tf    — region from var, default_tags on everything
                    (Project=ai-family-media-bot, ManagedBy=terraform)
- backend.tf      — S3 backend + DynamoDB lock table (names as placeholders);
                    also add infra/bootstrap/README.md with the 3-4 AWS CLI
                    commands to create the state bucket + lock table once, by hand
- variables.tf    — region (default us-east-1), project name, instance types,
                    worker max size, bedrock model IDs
- vpc.tf          — module terraform-aws-modules/vpc: 2 AZs, public + private
                    subnets, SINGLE NAT gateway (cost decision — Telegram
                    egress only), DNS support on. Tag private subnets as the
                    EKS module expects for internal-elb / cluster discovery.
- endpoints.tf    — RAW aws_vpc_endpoint resources: S3 (Gateway type, on
                    private route tables), Interface endpoints for
                    bedrock-runtime, ecr.api, ecr.dkr, sts, logs. Dedicated SG
                    allowing 443 from private subnets. Comment each endpoint
                    with WHY (sensitive media stays on AWS backbone; NAT is
                    for Telegram only).
- eks.tf          — module terraform-aws-modules/eks: cluster (name from
                    project name), two managed node groups:
                    * web: on-demand, 1 node, t3.small or similar, labels
                      role=web
                    * workers: SPOT, min 0 / desired 0 / max var.worker_max
                      (default 5), labels role=worker, taint
                      workload=jobs:NoSchedule
                    Enable IRSA/OIDC provider. Cluster endpoint: public
                    access, no CIDR restriction (admin has a dynamic home
                    IP; auth is still IAM+TLS) — add a short comment noting
                    the hardened alternatives (CIDR allowlist or private
                    endpoint + VPN) and why they're not used here.
- sqs.tf          — RAW: jobs queue + DLQ, redrive after 3 receives,
                    visibility timeout 300s (jobs can take minutes), long
                    polling 10s.
- s3.tf           — RAW: media bucket — private, block all public access,
                    SSE-S3, versioning off, lifecycle rule expiring objects
                    after 30 days (FinOps: generated media is ephemeral).
- ecr.tf          — RAW: one repo, scan on push, lifecycle policy keep last
                    10 images.
- irsa.tf         — RAW IAM roles with OIDC trust (use the EKS module's OIDC
                    output), least-privilege, no wildcards on resources:
                    * app role (serviceaccount family-media-bot, ns app):
                      bedrock:InvokeModel on the two model ARNs (model IDs
                      from variables), s3 Get/Put on media bucket ARN, sqs
                      Send/Receive/Delete/GetQueueAttributes on jobs queue.
                    * keda-operator role (ns keda): sqs GetQueueAttributes on
                      the jobs queue.
                    * cluster-autoscaler role (ns kube-system): standard
                      autoscaler policy scoped by cluster tag.
- outputs.tf      — cluster name, cluster endpoint, ECR repo URL, SQS queue
                    URL + ARN, media bucket name, app/keda/autoscaler role
                    ARNs, vpc id.

## Rules
- Every non-obvious choice gets a short comment explaining the reasoning —
  this repo will be read by interviewers.
- No secrets, no API keys, no hardcoded account IDs (use
  data.aws_caller_identity).
- Keep it apply-able in stages: nothing in vpc/endpoints/sqs/s3/ecr may
  depend on EKS.
- Do NOT run terraform apply. Run `terraform fmt` and `terraform validate`
  (init with -backend=false is fine for validate). Fix everything they report.
- Do not touch app/, docs/api-contract.md, or existing files outside infra/
  except: append an "Infrastructure" section to README.md (5-10 lines, what
  exists + staged apply order: bootstrap -> network -> data plane -> eks -> irsa).
- Finish with a short summary: files created, what to review manually, and
  the exact apply-order commands.
