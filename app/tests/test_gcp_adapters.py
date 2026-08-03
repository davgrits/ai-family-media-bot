from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from google.genai.types import FinishReason, HttpOptions, ThinkingLevel

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
        publisher_patch = patch("family_media_bot.adapters.queue_pubsub.pubsub_v1.PublisherClient")
        subscriber_patch = patch(
            "family_media_bot.adapters.queue_pubsub.pubsub_v1.SubscriberClient"
        )
        self.addCleanup(publisher_patch.stop)
        self.addCleanup(subscriber_patch.stop)
        self.publisher = publisher_patch.start().return_value
        self.subscriber = subscriber_patch.start().return_value
        self.streaming_future = MagicMock()
        self.streaming_future.cancelled.return_value = True
        self.subscriber.subscribe.return_value = self.streaming_future
        self.queue = PubSubQueue("demo-project", "jobs", "jobs")

    async def asyncTearDown(self) -> None:
        await self.queue.close()

    async def _start(self, concurrency: int = 1):
        await self.queue.start(concurrency)
        return self.subscriber.subscribe.call_args.kwargs["callback"]

    @staticmethod
    def _message(data: bytes, ack_id: str = "ack-1", message_id: str = "message-1"):
        message = MagicMock()
        message.data = data
        message.ack_id = ack_id
        message.message_id = message_id
        return message

    async def _deliver(self, message: MagicMock) -> None:
        callback = self.subscriber.subscribe.call_args.kwargs["callback"]
        await asyncio.to_thread(callback, message)
        await asyncio.sleep(0)

    async def test_streaming_subscription_starts_exactly_once(self) -> None:
        await self.queue.start(concurrency=3)
        await self.queue.start(concurrency=8)

        self.subscriber.subscribe.assert_called_once()
        kwargs = self.subscriber.subscribe.call_args.kwargs
        self.assertEqual(
            self.subscriber.subscribe.call_args.args,
            ("projects/demo-project/subscriptions/jobs",),
        )
        self.assertEqual(kwargs["flow_control"].max_messages, 3)
        self.assertTrue(kwargs["await_callbacks_on_shutdown"])

    async def test_publish_and_ack_original_message_only_after_processing(self) -> None:
        job = new_job(123, Mode.CUSTOM, "dragon")
        publish_future = MagicMock()
        self.publisher.publish.return_value = publish_future
        await self._start()

        await self.queue.enqueue(job)

        self.publisher.publish.assert_called_once_with(
            "projects/demo-project/topics/jobs",
            job.model_dump_json().encode("utf-8"),
        )
        publish_future.result.assert_called_once()

        message = self._message(job.model_dump_json().encode("utf-8"))
        await self._deliver(message)
        delivery = await self.queue.dequeue()

        self.assertIsNotNone(delivery)
        self.assertEqual(delivery.job, job)
        message.ack.assert_not_called()

        await self.queue.ack(delivery)
        message.ack.assert_called_once_with()
        message.nack.assert_not_called()

    async def test_dequeue_timeout_does_not_cancel_or_recreate_stream(self) -> None:
        await self._start()

        self.assertIsNone(await self.queue.dequeue(timeout=0.01))
        self.assertIsNone(await self.queue.dequeue(timeout=0.01))

        self.subscriber.subscribe.assert_called_once()
        self.streaming_future.cancel.assert_not_called()

    async def test_nack_releases_original_message(self) -> None:
        job = new_job(123, Mode.CUSTOM, "dragon")
        await self._start()
        message = self._message(
            job.model_dump_json().encode("utf-8"),
            ack_id="ack-2",
            message_id="message-2",
        )
        await self._deliver(message)
        delivery = await self.queue.dequeue()

        await self.queue.nack(delivery)

        message.nack.assert_called_once_with()
        message.ack.assert_not_called()

    async def test_invalid_json_and_invalid_jobs_are_nacked(self) -> None:
        await self._start()
        invalid_json = self._message(b"{", ack_id="bad-json", message_id="bad-json")
        invalid_job = self._message(
            b'{"job_id":"job-1"}',
            ack_id="bad-job",
            message_id="bad-job",
        )

        await self._deliver(invalid_json)
        await self._deliver(invalid_job)

        invalid_json.nack.assert_called_once_with()
        invalid_job.nack.assert_called_once_with()
        self.assertIsNone(await self.queue.dequeue(timeout=0.01))

    async def test_concurrency_sets_flow_control_and_bounds_buffer(self) -> None:
        job_one = new_job(123, Mode.CUSTOM, "dragon")
        job_two = new_job(456, Mode.RANDOM, "forest")
        await self._start(concurrency=2)
        message_one = self._message(
            job_one.model_dump_json().encode("utf-8"),
            ack_id="ack-1",
            message_id="message-1",
        )
        message_two = self._message(
            job_two.model_dump_json().encode("utf-8"),
            ack_id="ack-2",
            message_id="message-2",
        )
        await self._deliver(message_one)
        await self._deliver(message_two)

        first, second = await asyncio.gather(
            self.queue.dequeue(timeout=0.1),
            self.queue.dequeue(timeout=0.1),
        )

        self.assertEqual(
            {first.job.job_id, second.job.job_id},
            {job_one.job_id, job_two.job_id},
        )
        flow_control = self.subscriber.subscribe.call_args.kwargs["flow_control"]
        self.assertEqual(flow_control.max_messages, 2)
        await self.queue.ack(first)
        await self.queue.ack(second)

    async def test_shutdown_cancels_closes_and_releases_buffered_and_inflight(self) -> None:
        job_one = new_job(123, Mode.CUSTOM, "dragon")
        job_two = new_job(456, Mode.RANDOM, "forest")
        await self._start(concurrency=2)
        message_one = self._message(
            job_one.model_dump_json().encode("utf-8"),
            ack_id="ack-1",
            message_id="message-1",
        )
        message_two = self._message(
            job_two.model_dump_json().encode("utf-8"),
            ack_id="ack-2",
            message_id="message-2",
        )
        await self._deliver(message_one)
        await self._deliver(message_two)
        self.assertIsNotNone(await self.queue.dequeue(timeout=0.1))

        await self.queue.close()
        await self.queue.close()

        message_one.nack.assert_called_once_with()
        message_two.nack.assert_called_once_with()
        self.streaming_future.cancel.assert_called_once_with()
        self.streaming_future.result.assert_called_once()
        self.subscriber.close.assert_called_once_with()
        self.publisher.stop.assert_called_once_with()


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
            http_options=HttpOptions(timeout=40_000),
        )

    @patch("family_media_bot.adapters.story_vertex.genai.Client")
    async def test_story_budget_covers_thinking_and_non_latin_tokens(self, client_class) -> None:
        client = client_class.return_value
        client.models.generate_content.return_value = SimpleNamespace(
            text="Жила-была звезда.",
            usage_metadata=SimpleNamespace(prompt_token_count=1, candidates_token_count=1),
        )
        provider = VertexStoryProvider(self.settings)

        await provider.generate(Mode.FAIRYTALE, "prompt")

        config = client.models.generate_content.call_args.kwargs["config"]
        # 220 words of Cyrillic plus a reasoning reserve. The previous 1024
        # budget was consumed by thinking, truncating the story to a fragment.
        self.assertEqual(config.max_output_tokens, 220 * 4 + 2048)
        self.assertEqual(config.thinking_config.thinking_level, ThinkingLevel.MINIMAL)

    @patch("family_media_bot.adapters.story_vertex.genai.Client")
    async def test_story_truncated_at_token_ceiling_is_rejected(self, client_class) -> None:
        client = client_class.return_value
        client.models.generate_content.return_value = SimpleNamespace(
            text="Жила-была зв",
            candidates=[SimpleNamespace(finish_reason=FinishReason.MAX_TOKENS)],
            usage_metadata=SimpleNamespace(prompt_token_count=12, candidates_token_count=4),
        )
        provider = VertexStoryProvider(self.settings)

        # A half-sentence must fail the job and be retried, never delivered.
        with self.assertRaisesRegex(RuntimeError, "MAX_TOKENS"):
            await provider.generate(Mode.FAIRYTALE, "prompt")

    @patch("family_media_bot.adapters.story_vertex.genai.Client")
    async def test_only_a_clean_stop_is_accepted(self, client_class) -> None:
        client = client_class.return_value
        provider = VertexStoryProvider(self.settings)

        # Rejecting only MAX_TOKENS would still let these ship whatever partial
        # text the model emitted before it stopped.
        for reason in (
            FinishReason.SAFETY,
            FinishReason.RECITATION,
            FinishReason.BLOCKLIST,
            FinishReason.PROHIBITED_CONTENT,
        ):
            with self.subTest(finish_reason=reason):
                client.models.generate_content.return_value = SimpleNamespace(
                    text="Жила-была звезда, и вдруг",
                    candidates=[SimpleNamespace(finish_reason=reason)],
                    usage_metadata=SimpleNamespace(prompt_token_count=12, candidates_token_count=6),
                )
                with self.assertRaises(RuntimeError):
                    await provider.generate(Mode.FAIRYTALE, "prompt")

    @patch("family_media_bot.adapters.story_vertex.genai.Client")
    async def test_all_budget_spent_on_thinking_reports_the_real_cause(self, client_class) -> None:
        client = client_class.return_value
        # The canonical failure: thinking consumed the whole budget and no
        # story was emitted. Checking emptiness first would misreport this as
        # "empty response" and hide the token ceiling.
        client.models.generate_content.return_value = SimpleNamespace(
            text="",
            candidates=[SimpleNamespace(finish_reason=FinishReason.MAX_TOKENS)],
            usage_metadata=SimpleNamespace(
                prompt_token_count=12,
                candidates_token_count=0,
                thoughts_token_count=2928,
            ),
        )
        provider = VertexStoryProvider(self.settings)

        with self.assertRaisesRegex(RuntimeError, "MAX_TOKENS"):
            await provider.generate(Mode.FAIRYTALE, "prompt")

    @patch("family_media_bot.adapters.story_vertex.genai.Client")
    async def test_thinking_tokens_are_billed_at_the_output_rate(self, client_class) -> None:
        client = client_class.return_value
        client.models.generate_content.return_value = SimpleNamespace(
            text="Жила-была звезда.",
            usage_metadata=SimpleNamespace(
                prompt_token_count=12,
                candidates_token_count=34,
                thoughts_token_count=100,
            ),
        )
        provider = VertexStoryProvider(self.settings)

        result = await provider.generate(Mode.FAIRYTALE, "prompt")

        self.assertEqual(result.tokens_out, 34)
        self.assertEqual(result.tokens_thought, 100)
        # (12 * 1.50 + (34 + 100) * 9.00) / 1e6. Excluding thoughts here
        # understated every job's cost by the reasoning spend.
        self.assertEqual(result.cost_usd, 0.001224)

    @patch("family_media_bot.adapters.image_vertex.genai.Client")
    async def test_gemini_image_generation_uses_production_defaults(self, client_class) -> None:
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
            http_options=HttpOptions(timeout=40_000),
        )
        _, kwargs = client.models.generate_content.call_args
        self.assertEqual(kwargs["model"], "gemini-2.5-flash-image")
        self.assertEqual(kwargs["config"].response_modalities, ["IMAGE"])

    @patch("family_media_bot.adapters.image_vertex.genai.Client")
    async def test_imagen_generation_remains_configurable(self, client_class) -> None:
        client = client_class.return_value
        client.models.generate_images.return_value = SimpleNamespace(
            generated_images=[
                SimpleNamespace(image=SimpleNamespace(image_bytes=b"png", mime_type="image/png"))
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
            http_options=HttpOptions(timeout=40_000),
        )
        _, kwargs = client.models.generate_images.call_args
        self.assertEqual(kwargs["model"], "imagen-4.0-generate-001")
        self.assertEqual(kwargs["config"].image_size, "1K")
        self.assertEqual(kwargs["config"].output_mime_type, "image/png")

    @patch("family_media_bot.adapters.image_vertex.genai.Client")
    async def test_image_failure_raises_instead_of_substituting_a_placeholder(
        self, client_class
    ) -> None:
        client = client_class.return_value
        client.models.generate_content.side_effect = RuntimeError("quota exceeded")
        provider = VertexImageProvider(self.settings)

        # Returning a placeholder gradient here made a provider failure ack as
        # a success: $0 cost, "job completed" logged, a blank rectangle sent to
        # a child, and no signal anywhere.
        with self.assertRaises(RuntimeError):
            await provider.generate("a friendly star")

    @patch("family_media_bot.adapters.image_vertex.genai.Client")
    async def test_image_response_with_no_image_raises(self, client_class) -> None:
        client = client_class.return_value
        client.models.generate_content.return_value = SimpleNamespace(candidates=[])
        provider = VertexImageProvider(self.settings)

        with self.assertRaisesRegex(RuntimeError, "no generated image"):
            await provider.generate("a friendly star")

    @patch("family_media_bot.adapters.story_vertex.genai.Client")
    def test_unpriced_text_model_fails_at_construction(self, _client_class) -> None:
        # Falling back to $0 meant bumping a model id in env silently zeroed
        # cost reporting. Refuse to start instead.
        settings = Settings(gcp_project_id="demo-project", vertex_text_model_id="gemini-9-ultra")

        with self.assertRaisesRegex(ValueError, "no price entry for text model"):
            VertexStoryProvider(settings)

    @patch("family_media_bot.adapters.image_vertex.genai.Client")
    def test_unpriced_image_model_fails_at_construction(self, _client_class) -> None:
        settings = Settings(gcp_project_id="demo-project", vertex_image_model_id="imagen-9-ultra")

        with self.assertRaisesRegex(ValueError, "no price entry for image model"):
            VertexImageProvider(settings)


if __name__ == "__main__":
    unittest.main()
