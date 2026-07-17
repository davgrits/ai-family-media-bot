"""Composition root. Builds the concrete adapter for each port based on Settings.
This is the *only* place that knows which implementation is selected — the rest
of the app talks to interfaces."""

from __future__ import annotations

from .config import Settings
from .ports.image import ImageProvider
from .ports.profiles import ProfileStore
from .ports.queue import QueuePort
from .ports.storage import StoragePort
from .ports.story import StoryProvider
from .ports.vision import VisionProvider
from .telegram import TelegramClient


def build_queue(s: Settings) -> QueuePort:
    if s.queue == "inmemory":
        from .adapters.queue_inmemory import InMemoryQueue

        return InMemoryQueue()
    if s.queue == "sqs":
        from .adapters.queue_sqs import SqsQueue

        return SqsQueue(s.sqs_queue_url, s.aws_region)
    raise ValueError(f"unknown QUEUE={s.queue!r}")


def build_story(s: Settings) -> StoryProvider:
    if s.story_provider == "fake":
        from .adapters.story_fake import FakeStoryProvider

        return FakeStoryProvider()
    if s.story_provider == "bedrock":
        from .adapters.story_bedrock import BedrockStoryProvider

        return BedrockStoryProvider(s)
    raise ValueError(f"unknown STORY_PROVIDER={s.story_provider!r}")


def build_image(s: Settings) -> ImageProvider:
    if s.image_provider == "fake":
        from .adapters.image_fake import FakeImageProvider

        return FakeImageProvider()
    if s.image_provider == "bedrock":
        from .adapters.image_bedrock import BedrockImageProvider

        return BedrockImageProvider(s)
    raise ValueError(f"unknown IMAGE_PROVIDER={s.image_provider!r}")


def build_storage(s: Settings) -> StoragePort:
    if s.storage == "local":
        from .adapters.storage_localdir import LocalDirStorage

        return LocalDirStorage(s.local_storage_dir)
    if s.storage == "s3":
        from .adapters.storage_s3 import S3Storage

        return S3Storage(s.s3_bucket, s.aws_region)
    raise ValueError(f"unknown STORAGE={s.storage!r}")


def build_vision(s: Settings) -> VisionProvider:
    # Unset = follow the story provider: fake locally, Claude-on-Bedrock in prod.
    provider = s.vision_provider or s.story_provider
    if provider == "fake":
        from .adapters.vision_fake import FakeVisionProvider

        return FakeVisionProvider()
    if provider == "bedrock":
        from .adapters.vision_bedrock import BedrockVisionProvider

        return BedrockVisionProvider(s)
    raise ValueError(f"unknown VISION_PROVIDER={provider!r}")


def build_profiles(s: Settings) -> ProfileStore:
    # Unset = follow the storage backend: local dir locally, S3 in prod.
    store = s.profile_store or s.storage
    if store == "local":
        from .adapters.profiles_localdir import LocalDirProfileStore

        return LocalDirProfileStore(s.local_storage_dir)
    if store == "s3":
        from .adapters.profiles_s3 import S3ProfileStore

        return S3ProfileStore(s.s3_bucket, s.aws_region)
    raise ValueError(f"unknown PROFILE_STORE={store!r}")


def build_telegram(s: Settings) -> TelegramClient:
    return TelegramClient(s.telegram_bot_token, s.telegram_api_base)
