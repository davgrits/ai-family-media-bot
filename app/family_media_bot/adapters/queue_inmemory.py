"""InMemoryQueue — dev implementation of QueuePort backed by asyncio.Queue.

Lives inside a single process, so the web tier and worker tier must run together
(RUN_MODE=all) when using it. SqsQueue replaces it for the cross-process split.
"""

from __future__ import annotations

import asyncio

from ..models import Job
from ..ports.queue import QueuePort


class InMemoryQueue(QueuePort):
    def __init__(self, maxsize: int = 1000) -> None:
        self._q: asyncio.Queue[Job] = asyncio.Queue(maxsize=maxsize)

    async def enqueue(self, job: Job) -> None:
        await self._q.put(job)

    async def dequeue(self, timeout: float = 1.0) -> Job | None:
        try:
            return await asyncio.wait_for(self._q.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    async def depth(self) -> int:
        return self._q.qsize()

    async def check_ready(self) -> bool:
        return True
