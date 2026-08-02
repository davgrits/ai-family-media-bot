"""Application settings, sourced from environment variables (and an optional
`.env`). Every field has a local-dev default, so the app runs on a laptop with
no cloud account and no credentials."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Runtime ---
    run_mode: Literal["all", "web", "worker"] = "all"
    http_host: str = "0.0.0.0"
    http_port: int = 8080
    worker_concurrency: int = 1

    # --- Logging ---
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"

    # --- Swap points (interfaces) ---
    # Each port has a local fake for laptop runs and a GCP adapter for the
    # cluster. factory.py is the only place that reads these.
    queue: Literal["inmemory", "pubsub"] = "inmemory"
    story_provider: Literal["fake", "vertex"] = "fake"
    image_provider: Literal["fake", "vertex"] = "fake"
    storage: Literal["local", "gcs"] = "local"
    local_storage_dir: str = "./var/media"

    # --- Telegram ---
    telegram_bot_token: str = ""
    telegram_api_base: str = "https://api.telegram.org"
    telegram_webhook_secret: str = ""
    # Long-polling mode (local demo): the app pulls updates via getUpdates
    # instead of receiving webhooks. Only one polling instance may run per token.
    telegram_polling: bool = False

    # --- OpenTelemetry ---
    otel_exporter_otlp_endpoint: str = ""
    otel_service_name: str = "family-media-bot"

    # --- GCP / Vertex AI / GCS / Pub/Sub ---
    # These use Application Default Credentials. On GKE, Workload Identity
    # provides them; locally, use `gcloud auth application-default login`.
    gcp_project_id: str = ""
    gcp_region: str = "us-central1"
    gcs_bucket: str = ""
    pubsub_topic_name: str = ""
    pubsub_subscription_name: str = ""
    # Must match the subscription's dead_letter_policy.max_delivery_attempts
    # in infra/gcp/pubsub.tf. The worker uses it to recognise the last attempt
    # before the DLQ, so the chat is apologised to once, not once per retry.
    pubsub_max_delivery_attempts: int = 5
    vertex_text_location: str = "global"
    vertex_image_location: str = "us-central1"
    vertex_text_model_id: str = "gemini-3.5-flash"
    vertex_image_model_id: str = "gemini-2.5-flash-image"

    # --- Character registry ---
    # Mounted from a ConfigMap in the cluster; a repo path locally.
    characters_file: str = ""
    # Whether an absent registry is fatal. Explicit rather than inferred from
    # which providers are configured: `make run` and the kind smoke test both
    # legitimately have no registry mounted, while production must not start
    # without one and silently tell stories about nobody.
    characters_required: bool = False

    # --- Story shaping ---
    story_max_words: int = 220


@lru_cache
def get_settings() -> Settings:
    """Cached singleton so every module sees the same configuration."""
    return Settings()
