# Roadmap

Status legend: [x] done, [ ] pending

> Current runtime: GCP only. AWS and GCP infrastructure are defined in separate
> Terraform roots, and one shared Helm chart renders either deployment.

## 0. Core application

- [x] FastAPI service verified end-to-end with local adapters
- [x] Ports for queue, story, image, and storage dependencies
- [x] Local in-memory, fake-model, and filesystem adapters
- [x] Age-appropriate story and child-safe illustration prompts
- [x] Telegram polling and webhook intake
- [x] Ack after successful processing; nack/retry on failure
- [x] Python 3.11 package, unit tests, and container image

## 1. Multi-cloud runtime adapters

- [x] AWS: SQS, S3, and Bedrock adapters
- [x] GCP: Pub/Sub, GCS, and Vertex AI adapters
- [x] Provider selection through environment-driven factories
- [x] Shared story/image pipeline with no cloud SDK imports in business logic
- [x] Current models: `gemini-3.5-flash` and `gemini-2.5-flash-image`

## 2. Infrastructure (Terraform)

### GCP — deployed

- [x] VPC, Cloud NAT, and private-node GKE
- [x] Separate on-demand web and Spot worker node pools
- [x] Pub/Sub jobs topic, subscription, and dead-letter queue
- [x] Private GCS media bucket with lifecycle cleanup
- [x] Artifact Registry
- [x] Workload Identity for the application and KEDA
- [x] Applied and validated in project `ai-family-media-bot`

### AWS — defined, not currently deployed

- [x] Independent Terraform root and state configuration
- [x] VPC, private EKS nodes, NAT, and selected VPC endpoints
- [x] SQS jobs queue and dead-letter queue
- [x] Private S3 media bucket with lifecycle cleanup
- [x] ECR and IRSA roles for the application and cluster add-ons
- [x] Terraform formatting and validation
- [ ] Apply or restore the AWS environment when an AWS runtime is required

## 3. Shared Kubernetes deployment (Helm)

- [x] One schema-validated chart for both AWS and GCP
- [x] Shared web and worker Deployments
- [x] Provider-specific values for adapters, identity, and cloud resources
- [x] Provider-specific KEDA scaler branches
- [x] GCP KEDA installation and application release
- [x] GCP worker and Spot node pool scale to zero while idle
- [x] AWS and GCP releases lint and render successfully
- [ ] Deploy the shared chart to AWS and verify SQS-driven scale-up

## 4. GCP production verification

- [x] GKE web pod healthy and ready
- [x] Pub/Sub, GCS, Vertex AI, and Workload Identity readiness checks
- [x] Live Gemini story and image generation from the running pod
- [x] Telegram Bot API connectivity and polling startup
- [x] KEDA ScaledObject healthy with an idle worker count of zero
- [ ] Capture a clean Telegram message → Pub/Sub → worker → reply demonstration
- [ ] Capture KEDA and GKE worker scale-up/scale-down evidence

## 5. CI/CD (GitHub Actions)

- [ ] Run unit tests, Helm validation, and Terraform checks on pull requests
- [ ] Build an AMD64 or multi-architecture container image
- [ ] Push to Artifact Registry for GCP and ECR for AWS using OIDC
- [ ] Promote immutable image tags through provider-specific Helm values
- [ ] Add protected deployment environments and required checks

## 6. Observability

- [x] Structured JSON application logs
- [x] Prometheus health, job, duration, and cost metrics
- [x] OpenTelemetry spans with no-op behavior when no exporter is configured
- [x] KEDA scaling from Google Cloud Monitoring's Prometheus endpoint
- [ ] Add operational dashboards
- [ ] Alert on dead-letter messages, crash loops, and sustained queue backlog
- [ ] Define provider-specific log and metric retention

## 7. Portfolio and operations

- [x] README reflects the shared chart and GCP-only live deployment
- [x] Architecture diagram distinguishes live GCP from the AWS target
- [x] GCP operating and interview runbook
- [ ] Add screenshots of the Telegram result and scaling sequence
- [ ] Publish a current GCP cost breakdown and budget guardrails
- [ ] Add GCP teardown and restoration instructions
- [ ] Add an AWS re-deployment runbook

## Working notes

- The live fixed-cost floor is the GKE control plane, Cloud NAT, and warm web
  node; model generation and Spot workers are usage-driven.
- AWS is source-controlled and render-tested, but it has no current runtime
  cost because it is not deployed.
