# Helm chart

The application chart for GKE. Templates own the web and worker Deployments, the
Kubernetes service account, and the environment ConfigMap.

Terraform owns cloud resources and identity bindings; this chart consumes their
identifiers. The image repository is always the complete Artifact Registry path
without a tag.

## Validate

From the repository root:

```bash
make helm-lint

# Inspect the rendered manifests in detail.
helm template family-media-bot deploy/charts/family-media-bot \
  --namespace app -f deploy/values/prod.yaml --set image.tag=ci
```

`values.schema.json` rejects a release with no image coordinates, an adapter
value the application would refuse at startup, or a worker `replicaCount` below
1. Failing in `helm` is cheaper than failing in a CrashLoopBackOff.

## Install prerequisites

Create the Telegram token out of band, so its value never enters a values file
or the Helm release:

```bash
kubectl create namespace app --dry-run=client -o yaml | kubectl apply -f -
kubectl -n app create secret generic telegram-bot-token \
  --from-literal=TELEGRAM_BOT_TOKEN='<token from BotFather>'
```

## Deploy

The tag is supplied at upgrade time rather than committed, so a release is always
traceable to a commit:

```bash
helm upgrade --install family-media-bot deploy/charts/family-media-bot \
  --namespace app -f deploy/values/prod.yaml \
  --set-string image.tag='<git-sha>' --rollback-on-failure --wait --timeout 5m
```

`--rollback-on-failure` is Helm 4's replacement for `--atomic`: a release that
fails to become ready is rolled back rather than left half-applied.

Stop any local polling process first — Telegram permits only one `getUpdates`
consumer per bot token.

## Verify

```bash
helm status family-media-bot -n app
kubectl -n app get deployments,pods
kubectl -n app logs deployment/family-media-bot-web -f
kubectl -n app logs deployment/family-media-bot-worker -f
```

At idle both tiers are `1/1`. The worker is deliberately warm rather than scaled
to zero: activating from zero on Pub/Sub backlog measured ~4m43s, because the
backlog metric samples on a ~60s interval and can gap for minutes.
