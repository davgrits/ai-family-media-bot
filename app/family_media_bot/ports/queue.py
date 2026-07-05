"""QueuePort — the async work queue between the web tier and the worker tier.

Dev impl: InMemoryQueue. Prod impl: SqsQueue (lands in a later session).
"""

from __future__ import annotations

import abc

from ..models import Job


class QueuePort(abc.ABC):
    @abc.abstractmethod
    async def enqueue(self, job: Job) -> None:
        """Add a job to the queue. Called from the webhook — must be fast."""

    @abc.abstractmethod
    async def dequeue(self, timeout: float = 1.0) -> Job | None:
        """Return the next job, or None if none arrives within `timeout` seconds.
        The timeout lets the worker loop check for shutdown between polls."""

    @abc.abstractmethod
    async def depth(self) -> int:
        """Approximate number of queued jobs (drives autoscaling + metrics)."""

    @abc.abstractmethod
    async def check_ready(self) -> bool:
        """Readiness probe — is the queue backend reachable?"""
