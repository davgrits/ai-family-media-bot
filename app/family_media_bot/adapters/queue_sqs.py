"""SqsQueue — QueuePort backed by Amazon SQS with explicit ack/nack handling."""

from __future__ import annotations

import asyncio
import logging

import boto3

from ..models import Job
from ..ports.queue import QueueDelivery, QueuePort

logger = logging.getLogger(__name__)


class SqsQueue(QueuePort):
    def __init__(self, queue_url: str, region: str) -> None:
        if not queue_url:
            raise ValueError("QUEUE=sqs requires SQS_QUEUE_URL to be set")
        self._queue_url = queue_url
        self._client = boto3.client("sqs", region_name=region)

    async def enqueue(self, job: Job) -> None:
        await asyncio.to_thread(
            self._client.send_message,
            QueueUrl=self._queue_url,
            MessageBody=job.model_dump_json(),
        )

    async def dequeue(self, timeout: float = 1.0) -> QueueDelivery | None:
        resp = await asyncio.to_thread(
            self._client.receive_message,
            QueueUrl=self._queue_url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=max(1, min(20, int(timeout))),
        )
        messages = resp.get("Messages", [])
        if not messages:
            return None
        msg = messages[0]
        try:
            return QueueDelivery(
                job=Job.model_validate_json(msg["Body"]),
                receipt=msg["ReceiptHandle"],
            )
        except Exception:
            # Validation errors may embed the rejected body.
            logger.warning("rejecting malformed SQS message")
            await asyncio.to_thread(
                self._client.change_message_visibility,
                QueueUrl=self._queue_url,
                ReceiptHandle=msg["ReceiptHandle"],
                VisibilityTimeout=0,
            )
            return None

    async def ack(self, delivery: QueueDelivery) -> None:
        await asyncio.to_thread(
            self._client.delete_message,
            QueueUrl=self._queue_url,
            ReceiptHandle=delivery.receipt,
        )

    async def nack(self, delivery: QueueDelivery) -> None:
        await asyncio.to_thread(
            self._client.change_message_visibility,
            QueueUrl=self._queue_url,
            ReceiptHandle=delivery.receipt,
            VisibilityTimeout=0,
        )

    async def depth(self) -> int:
        resp = await asyncio.to_thread(
            self._client.get_queue_attributes,
            QueueUrl=self._queue_url,
            AttributeNames=["ApproximateNumberOfMessages"],
        )
        return int(resp["Attributes"].get("ApproximateNumberOfMessages", "0"))

    async def check_ready(self) -> bool:
        try:
            await self.depth()
            return True
        except Exception:
            return False
