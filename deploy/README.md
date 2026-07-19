# Kubernetes deployments

Helm is the canonical application deployment for both clouds:

```text
charts/family-media-bot/  shared web, worker, identity, config, and KEDA objects
values/aws.yaml           EKS/IRSA + SQS/S3/Bedrock configuration
values/gcp.yaml           GKE Workload Identity + Pub/Sub/GCS/Vertex configuration
addons/aws/               AWS KEDA and cluster-autoscaler Helm values
addons/gcp/               GCP KEDA Helm values
examples/                 provider-neutral secret examples
legacy/kustomize/aws/     historical manifests; do not use for new deployments
```

The chart shares everything Kubernetes-native and branches only where the cloud
API differs: workload identity annotations, environment configuration, and the
KEDA scaler. Terraform owns cloud resources; Helm consumes their identifiers.

Validate both deployment paths with `make helm-lint`. See the
[chart guide](charts/family-media-bot/README.md) for install and verification
commands.
