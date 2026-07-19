# Legacy AWS Kustomize deployment

These files preserve the original EKS deployment for history and comparison.
They are not maintained as a second production path. Use the shared Helm chart
with `deploy/values/aws.yaml` for AWS deployments.

Do not apply this directory alongside the Helm release: both define resources
with the same names in namespace `app`.
