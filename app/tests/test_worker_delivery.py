from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock

from family_media_bot.models import Mode, new_job
from family_media_bot.ports.queue import QueueDelivery
from family_media_bot.worker import Worker


class WorkerDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def _run_one_delivery(self, success: bool):
        job = new_job(123, Mode.CUSTOM, "dragon")
        delivery = QueueDelivery(job=job, receipt="receipt")
        queue = AsyncMock()
        queue.dequeue.side_effect = [delivery, asyncio.CancelledError()]
        queue.depth.return_value = 0
        pipeline = AsyncMock()
        pipeline.process.return_value = success
        worker = Worker(queue, pipeline)

        await worker._run(0)
        return queue, pipeline, delivery

    async def test_ack_on_success(self) -> None:
        queue, pipeline, delivery = await self._run_one_delivery(True)

        pipeline.process.assert_awaited_once_with(delivery.job)
        queue.ack.assert_awaited_once_with(delivery)
        queue.nack.assert_not_awaited()

    async def test_nack_on_failure(self) -> None:
        queue, pipeline, delivery = await self._run_one_delivery(False)

        pipeline.process.assert_awaited_once_with(delivery.job)
        queue.nack.assert_awaited_once_with(delivery)
        queue.ack.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
