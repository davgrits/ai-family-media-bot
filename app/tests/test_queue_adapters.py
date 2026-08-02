from __future__ import annotations

import unittest
from unittest.mock import patch

from family_media_bot.adapters.queue_inmemory import InMemoryQueue
from family_media_bot.adapters.queue_sqs import SqsQueue
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


class SqsQueueTests(unittest.IsolatedAsyncioTestCase):
    @patch("family_media_bot.adapters.queue_sqs.boto3.client")
    async def test_lifecycle_and_ack_nack_behavior_remain_compatible(self, client_factory) -> None:
        client = client_factory.return_value
        job = new_job(123, Mode.CUSTOM, "dragon")
        client.receive_message.return_value = {
            "Messages": [
                {
                    "Body": job.model_dump_json(),
                    "ReceiptHandle": "receipt-1",
                }
            ]
        }
        queue = SqsQueue("https://sqs.us-east-1.amazonaws.com/123/jobs", "us-east-1")

        await queue.start(concurrency=2)
        delivery = await queue.dequeue(timeout=1.0)
        self.assertIsNotNone(delivery)
        await queue.nack(delivery)
        await queue.ack(delivery)
        await queue.close()

        client.change_message_visibility.assert_called_once_with(
            QueueUrl="https://sqs.us-east-1.amazonaws.com/123/jobs",
            ReceiptHandle="receipt-1",
            VisibilityTimeout=0,
        )
        client.delete_message.assert_called_once_with(
            QueueUrl="https://sqs.us-east-1.amazonaws.com/123/jobs",
            ReceiptHandle="receipt-1",
        )


if __name__ == "__main__":
    unittest.main()
