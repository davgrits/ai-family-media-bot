"""Application settings, sourced from environment variables (and an optional
`.env`). Every field has a local-dev default so the stub runs with no AWS."""

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
    queue: Literal["inmemory", "sqs"] = "inmemory"
    story_provider: Literal["fake", "bedrock"] = "fake"
    image_provider: Literal["fake", "bedrock"] = "fake"
    storage: Literal["local", "s3"] = "local"
    local_storage_dir: str = "./var/media"
    # Vision (photo → character card): empty = follow STORY_PROVIDER.
    vision_provider: Literal["", "fake", "bedrock"] = ""
    # Chat profiles (language + family cast): empty = follow STORAGE.
    profile_store: Literal["", "local", "s3"] = ""

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

    # --- AWS / Bedrock / S3 ---
    aws_region: str = "us-east-1"
    s3_bucket: str = ""
    sqs_queue_url: str = ""
    bedrock_text_model_id: str = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    # Nova Canvas went Legacy on Bedrock; Stability Image Core is the active
    # text-to-image model, but only in us-west-2 — hence the separate region.
    bedrock_image_model_id: str = "stability.stable-image-core-v1:1"
    bedrock_image_region: str = "us-west-2"

    # --- Story shaping ---
    story_max_words: int = 220


@lru_cache
def get_settings() -> Settings:
    """Cached singleton so every module sees the same configuration."""
    return Settings()
