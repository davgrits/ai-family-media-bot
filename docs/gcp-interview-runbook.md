# GCP interview runbook

This is the short operating and explanation guide for the live GCP path.

## Thirty-second architecture

Terraform creates a zonal private-node GKE cluster, Artifact Registry, Pub/Sub,
GCS, Vertex AI permissions, and two Google service accounts. Helm installs KEDA
and the application. Workload Identity maps Kubernetes service accounts to
Google service accounts, so there are no downloaded JSON keys.

The always-on web pod long-polls Telegram and publishes a frozen `Job` JSON
contract to Pub/Sub. KEDA reads the subscription backlog from Google Managed
Prometheus and scales worker pods from zero. GKE then scales a tainted Spot node
pool from zero. A worker generates a story and image with Vertex AI, stores the
image in GCS, replies through Telegram, and acknowledges the Pub/Sub delivery.
Failures are nacked and eventually reach the dead-letter topic.

```text
Telegram -> web pod -> Pub/Sub -> KEDA/HPA -> worker pod
               |                         -> Vertex AI
               |                         -> GCS
               `---- Workload Identity ------^
```

## Live deployment facts

- GCP project: `ai-family-media-bot`
- GKE: zonal `us-central1-a`, private nodes, Cloud NAT
- Always-on pool: one on-demand `e2-medium`
- Worker pool: Spot `e2-standard-2`, autoscaling `0..5`, tainted
- KEDA: Helm chart `2.20.1`; application workers `0..10`
- App image: Artifact Registry with immutable deployment tags
- Story: `gemini-3.5-flash`
- Image: `gemini-2.5-flash-image`
- Vertex endpoints: text uses `global`; image generation uses regional `us-central1`
- Queue/storage: Pub/Sub and GCS
- Helm namespaces: `keda` and `app`

The one `e2-medium` choice is deliberate. GKE system pods plus three default
KEDA controllers do not fit on one shared-core `e2-small`; the cluster scaled
to two small nodes. Right-sized KEDA requests plus one medium node use one boot
disk and provide enough memory while keeping the worker pool at zero when idle.

## Five-minute demo

```bash
# Terraform is authoritative and drift-free.
terraform -chdir=infra/gcp plan -detailed-exitcode

# Show one warm node and the separate scale-to-zero worker pool behavior.
kubectl get nodes -L node.kubernetes.io/instance-type,role
kubectl -n app get deployments,pods,scaledobjects,hpa

# Show both independently managed Helm releases.
helm list -n keda
helm list -n app

# Prove application ADC access without exposing credentials.
kubectl -n app exec deployment/family-media-bot-web -- \
  python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8080/readyz').read().decode())"
```

Expected idle state:

- web deployment `1/1`;
- worker deployment `0/0`;
- ScaledObject `READY=True`, `ACTIVE=False`, trigger `prometheus`;
- `/readyz` reports queue, storage, story, and image as `true`.

Then send the Telegram bot `/surprise` or `/custom a small dragon learns to
share`. Watch the event-driven path:

```bash
kubectl -n app get pods -w
kubectl get nodes -L role -w
kubectl -n app logs deployment/family-media-bot-web -f
kubectl -n keda logs deployment/keda-operator -f
```

The expected sequence is Pub/Sub backlog `0 -> 1`, KEDA worker `0 -> 1`, GKE
Spot worker node `0 -> 1`, generation/reply/ack, and scale-down after cooldown.

## Deployment commands

The full reusable commands are in
[`deploy/charts/family-media-bot/README.md`](../deploy/charts/family-media-bot/README.md).
The same chart renders AWS or GCP; `deploy/values/gcp.yaml` supplies only the
provider-specific image, adapters, identity annotation, and resource names.
The important ownership boundary is:

- Terraform owns cloud APIs, network, GKE, Pub/Sub, GCS, Artifact Registry,
  IAM, and Workload Identity bindings.
- The KEDA Helm release owns KEDA CRDs/controllers.
- The application Helm release owns the app KSA, ConfigMap, Deployments,
  TriggerAuthentication, and ScaledObject.
- A Kubernetes Secret contains the Telegram token and is never committed.

## Interview talking points

- **Why Helm?** One parameterized, schema-validated release renders the web and
  worker tiers, cloud configuration, identity annotation, and KEDA objects.
  `helm upgrade --rollback-on-failure --wait` provides revisioned rollouts.
- **Why Terraform outputs into Helm?** Terraform creates cloud identifiers;
  Helm consumes them. Neither tool tries to own the other's resources.
- **Why Workload Identity?** Short-lived credentials, no JSON keys, and distinct
  least-privilege identities for the app and KEDA.
- **Why Pub/Sub?** It decouples Telegram latency from model latency and gives
  ack/nack, retry, retention, and dead-letter behavior.
- **Why KEDA plus GKE autoscaling?** KEDA scales pods from queue demand; GKE
  scales nodes to host those pods. They solve different layers.
- **Why Managed Prometheus?** KEDA deprecated its MQL Pub/Sub scaler. Google
  already exposes Pub/Sub metrics through a Prometheus endpoint, so no in-cluster
  Prometheus server is needed.
- **Why zonal GKE?** This portfolio workload accepts one-zone availability to
  use the GKE free-tier management credit and avoid regional node multiplication.
- **What would production change?** Regional GKE, multiple warm nodes, webhook
  ingress instead of long polling, PodDisruptionBudgets, NetworkPolicies,
  managed secrets, alerts/SLOs, CI image signing/scanning, and per-environment
  Terraform states.

## Security note

Dependency HTTP request logging is forced to warning level because Telegram
puts the bot token in the URL path. If a token ever appears in logs, revoke it
with BotFather, update the local `.env`, and replace the Kubernetes Secret.
