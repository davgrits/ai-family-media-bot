from __future__ import annotations

import unittest

from family_media_bot.adapters.queue_inmemory import InMemoryQueue
from family_media_bot.models import Mode, new_job


class InMemoryQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_lifecycle_and_ack_nack_behavior_remain_compatible(self) -> None:
        queue = InMemoryQueue()
        job = new_job(123, Mode.CUSTOM, "dragon")

        await queue.start(concurrency=2)
        await queue.enqueue(job)
        first = await queue.dequeue(timeout=0.1)
        self.assertIsNotNone(first)
        await queue.nack(first)
        second = await queue.dequeue(timeout=0.1)
        self.assertIsNotNone(second)
        await queue.ack(second)
        await queue.close()

        self.assertEqual(first.job, job)
        self.assertEqual(second.job, job)
        self.assertEqual(await queue.depth(), 0)


if __name__ == "__main__":
    unittest.main()
