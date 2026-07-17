# Roadmap

Status legend: [x] done, [ ] pending

## 0. Local application skeleton

- [x] FastAPI app stub, verified end-to-end locally
- [x] Hexagonal ports for every external dependency: QueuePort, StoryProvider, ImageProvider, StoragePort
- [x] Local in-memory/stub adapters: app runs fully without AWS
- [x] Project config on pyproject.toml
- [x] Dockerfile with layer-caching-friendly structure

## 1. Infrastructure (Terraform)

- [x] VPC: 2 AZ, public + private subnets, single NAT gateway
- [x] VPC endpoints: S3 gateway + interface (bedrock-runtime, sqs, sts)
- [x] SQS jobs queue + DLQ (redrive after 3)
- [x] S3 media bucket (SSE, 30-day expiry, public access blocked)
- [x] ECR repo (scan on push, immutable tags, keep last 10)
- [x] EKS cluster + node groups: web (on-demand) and workers (Spot, 0 -> N, tainted)
- [x] IRSA roles: app, keda-operator, cluster-autoscaler
- [x] Full converge: `terraform plan` clean, no drift
- [x] Verified scale-to-zero: workers node group starts at 0 nodes

## 2. Cluster add-ons (Helm)

- [x] KEDA with IRSA annotation (`keda_operator_role_arn`)
- [x] cluster-autoscaler with IRSA annotation (`cluster_autoscaler_role_arn`)
- [ ] Verify: KEDA ScaledObject scales workers 0 -> 1 on SQS message

## 3. Kubernetes manifests

- [ ] Namespace `app`, ServiceAccount `family-media-bot` with IRSA annotation (`app_role_arn`)
- [ ] Deployment web: nodeSelector `role=web`
- [ ] Deployment worker: nodeSelector `role=worker`, toleration `workload=jobs:NoSchedule`
- [ ] Secret: Telegram bot token
- [ ] KEDA ScaledObject on jobs queue

## 4. Application

- [x] Create Telegram bot via BotFather, obtain token
- [x] Story prompts for Bedrock (age-appropriate, per-child character descriptions)
- [x] Illustration prompts (stylized avatars only, no real photos of minors)
- [x] Wire real providers behind existing ports: Bedrock StoryProvider + ImageProvider, SQS QueuePort, S3 StoragePort
- [x] End-to-end test: Telegram message -> story + image delivered
- [x] Three languages (en/ru/he): /start language picker, per-chat choice in ProfileStore, story generated in the chat's language
- [x] Family story cast: photo + name caption -> vision model -> text character card (photo never persisted); /family manage; /fairytale stars the cast
- [x] Shared Router so webhook and polling paths behave identically
- [x] Unit tests (pytest): i18n completeness, command parsing, router conversation flows
- [ ] Verify with real Bedrock: he/en stories, vision character card from a photo
- [ ] terraform apply: S3 lifecycle now scoped to generated/ (profiles/ must not expire)

## 5. CI/CD (GitHub Actions)

- [ ] Workflow: test -> build -> push to ECR (OIDC auth, no long-lived keys)
- [ ] Deploy step: update image tag in EKS
- [ ] Branch protection + required checks

## 6. Observability

- [ ] Decide scope: CloudWatch Container Insights vs Prometheus + Grafana
- [ ] Metrics: queue depth, job duration, Bedrock latency, cost per story
- [ ] Alerts: DLQ non-empty, worker crash loop

## 7. Portfolio polish

- [ ] Top-level README: architecture diagram, FinOps section, design decisions
- [ ] Screenshots: scale-to-zero node output, KEDA scaling event, sample story
- [ ] Cost breakdown: what runs 24/7 vs on demand, monthly estimate
- [ ] Teardown/bring-up instructions (destroy when idle, NAT + EKS control plane are the fixed costs)

## Working notes

- Fixed hourly costs while cluster is up: EKS control plane, NAT gateway, web node. Destroy between sessions if idle for days.
