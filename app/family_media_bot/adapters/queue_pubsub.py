"""Pub/Sub implementation of QueuePort.

The web tier publishes the Job JSON to a topic; worker pods pull from the
Terraform-created subscription. Authentication uses Application Default
Credentials, which GKE obtains through Workload Identity—no key file is read.
"""

from __future__ import annotations

import asyncio
import logging

from google.api_core.exceptions import DeadlineExceeded
from google.cloud import pubsub_v1

from ..models import Job
from ..ports.queue import QueueDelivery, QueuePort

logger = logging.getLogger(__name__)


class PubSubQueue(QueuePort):
    def __init__(self, project_id: str, topic_name: str, subscription_name: str) -> None:
        if not project_id:
            raise ValueError("QUEUE=pubsub requires GCP_PROJECT_ID to be set")
        if not topic_name:
            raise ValueError("QUEUE=pubsub requires PUBSUB_TOPIC_NAME to be set")
        if not subscription_name:
            raise ValueError("QUEUE=pubsub requires PUBSUB_SUBSCRIPTION_NAME to be set")

        self._publisher = pubsub_v1.PublisherClient()
        self._subscriber = pubsub_v1.SubscriberClient()
        self._topic_path = self._resource_path(project_id, "topics", topic_name)
        self._subscription_path = self._resource_path(
            project_id, "subscriptions", subscription_name
        )

    @staticmethod
    def _resource_path(project_id: str, resource_type: str, name: str) -> str:
        if name.startswith("projects/"):
            return name
        return f"projects/{project_id}/{resource_type}/{name}"

    async def enqueue(self, job: Job) -> None:
        future = self._publisher.publish(self._topic_path, job.model_dump_json().encode("utf-8"))
        await asyncio.to_thread(future.result)

    async def dequeue(self, timeout: float = 1.0) -> QueueDelivery | None:
        try:
            response = await asyncio.to_thread(
                self._subscriber.pull,
                request={"subscription": self._subscription_path, "max_messages": 1},
                timeout=max(1.0, timeout),
            )
        except DeadlineExceeded:
            return None
        if not response.received_messages:
            return None

        received = response.received_messages[0]
        try:
            return QueueDelivery(
                job=Job.model_validate_json(received.message.data),
                receipt=received.ack_id,
            )
        except Exception:
            logger.exception("rejecting malformed Pub/Sub message")
            await self._modify_ack_deadline(received.ack_id, seconds=0)
            return None

    async def ack(self, delivery: QueueDelivery) -> None:
        await asyncio.to_thread(
            self._subscriber.acknowledge,
            request={"subscription": self._subscription_path, "ack_ids": [delivery.receipt]},
        )

    async def nack(self, delivery: QueueDelivery) -> None:
        await self._modify_ack_deadline(delivery.receipt, seconds=0)

    async def _modify_ack_deadline(self, ack_id: str, seconds: int) -> None:
        await asyncio.to_thread(
            self._subscriber.modify_ack_deadline,
            request={
                "subscription": self._subscription_path,
                "ack_ids": [ack_id],
                "ack_deadline_seconds": seconds,
            },
        )

    async def depth(self) -> int:
        # Pub/Sub exposes backlog size through Cloud Monitoring rather than the
        # Subscriber API. KEDA reads that authoritative metric for scaling.
        return 0

    async def check_ready(self) -> bool:
        try:
            await asyncio.to_thread(
                self._subscriber.get_subscription,
                request={"subscription": self._subscription_path},
            )
            return True
        except Exception:
            return False
