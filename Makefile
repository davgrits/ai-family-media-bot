.PHONY: help venv install install-dev test run demo docker-build docker-run smoke clean

APP_DIR := app
IMAGE   := family-media-bot:dev
PORT    := 8080

help:
	@echo "Targets:"
	@echo "  make venv          Create app/.venv"
	@echo "  make install       Install runtime deps into the venv"
	@echo "  make install-dev   Install runtime + test deps"
	@echo "  make test          Run the unit tests (pytest)"
	@echo "  make run           Run the service locally (fake providers, no AWS)"
	@echo "  make demo          Run the live demo (Telegram polling + Bedrock + SQS + S3; needs app/.env)"
	@echo "  make docker-build  Build the container image"
	@echo "  make docker-run    Run the container (maps :$(PORT))"
	@echo "  make smoke         Hit /healthz and POST a sample Telegram update"

venv:
	cd $(APP_DIR) && python3 -m venv .venv

install: venv
	cd $(APP_DIR) && ./.venv/bin/pip install -U pip && ./.venv/bin/pip install -r requirements.txt

install-dev: install
	cd $(APP_DIR) && ./.venv/bin/pip install -r requirements-dev.txt

test:
	cd $(APP_DIR) && ./.venv/bin/python -m pytest -q

run:
	cd $(APP_DIR) && ./.venv/bin/python -m family_media_bot

# One command for the live demo. Config comes from app/.env (gitignored):
# real providers (Bedrock/SQS/S3), Telegram long polling. See README "Local demo".
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
