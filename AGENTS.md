# Repository Guidelines

## Project Structure & Module Organization

- `app/family_media_bot/` contains the Python 3.11 service. Keep cloud integrations behind interfaces in `ports/`, implementations in `adapters/`, and provider selection in `factory.py`.
- `app/tests/` contains `unittest` suites named `test_*.py`.
- `infra/gcp/` is the single Terraform root. It owns its own state and provider configuration.
- `deploy/charts/family-media-bot/` is the application chart. Deployment inputs live in `deploy/values/prod.yaml`; the image tag is supplied by CI and never committed.
- `docs/` contains the frozen API contract, sample payloads, and operational runbooks. Generated local media belongs under `app/var/`.

## Build, Test, and Development Commands

- `make install` creates `app/.venv` and installs runtime dependencies; `make install-dev` adds the pinned linter.
- `make run` starts the local fake/in-memory implementation on port 8080.
- `make test` runs all Python unit tests.
- `make lint` runs `ruff check` plus `ruff format --check` — the same two commands CI runs. `make fmt` applies them.
- `make smoke` checks `/healthz` and submits `docs/sample-update.json`; run the service first.
- `make docker-build` builds the local container image.
- `make helm-lint` schema-validates and renders the release.
- `terraform -chdir=infra/gcp fmt -check -recursive`, `validate`, and `plan` verify infrastructure changes.

## Coding Style & Naming Conventions

Use four-space Python indentation, type hints, `snake_case` for functions/modules, and `PascalCase` for classes. Ruff is pinned in `app/requirements-dev.txt` and configured in `app/pyproject.toml`: Python 3.11 target, 100-character limit, `E,W,F,I` selected — note `E501` only applies because it is selected explicitly, since ruff's default rule set omits it. Preserve the port/adapter boundary; business logic must not import cloud SDKs directly. Run `terraform fmt` for Terraform and use two-space YAML indentation.

Adapters must not substitute a fabricated value for a provider failure. Returning a placeholder image, a partial story, or a $0 cost turns a failure into a plausible-looking success that the pipeline acks and nobody notices. Raise instead, and let the queue retry.

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

Never commit `.env`, Terraform state/plans, service-account keys, or bot tokens. Credentials come from Application Default Credentials locally and Workload Identity in the cluster; no service-account key file exists anywhere in this project. If a Telegram token appears in logs, rotate it immediately and update the Kubernetes Secret.

No personal data in log records. That includes `chat_id`, raw message text, message payloads, and character names — the characters are real children.
