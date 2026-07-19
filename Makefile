.PHONY: help venv install test helm-lint helm-lint-aws helm-lint-gcp helm-lint-addons run demo docker-build docker-run smoke clean

APP_DIR := app
IMAGE   := family-media-bot:dev
PORT    := 8080

help:
	@echo "Targets:"
	@echo "  make venv          Create app/.venv"
	@echo "  make install       Install runtime deps into the venv"
	@echo "  make test          Run the Python unit tests"
	@echo "  make helm-lint     Lint and render the AWS and GCP Helm releases"
	@echo "  make run           Run the service locally (fake providers, no AWS)"
	@echo "  make demo          Run the live demo (Telegram polling + Bedrock + SQS + S3; needs app/.env)"
	@echo "  make docker-build  Build the container image"
	@echo "  make docker-run    Run the container (maps :$(PORT))"
	@echo "  make smoke         Hit /healthz and POST a sample Telegram update"

venv:
	cd $(APP_DIR) && python3 -m venv .venv

install: venv
	cd $(APP_DIR) && ./.venv/bin/pip install -U pip && ./.venv/bin/pip install -r requirements.txt

test:
	cd $(APP_DIR) && ./.venv/bin/python -m unittest discover -s tests -v

helm-lint: helm-lint-aws helm-lint-gcp helm-lint-addons

helm-lint-aws:
	helm lint deploy/charts/family-media-bot \
		-f deploy/values/aws.yaml
	helm template family-media-bot-aws deploy/charts/family-media-bot \
		--namespace app \
		-f deploy/values/aws.yaml >/dev/null

helm-lint-gcp:
	helm lint deploy/charts/family-media-bot \
		-f deploy/values/gcp.yaml
	helm template family-media-bot-gcp deploy/charts/family-media-bot \
		--namespace app \
		-f deploy/values/gcp.yaml >/dev/null

helm-lint-addons:
	helm template keda-gcp kedacore/keda --version 2.20.1 \
		--namespace keda -f deploy/addons/gcp/keda-values.yaml >/dev/null
	helm template keda-aws kedacore/keda --version 2.20.1 \
		--namespace keda -f deploy/addons/aws/keda-values.yaml >/dev/null
	helm template cluster-autoscaler autoscaler/cluster-autoscaler \
		--namespace kube-system \
		-f deploy/addons/aws/cluster-autoscaler-values.yaml >/dev/null

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
