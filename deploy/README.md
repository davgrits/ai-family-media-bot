# Kubernetes deployment

Helm is the only deployment path.

```text
charts/family-media-bot/  web, worker, identity, config
values/prod.yaml          the live GCP environment's inputs
examples/                 how to create the Telegram secret (never a committed value)
```

Terraform owns the cloud resources; Helm consumes their identifiers. Everything
in `values/prod.yaml` comes from `terraform -chdir=infra/gcp output`.

The image tag is deliberately **not** in `values/prod.yaml`. CI supplies it at
upgrade time (`--set image.tag=$GITHUB_SHA`), so a release is always traceable to
a commit and a committed tag cannot drift from what is actually running.

Validate with `make helm-lint`. See the
[chart guide](charts/family-media-bot/README.md) for install and verification
commands.
