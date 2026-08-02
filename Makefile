.PHONY: help venv install install-dev test lint fmt helm-lint run demo docker-build docker-run smoke clean

APP_DIR := app
IMAGE   := family-media-bot:dev
PORT    := 8080
CHART   := deploy/charts/family-media-bot
VALUES  := deploy/values/prod.yaml

help:
	@echo "Targets:"
	@echo "  make venv          Create app/.venv"
	@echo "  make install       Install runtime deps into the venv"
	@echo "  make install-dev   Runtime deps + the pinned linter"
	@echo "  make test          Run the Python unit tests"
	@echo "  make lint          ruff check + format check (what CI runs)"
	@echo "  make fmt           Apply ruff formatting and safe fixes"
	@echo "  make helm-lint     Lint and render the Helm release"
	@echo "  make run           Run the service locally (fake providers, no cloud account)"
	@echo "  make demo          Run against real GCP (Telegram polling; needs app/.env)"
	@echo "  make docker-build  Build the container image"
	@echo "  make docker-run    Run the container (maps :$(PORT))"
	@echo "  make smoke         Hit /healthz and POST a sample Telegram update"

venv:
	cd $(APP_DIR) && python3 -m venv .venv

install: venv
	cd $(APP_DIR) && ./.venv/bin/pip install -U pip && ./.venv/bin/pip install -r requirements.txt

install-dev: venv
	cd $(APP_DIR) && ./.venv/bin/pip install -U pip && ./.venv/bin/pip install -r requirements-dev.txt

test:
	cd $(APP_DIR) && ./.venv/bin/python -m unittest discover -s tests -v

lint:
	cd $(APP_DIR) && ./.venv/bin/ruff check .
	cd $(APP_DIR) && ./.venv/bin/ruff format --check .

# Format first: ruff format resolves some long lines that `check --fix` cannot,
# and `check` exits non-zero on anything left for a human, which must not stop
# the formatter from having run.
fmt:
	cd $(APP_DIR) && ./.venv/bin/ruff format .
	cd $(APP_DIR) && ./.venv/bin/ruff check --fix .

# --strict turns chart warnings into failures; image.tag is normally supplied by
# CI, so a placeholder is passed here to satisfy the schema.
helm-lint:
	helm lint $(CHART) -f $(VALUES) --set image.tag=ci --strict
	helm template family-media-bot $(CHART) --namespace app \
		-f $(VALUES) --set image.tag=ci >/dev/null

run:
	cd $(APP_DIR) && ./.venv/bin/python -m family_media_bot

# One command for the live demo. Config comes from app/.env (gitignored): real
# Vertex AI + GCS, Telegram long polling. See README "Local demo".
demo:
	@test -f $(APP_DIR)/.env || { echo "Missing app/.env — see README section 'Local demo'"; exit 1; }
	cd $(APP_DIR) && ./.venv/bin/python -m family_media_bot

docker-build:
	docker build -t $(IMAGE) $(APP_DIR)

docker-run:
	docker run --rm -p $(PORT):8080 --env-file .env $(IMAGE)

# Requires the server to be running (make run) in another shell.
smoke:
	@echo "== /healthz =="
	curl -fsS localhost:$(PORT)/healthz; echo
	@echo "== POST /webhook (sample /custom update) =="
	curl -fsS -X POST localhost:$(PORT)/webhook \
		-H 'content-type: application/json' \
		-d @docs/sample-update.json; echo
	@echo "== watch the server logs for 'job completed' + check app/var/media/generated/ =="

clean:
	rm -rf $(APP_DIR)/var $(APP_DIR)/.venv
