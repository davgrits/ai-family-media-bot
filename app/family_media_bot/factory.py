"""Composition root. Builds the concrete adapter for each port based on Settings.
This is the *only* place that knows which implementation is selected — the rest
of the app talks to interfaces."""

from __future__ import annotations

from .config import Settings
from .ports.image import ImageProvider
from .ports.queue import QueuePort
from .ports.storage import StoragePort
from .ports.story import StoryProvider
from .telegram import TelegramClient


def build_queue(s: Settings) -> QueuePort:
    if s.queue == "inmemory":
        from .adapters.queue_inmemory import InMemoryQueue

        return InMemoryQueue()
    if s.queue == "sqs":
        from .adapters.queue_sqs import SqsQueue

        return SqsQueue(s.sqs_queue_url, s.aws_region)
    if s.queue == "pubsub":
        from .adapters.queue_pubsub import PubSubQueue

        return PubSubQueue(s.gcp_project_id, s.pubsub_topic_name, s.pubsub_subscription_name)
    raise ValueError(f"unknown QUEUE={s.queue!r}")


def build_story(s: Settings) -> StoryProvider:
    if s.story_provider == "fake":
        from .adapters.story_fake import FakeStoryProvider

        return FakeStoryProvider()
    if s.story_provider == "bedrock":
        from .adapters.story_bedrock import BedrockStoryProvider

        return BedrockStoryProvider(s)
    if s.story_provider == "vertex":
        from .adapters.story_vertex import VertexStoryProvider

        return VertexStoryProvider(s)
    raise ValueError(f"unknown STORY_PROVIDER={s.story_provider!r}")


def build_image(s: Settings) -> ImageProvider:
    if s.image_provider == "fake":
        from .adapters.image_fake import FakeImageProvider

        return FakeImageProvider()
    if s.image_provider == "bedrock":
        from .adapters.image_bedrock import BedrockImageProvider

        return BedrockImageProvider(s)
    if s.image_provider == "vertex":
        from .adapters.image_vertex import VertexImageProvider

        return VertexImageProvider(s)
    raise ValueError(f"unknown IMAGE_PROVIDER={s.image_provider!r}")


def build_storage(s: Settings) -> StoragePort:
    if s.storage == "local":
        from .adapters.storage_localdir import LocalDirStorage

        return LocalDirStorage(s.local_storage_dir)
    if s.storage == "s3":
        from .adapters.storage_s3 import S3Storage

        return S3Storage(s.s3_bucket, s.aws_region)
    if s.storage == "gcs":
        from .adapters.storage_gcs import GcsStorage

        return GcsStorage(s.gcs_bucket, s.gcp_project_id)
    raise ValueError(f"unknown STORAGE={s.storage!r}")


def build_telegram(s: Settings) -> TelegramClient:
    return TelegramClient(s.telegram_bot_token, s.telegram_api_base)
