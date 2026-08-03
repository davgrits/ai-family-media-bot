from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock

from family_media_bot.models import Mode, new_job
from family_media_bot.ports.queue import QueueDelivery
from family_media_bot.worker import Worker


class WorkerDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def _run_one_delivery(self, success: bool, attempt: int = 0):
        job = new_job(123, Mode.CUSTOM, "dragon")
        delivery = QueueDelivery(job=job, receipt="receipt", attempt=attempt)
        queue = AsyncMock()
        queue.dequeue.side_effect = [delivery, asyncio.CancelledError()]
        queue.depth.return_value = 0
        pipeline = AsyncMock()
        pipeline.process.return_value = success
        worker = Worker(queue, pipeline, max_delivery_attempts=5)

        await worker._run(0)
        return queue, pipeline, delivery

    async def test_ack_on_success(self) -> None:
        queue, pipeline, delivery = await self._run_one_delivery(True)

        pipeline.process.assert_awaited_once_with(delivery.job, notify_on_failure=True)
        queue.ack.assert_awaited_once_with(delivery)
        queue.nack.assert_not_awaited()

    async def test_nack_on_failure(self) -> None:
        queue, pipeline, delivery = await self._run_one_delivery(False)

        pipeline.process.assert_awaited_once_with(delivery.job, notify_on_failure=True)
        queue.nack.assert_awaited_once_with(delivery)
        queue.ack.assert_not_awaited()

    async def test_chat_is_not_notified_while_retries_remain(self) -> None:
        # Attempts 1-4 of 5 must stay silent, or one failed request sends the
        # same apology five times before the message reaches the DLQ.
        for attempt in (1, 2, 3, 4):
            with self.subTest(attempt=attempt):
                _, pipeline, delivery = await self._run_one_delivery(False, attempt=attempt)
                pipeline.process.assert_awaited_once_with(delivery.job, notify_on_failure=False)

    async def test_chat_is_notified_on_the_final_attempt(self) -> None:
        _, pipeline, delivery = await self._run_one_delivery(False, attempt=5)

        pipeline.process.assert_awaited_once_with(delivery.job, notify_on_failure=True)

    async def test_queue_without_attempt_reporting_always_notifies(self) -> None:
        # The in-memory dev queue never redelivers, so every delivery is final.
        _, pipeline, delivery = await self._run_one_delivery(False, attempt=0)

        pipeline.process.assert_awaited_once_with(delivery.job, notify_on_failure=True)

    async def test_unexpected_processing_exception_releases_delivery(self) -> None:
        job = new_job(123, Mode.CUSTOM, "dragon")
        delivery = QueueDelivery(job=job, receipt="receipt")
        queue = AsyncMock()
        queue.dequeue.side_effect = [delivery, asyncio.CancelledError()]
        queue.depth.return_value = 0
        pipeline = AsyncMock()
        pipeline.process.side_effect = RuntimeError("failed")
        worker = Worker(queue, pipeline)

        await worker._run(0)

        queue.nack.assert_awaited_once_with(delivery)
        queue.ack.assert_not_awaited()

    async def test_worker_starts_queue_once_with_configured_concurrency_and_closes(self) -> None:
        waiting = asyncio.Event()

        async def wait_for_delivery(timeout: float):
            await waiting.wait()

        queue = AsyncMock()
        queue.dequeue.side_effect = wait_for_delivery
        # A short drain window: these loops never finish on their own, so the
        # production value would make the test wait it out in full.
        worker = Worker(queue, AsyncMock(), concurrency=3, drain_timeout=0.05)

        await worker.start()
        await asyncio.sleep(0)
        await worker.stop()

        queue.start.assert_awaited_once_with(3)
        self.assertEqual(queue.dequeue.await_count, 3)
        queue.close.assert_awaited_once_with()

    async def test_a_job_in_flight_gets_a_window_to_finish_before_cancellation(self) -> None:
        """The drain window. Before it existed, stop() set the flag and cancelled
        in the same breath, so every rolling update discarded whatever was
        mid-generation — and cancelling inside the Telegram send could deliver a
        story and then nack it, sending the family a second, different story."""
        job = new_job(123, Mode.CUSTOM, "dragon")
        delivery = QueueDelivery(job=job, receipt="receipt", attempt=1)
        finished = asyncio.Event()

        async def slow_process(job, notify_on_failure=True):
            await asyncio.sleep(0.05)
            finished.set()
            return True

        queue = AsyncMock()
        queue.dequeue.side_effect = [delivery, asyncio.CancelledError()]
        queue.depth.return_value = 0
        pipeline = AsyncMock()
        pipeline.process.side_effect = slow_process
        worker = Worker(queue, pipeline, drain_timeout=5.0)

        await worker.start()
        await asyncio.sleep(0)
        await worker.stop()

        self.assertTrue(finished.is_set(), "the in-flight job should have completed")
        queue.ack.assert_awaited_once_with(delivery)
        queue.nack.assert_not_awaited()

    async def test_a_job_exceeding_its_time_budget_is_released_for_retry(self) -> None:
        # Neither Vertex call had an upper bound, so a hung call would block the
        # only worker indefinitely.
        job = new_job(123, Mode.CUSTOM, "dragon")
        delivery = QueueDelivery(job=job, receipt="receipt", attempt=1)

        async def hang(job, notify_on_failure=True):
            await asyncio.sleep(30)

        queue = AsyncMock()
        queue.dequeue.side_effect = [delivery, asyncio.CancelledError()]
        queue.depth.return_value = 0
        pipeline = AsyncMock()
        pipeline.process.side_effect = hang
        worker = Worker(queue, pipeline, job_timeout=0.05)

        await worker._run(0)

        queue.nack.assert_awaited_once_with(delivery)
        queue.ack.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
