# Roadmap

Status legend: [x] done, [ ] pending

> Runtime: GCP only. One Terraform root, one Helm chart. AWS support was built,
> measured, and deliberately removed — a second cloud that is never deployed
> doubles the reading cost of the repo without proving anything the port and
> adapter interfaces do not already prove.

## 0. Core application

- [x] FastAPI service verified end-to-end with local adapters
- [x] Ports for queue, story, image, and storage dependencies
- [x] Local in-memory, fake-model, and filesystem adapters
- [x] Age-appropriate story and child-safe illustration prompts
- [x] Telegram polling and webhook intake
- [x] Ack after successful processing; nack/retry on failure
- [x] Python 3.11 package, unit tests, and container image
- [x] Adapters raise on provider failure instead of fabricating a success
- [ ] Character registry in git, injected verbatim into story and image prompts
- [ ] `/family list`
- [ ] Story language selection (en/ru/he)

## 1. GCP runtime adapters

- [x] Pub/Sub, GCS, and Vertex AI adapters
- [x] Adapter selection through environment-driven factories, validated at startup
- [x] Story/image pipeline with no cloud SDK imports in business logic
- [x] Streaming pull with flow control capped at worker concurrency
- [x] Models: `gemini-3.5-flash` and `gemini-2.5-flash-image`
- [x] Per-job cost includes reasoning tokens, which are billed but excluded from
      the visible output count
- [ ] Upper time bound on every model call

## 2. Infrastructure (Terraform)

- [x] VPC, Cloud NAT, and private-node GKE
- [x] Pub/Sub jobs topic, subscription, and dead-letter queue
- [x] Private GCS media bucket with lifecycle cleanup
- [x] Artifact Registry
- [x] Workload Identity for the application
- [x] Applied and validated in project `ai-family-media-bot`
- [ ] Consolidate to one node pool (the two-pool split existed for scale-to-zero)
- [ ] Workload Identity Federation for CI, so no key file is ever issued

## 3. Kubernetes deployment (Helm)

- [x] One schema-validated chart
- [x] Web and worker Deployments
- [x] Image tag supplied at upgrade time, never committed
- [x] Chart lints and renders
- [ ] Service template and a `helm test` hook against `/healthz`
- [ ] `checksum/characters` annotation so a ConfigMap-only change reaches the pods

## 4. Production verification

- [x] GKE web pod healthy and ready
- [x] Pub/Sub, GCS, Vertex AI, and Workload Identity readiness checks
- [x] Live Gemini story and image generation from the running pod
- [x] Telegram Bot API connectivity and polling startup
- [x] Cold-start latency measured: 4m43s activation, which is what removed
      scale-to-zero
- [ ] Capture a clean Telegram message → Pub/Sub → worker → reply demonstration
- [ ] Verify character consistency across two illustrations of the same cast

## 5. CI/CD (GitHub Actions)

- [ ] Run unit tests, ruff, Helm validation, and Terraform checks on pull requests
- [ ] Build an AMD64 or multi-architecture container image
- [ ] Push to Artifact Registry using Workload Identity Federation, keyless
- [ ] Promote immutable image tags through `--set image.tag`
- [ ] Add protected deployment environments and required checks

## 6. Observability

- [x] Structured JSON application logs with no personal data
- [x] Prometheus health, job, duration, queue-wait, and cost metrics
- [x] OpenTelemetry spans with no-op behavior when no exporter is configured
- [ ] **Collect the metrics that are already emitted** — nothing scrapes
      `/metrics` today, so `fmb_queue_wait_seconds` measured the cold start from
      day one and no one could see it
- [ ] Alert on sustained queue backlog (`oldest_unacked_message_age`)
- [ ] Detect a permanently dead Pub/Sub stream: the pod stays Ready while
      consuming nothing
- [ ] Alert on dead-letter messages and crash loops

## 7. Portfolio and operations

- [x] README reflects the GCP-only deployment
- [x] Architecture diagram matches what is actually running
- [x] Operating runbook
- [ ] Add screenshots of the Telegram result
- [ ] Publish a current GCP cost breakdown and budget guardrails
- [ ] Add teardown and restoration instructions

## Working notes

- The fixed-cost floor is the GKE control plane (free for one zonal cluster),
  Cloud NAT, and the node running both warm tiers. Model generation is
  usage-driven. Roughly ₪220–280/month.
- Two warm tiers is a deliberate cost-for-latency trade: an idle worker costs
  ~₪90/month and removes 4m43s from the first request after a quiet period.
