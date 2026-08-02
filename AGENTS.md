# Repository Guidelines

## Project Structure & Module Organization

- `app/family_media_bot/` contains the Python 3.11 service. Keep cloud integrations behind interfaces in `ports/`, implementations in `adapters/`, and provider selection in `factory.py`.
- `app/tests/` contains `unittest` suites named `test_*.py`.
- `infra/aws/` and `infra/gcp/` are independent Terraform roots with separate state and provider configuration.
- `deploy/charts/family-media-bot/` is the shared AWS/GCP application chart. Provider inputs live in `deploy/values/`, cloud add-on values in `deploy/addons/`, and historical AWS Kustomize YAML in `deploy/legacy/`.
- `docs/` contains the frozen API contract, sample payloads, and operational runbooks. Generated local media belongs under `app/var/`.

## Build, Test, and Development Commands

- `make install` creates `app/.venv` and installs runtime dependencies.
- `make run` starts the local fake/in-memory implementation on port 8080.
- `make test` runs all Python unit tests.
- `make smoke` checks `/healthz` and submits `docs/sample-update.json`; run the service first.
- `make docker-build` builds the local container image.
- `make helm-lint` schema-validates and renders both app releases plus cloud add-ons.
- `terraform -chdir=infra/gcp fmt -check -recursive`, `validate`, and `plan` verify GCP infrastructure changes. Run equivalent commands from `infra/aws/` for AWS work.

## Coding Style & Naming Conventions

Use four-space Python indentation, type hints, `snake_case` for functions/modules, and `PascalCase` for classes. Ruff targets Python 3.11 with a 100-character line limit. Preserve the port/adapter boundary; business logic must not import cloud SDKs directly. Run `terraform fmt` for Terraform and use two-space YAML indentation. Keep Kubernetes workload logic shared in Helm; isolate only provider identity, adapters, and scalers in values/template branches.

Long-lived queue consumers use the provider-neutral `QueuePort.start(concurrency)` and
`QueuePort.close()` lifecycle. The worker owns that lifecycle. Streaming callbacks must hand work
off to asyncio, retain provider delivery handles through processing, and release buffered or
in-flight deliveries during bounded, idempotent shutdown.

## Testing Guidelines

Use standard-library `unittest`, including `IsolatedAsyncioTestCase` for async code and mocks for cloud APIs. Name files `test_<concern>.py` and methods `test_<behavior>`. Add regression tests for queue acknowledgement, IAM-sensitive readiness behavior, credential redaction, and provider fallbacks. Run `make test` and `make helm-lint` before submitting.

For streaming queues, also cover one-time subscription startup, concurrency-aligned flow control,
dequeue timeouts, malformed-message nacks, and shutdown release of buffered/in-flight deliveries.

## Commit & Pull Request Guidelines

History is small and uses a descriptive subject rather than a strict Conventional Commits format. Write concise, imperative subjects that summarize the outcome, and keep unrelated cloud changes separate. Pull requests should explain architecture or cost trade-offs, list validation commands, link relevant issues, and include rendered manifest/plan summaries. Add screenshots only for user-visible Telegram or dashboard changes.

## Security & Configuration

Never commit `.env`, Terraform state/plans, service-account keys, or bot tokens. Use ADC/Workload Identity for GCP and IRSA for AWS. Do not deploy `deploy/legacy/` beside Helm. If a Telegram token appears in logs, rotate it immediately and update the Kubernetes Secret.
