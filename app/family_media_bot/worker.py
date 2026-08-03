"""Background worker — drains the queue and runs the pipeline per job. Runs as
N concurrent asyncio tasks inside the process (dev), or as the dedicated worker
tier in prod."""

from __future__ import annotations

import asyncio
import logging

from . import metrics
from .pipeline import Pipeline
from .ports.queue import QueueDelivery, QueuePort

logger = logging.getLogger(__name__)


class Worker:
    def __init__(
        self,
        queue: QueuePort,
        pipeline: Pipeline,
        concurrency: int = 1,
        max_delivery_attempts: int = 5,
        job_timeout: float = 90.0,
        drain_timeout: float = 30.0,
    ) -> None:
        self._queue = queue
        self._pipeline = pipeline
        self._concurrency = max(1, concurrency)
        # Must match the subscription's dead-letter max_delivery_attempts, so
        # the worker knows which attempt is the last one before the DLQ.
        self._max_delivery_attempts = max(1, max_delivery_attempts)
        # Measured pipeline time is ~14s. 90s is generous headroom while still
        # far under terminationGracePeriodSeconds, so a hung job cannot outlive
        # the pod's shutdown budget.
        self._job_timeout = job_timeout
        self._drain_timeout = drain_timeout
        self._tasks: list[asyncio.Task] = []
        self._stop = asyncio.Event()

    def _is_final_attempt(self, delivery: QueueDelivery) -> bool:
        """A provider that does not report an attempt count (in-memory dev
        queue) never retries, so every delivery is final."""
        if delivery.attempt <= 0:
            return True
        return delivery.attempt >= self._max_delivery_attempts

    async def start(self) -> None:
        self._stop.clear()
        await self._queue.start(self._concurrency)
        self._tasks = [asyncio.create_task(self._run(i)) for i in range(self._concurrency)]
        logger.info("worker started", extra={"concurrency": self._concurrency})

    async def _run(self, index: int) -> None:
        while not self._stop.is_set():
            delivery: QueueDelivery | None = None
            try:
                delivery = await self._queue.dequeue(timeout=1.0)
                if delivery is None:
                    continue
                try:
                    metrics.QUEUE_DEPTH.set(await self._queue.depth())
                except Exception:
                    pass
                notify = self._is_final_attempt(delivery)
                # Bound the job. Neither Vertex call had an upper time bound, so
                # a hung model call would block the only worker indefinitely
                # while its Pub/Sub lease was extended underneath it.
                processed = await asyncio.wait_for(
                    self._pipeline.process(delivery.job, notify_on_failure=notify),
                    timeout=self._job_timeout,
                )
                if processed:
                    await self._queue.ack(delivery)
                else:
                    await self._queue.nack(delivery)
                delivery = None
            except TimeoutError:
                logger.error(
                    "job exceeded its time budget; releasing for retry",
                    extra={"worker": index, "timeout_s": self._job_timeout},
                )
                if delivery is not None:
                    await self._release(delivery, index)
                    delivery = None
            except asyncio.CancelledError:
                if delivery is not None:
                    await self._release(delivery, index)
                break
            except Exception:
                logger.exception("worker loop error", extra={"worker": index})
                if delivery is not None:
                    await self._release(delivery, index)

    async def _release(self, delivery: QueueDelivery, index: int) -> None:
        try:
            await self._queue.nack(delivery)
        except Exception:
            logger.exception(
                "failed to release delivery",
                extra={"worker": index, "job_id": delivery.job.job_id},
            )

    async def stop(self) -> None:
        """Signal, drain, then cancel.

        Previously this set the flag and cancelled in the same breath, so every
        rolling update discarded whatever was mid-generation. Worse, cancelling
        inside `telegram.send_story_and_image` could deliver a story and then nack
        it, so the retry sent the family a second, different story for one request.

        Now the loops get a window to finish the job they hold. Whatever is still
        running after it is cancelled and nacked, which is correct — the work is
        durable in Pub/Sub either way.
        """
        self._stop.set()

        done, pending = await asyncio.wait(self._tasks, timeout=self._drain_timeout)
        if pending:
            logger.warning(
                "drain window elapsed with jobs still running; cancelling",
                extra={"draining": len(pending), "drain_timeout_s": self._drain_timeout},
            )
        else:
            logger.info("worker loops drained cleanly", extra={"drained": len(done)})

        for task in pending:
            task.cancel()
        for task in pending:
            try:
                await task
            except asyncio.CancelledError:
                pass

        await self._queue.close()
        logger.info("worker stopped")
