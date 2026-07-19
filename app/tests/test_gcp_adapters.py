from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from google.api_core.exceptions import DeadlineExceeded

from family_media_bot.adapters.image_vertex import VertexImageProvider
from family_media_bot.adapters.queue_pubsub import PubSubQueue
from family_media_bot.adapters.storage_gcs import GcsStorage
from family_media_bot.adapters.story_common import split_illustration_hint
from family_media_bot.adapters.story_vertex import VertexStoryProvider
from family_media_bot.config import Settings
from family_media_bot.models import Mode, new_job


class StoryCommonTests(unittest.TestCase):
    def test_split_illustration_hint(self) -> None:
        story, hint = split_illustration_hint(
            "Первая строка.\nВторая строка.\nILLUSTRATION: a moonlit lighthouse"
        )

        self.assertEqual(story, "Первая строка.\nВторая строка.")
        self.assertEqual(hint, "a moonlit lighthouse")


class PubSubQueueTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        publisher_patch = patch(
            "family_media_bot.adapters.queue_pubsub.pubsub_v1.PublisherClient"
        )
        subscriber_patch = patch(
            "family_media_bot.adapters.queue_pubsub.pubsub_v1.SubscriberClient"
        )
        self.addCleanup(publisher_patch.stop)
        self.addCleanup(subscriber_patch.stop)
        self.publisher = publisher_patch.start().return_value
        self.subscriber = subscriber_patch.start().return_value
        self.queue = PubSubQueue("demo-project", "jobs", "jobs")

    async def test_publish_and_ack_only_after_processing(self) -> None:
        job = new_job(123, Mode.CUSTOM, "dragon")
        publish_future = MagicMock()
        self.publisher.publish.return_value = publish_future

        await self.queue.enqueue(job)

        self.publisher.publish.assert_called_once_with(
            "projects/demo-project/topics/jobs",
            job.model_dump_json().encode("utf-8"),
        )
        publish_future.result.assert_called_once()

        received = SimpleNamespace(
            ack_id="ack-1",
            message=SimpleNamespace(data=job.model_dump_json().encode("utf-8")),
        )
        self.subscriber.pull.return_value = SimpleNamespace(received_messages=[received])

        delivery = await self.queue.dequeue()

        self.assertIsNotNone(delivery)
        self.assertEqual(delivery.job, job)
        self.subscriber.acknowledge.assert_not_called()

        await self.queue.ack(delivery)
        self.subscriber.acknowledge.assert_called_once_with(
            request={
                "subscription": "projects/demo-project/subscriptions/jobs",
                "ack_ids": ["ack-1"],
            }
        )

    async def test_idle_deadline_returns_none(self) -> None:
        self.subscriber.pull.side_effect = DeadlineExceeded("idle")
        self.assertIsNone(await self.queue.dequeue(timeout=1.0))

    async def test_nack_releases_delivery(self) -> None:
        job = new_job(123, Mode.CUSTOM, "dragon")
        received = SimpleNamespace(
            ack_id="ack-2",
            message=SimpleNamespace(data=job.model_dump_json().encode("utf-8")),
        )
        self.subscriber.pull.return_value = SimpleNamespace(received_messages=[received])
        delivery = await self.queue.dequeue()

        await self.queue.nack(delivery)

        self.subscriber.modify_ack_deadline.assert_called_once_with(
            request={
                "subscription": "projects/demo-project/subscriptions/jobs",
                "ack_ids": ["ack-2"],
                "ack_deadline_seconds": 0,
            }
        )


class GcsStorageTests(unittest.IsolatedAsyncioTestCase):
    @patch("family_media_bot.adapters.storage_gcs.storage.Client")
    async def test_save_and_readiness_use_object_permissions(self, client_class) -> None:
        client = client_class.return_value
        blob = client.bucket.return_value.blob.return_value
        client.list_blobs.return_value = iter([])
        storage_adapter = GcsStorage("media-bucket", "demo-project")

        uri = await storage_adapter.save("job-1", b"story", "text/plain")

        self.assertEqual(uri, "gs://media-bucket/generated/job-1.txt")
        blob.upload_from_string.assert_called_once_with(b"story", content_type="text/plain")
        self.assertTrue(await storage_adapter.check_ready())
        client.list_blobs.assert_called_once_with("media-bucket", max_results=1)


class VertexAdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.settings = Settings(gcp_project_id="demo-project")

    @patch("family_media_bot.adapters.story_vertex.genai.Client")
    async def test_story_generation(self, client_class) -> None:
        client = client_class.return_value
        client.models.generate_content.return_value = SimpleNamespace(
            text="Жила-была звезда.\nILLUSTRATION: a friendly star",
            usage_metadata=SimpleNamespace(
                prompt_token_count=12,
                candidates_token_count=34,
            ),
        )
        provider = VertexStoryProvider(self.settings)

        result = await provider.generate(Mode.FAIRYTALE, "prompt")

        self.assertEqual(result.text, "Жила-была звезда.")
        self.assertEqual(result.illustration_hint, "a friendly star")
        self.assertEqual(result.model_id, "gemini-3.5-flash")
        self.assertEqual(result.cost_usd, 0.000324)
        self.assertEqual(result.tokens_in, 12)
        self.assertEqual(result.tokens_out, 34)
        client_class.assert_called_once_with(
            vertexai=True,
            project="demo-project",
            location="global",
        )

    @patch("family_media_bot.adapters.image_vertex.genai.Client")
    async def test_gemini_image_generation_uses_production_defaults(
        self, client_class
    ) -> None:
        client = client_class.return_value
        client.models.generate_content.return_value = SimpleNamespace(
            candidates=[
                SimpleNamespace(
                    content=SimpleNamespace(
                        parts=[
                            SimpleNamespace(
                                inline_data=SimpleNamespace(
                                    data=b"png",
                                    mime_type="image/png",
                                )
                            )
                        ]
                    )
                )
            ]
        )
        provider = VertexImageProvider(self.settings)

        result = await provider.generate("a friendly star")

        self.assertEqual(result.png_bytes, b"png")
        self.assertEqual(result.model_id, "gemini-2.5-flash-image")
        self.assertEqual(result.cost_usd, 0.039)
        self.assertEqual(result.content_type, "image/png")
        client_class.assert_called_once_with(
            vertexai=True,
            project="demo-project",
            location="us-central1",
        )
        _, kwargs = client.models.generate_content.call_args
        self.assertEqual(kwargs["model"], "gemini-2.5-flash-image")
        self.assertEqual(kwargs["config"].response_modalities, ["IMAGE"])

    @patch("family_media_bot.adapters.image_vertex.genai.Client")
    async def test_imagen_generation_remains_configurable(self, client_class) -> None:
        client = client_class.return_value
        client.models.generate_images.return_value = SimpleNamespace(
            generated_images=[
                SimpleNamespace(
                    image=SimpleNamespace(image_bytes=b"png", mime_type="image/png")
                )
            ]
        )
        settings = Settings(
            gcp_project_id="demo-project",
            vertex_image_model_id="imagen-4.0-generate-001",
        )
        provider = VertexImageProvider(settings)

        result = await provider.generate("a friendly star")

        self.assertEqual(result.png_bytes, b"png")
        self.assertEqual(result.model_id, "imagen-4.0-generate-001")
        self.assertEqual(result.cost_usd, 0.04)
        self.assertEqual(result.content_type, "image/png")
        client_class.assert_called_once_with(
            vertexai=True,
            project="demo-project",
            location="us-central1",
        )
        _, kwargs = client.models.generate_images.call_args
        self.assertEqual(kwargs["model"], "imagen-4.0-generate-001")
        self.assertEqual(kwargs["config"].image_size, "1K")
        self.assertEqual(kwargs["config"].output_mime_type, "image/png")


if __name__ == "__main__":
    unittest.main()
