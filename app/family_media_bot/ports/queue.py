"""QueuePort — the async work queue between the web tier and the worker tier.

Dev impl: InMemoryQueue. Prod impl: SqsQueue (lands in a later session).
"""

from __future__ import annotations

import abc
from dataclasses import dataclass

from ..models import Job


@dataclass(frozen=True)
class QueueDelivery:
    """A received job plus the provider-specific receipt used for ack/nack."""

    job: Job
    receipt: str = ""


class QueuePort(abc.ABC):
    @abc.abstractmethod
    async def enqueue(self, job: Job) -> None:
        """Add a job to the queue. Called from the webhook — must be fast."""

    @abc.abstractmethod
    async def dequeue(self, timeout: float = 1.0) -> QueueDelivery | None:
        """Return the next delivery, or None if none arrives within `timeout`."""

    @abc.abstractmethod
    async def ack(self, delivery: QueueDelivery) -> None:
        """Remove a successfully processed delivery from the queue."""

    @abc.abstractmethod
    async def nack(self, delivery: QueueDelivery) -> None:
        """Release a failed delivery for retry and eventual DLQ routing."""

    @abc.abstractmethod
    async def depth(self) -> int:
        """Approximate number of queued jobs (drives autoscaling + metrics)."""

    @abc.abstractmethod
    async def check_ready(self) -> bool:
        """Readiness probe — is the queue backend reachable?"""
