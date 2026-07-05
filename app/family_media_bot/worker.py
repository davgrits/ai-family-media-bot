"""Background worker — drains the queue and runs the pipeline per job. Runs as
N concurrent asyncio tasks inside the process (dev), or as the dedicated worker
tier in prod (RUN_MODE=worker, sharing SQS with the web tier)."""

from __future__ import annotations

import asyncio
import logging

from . import metrics
from .pipeline import Pipeline
from .ports.queue import QueuePort

logger = logging.getLogger(__name__)


class Worker:
    def __init__(self, queue: QueuePort, pipeline: Pipeline, concurrency: int = 1) -> None:
        self._queue = queue
        self._pipeline = pipeline
        self._concurrency = max(1, concurrency)
        self._tasks: list[asyncio.Task] = []
        self._stop = asyncio.Event()

    async def start(self) -> None:
        self._stop.clear()
        self._tasks = [asyncio.create_task(self._run(i)) for i in range(self._concurrency)]
        logger.info("worker started", extra={"concurrency": self._concurrency})

    async def _run(self, index: int) -> None:
        while not self._stop.is_set():
            try:
                job = await self._queue.dequeue(timeout=1.0)
                if job is None:
                    continue
                try:
                    metrics.QUEUE_DEPTH.set(await self._queue.depth())
                except Exception:
                    pass
                await self._pipeline.process(job)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("worker loop error", extra={"worker": index})

    async def stop(self) -> None:
        self._stop.set()
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        logger.info("worker stopped")
