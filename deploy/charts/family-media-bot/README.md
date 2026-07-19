# Multi-cloud Helm deployment

This is the canonical application chart for EKS and GKE. Shared templates own
the web and worker Deployments, Kubernetes service account, ConfigMap, and KEDA
objects. Provider values select AWS (SQS, S3, Bedrock, IRSA) or GCP (Pub/Sub,
GCS, Vertex AI, Workload Identity).

The image repository is always the complete repository path without a tag.
Terraform owns cloud resources and identity bindings; this chart consumes their
identifiers.

## Validate

From the repository root:

```bash
make helm-lint

# Inspect one provider in detail.
helm template family-media-bot deploy/charts/family-media-bot \
  --namespace app -f deploy/values/gcp.yaml
```

The JSON schema rejects unsupported providers, missing image coordinates, and
incomplete active-provider configuration.

## Install prerequisites

Create the Telegram token out-of-band and install KEDA before the application:

```bash
kubectl create namespace app --dry-run=client -o yaml | kubectl apply -f -
kubectl -n app create secret generic telegram-bot-token \
  --from-literal=TELEGRAM_BOT_TOKEN='<token from BotFather>'

helm repo add kedacore https://kedacore.github.io/charts
helm repo update
helm upgrade --install keda kedacore/keda --version 2.20.1 \
  --namespace keda --create-namespace \
  -f deploy/addons/gcp/keda-values.yaml --wait
```

Use `deploy/addons/aws/keda-values.yaml` on EKS. EKS also needs the
`aws-cluster-autoscaler` chart with
`deploy/addons/aws/cluster-autoscaler-values.yaml`.

## Deploy

Pass exactly one provider values file and override the immutable image tag:

```bash
# GCP
helm upgrade --install family-media-bot deploy/charts/family-media-bot \
  --namespace app -f deploy/values/gcp.yaml \
  --set-string image.tag='<git-sha>' --rollback-on-failure --wait

# AWS
helm upgrade --install family-media-bot deploy/charts/family-media-bot \
  --namespace app -f deploy/values/aws.yaml \
  --set-string image.tag='<git-sha>' --rollback-on-failure --wait
```

Stop any local polling process first: Telegram permits only one `getUpdates`
consumer per bot token.

## Verify

```bash
helm status family-media-bot -n app
kubectl -n app get deployments,pods,scaledobjects,hpa
kubectl -n app logs deployment/family-media-bot-web -f
```

At idle, the web tier is `1/1`, the worker is `0/0`, and the ScaledObject is
ready but inactive.
