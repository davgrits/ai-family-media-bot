"""Pub/Sub implementation of QueuePort.

The web tier publishes Job JSON to a topic. Each worker process owns one
long-lived streaming subscriber and bridges its callback thread to asyncio.
Authentication uses Application Default Credentials, supplied by GKE Workload
Identity in production.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from google.cloud import pubsub_v1

from .. import metrics
from ..models import Job
from ..ports.queue import QueueDelivery, QueuePort

if TYPE_CHECKING:
    from google.cloud.pubsub_v1.subscriber.futures import StreamingPullFuture
    from google.cloud.pubsub_v1.subscriber.message import Message

logger = logging.getLogger(__name__)

_STREAM_SHUTDOWN_TIMEOUT_SECONDS = 10.0

# Must stay under the subscription's ack deadline, or a lease outlives the
# deadline and Pub/Sub redelivers work that is still being processed.
_MAX_LEASE_SECONDS = 60

_INITIAL_RECONNECT_BACKOFF_SECONDS = 1.0
_MAX_RECONNECT_BACKOFF_SECONDS = 30.0


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

        self._loop: asyncio.AbstractEventLoop | None = None
        self._deliveries: asyncio.Queue[QueueDelivery] | None = None
        self._buffered: dict[str, Message] = {}
        self._in_flight: dict[str, Message] = {}
        self._streaming_future: StreamingPullFuture | None = None
        self._lifecycle_lock = asyncio.Lock()
        self._callback_lock = threading.Lock()
        self._closing = False
        self._closed = False
        # Whether the data-plane stream is believed alive. Read by /livez, which
        # is why it is a plain local boolean and not a cloud call: a liveness
        # probe that talks to Google turns a Google outage into a restart loop.
        self._stream_alive = False
        self._concurrency = 1
        self._supervisor: asyncio.Task | None = None

    @staticmethod
    def _resource_path(project_id: str, resource_type: str, name: str) -> str:
        if name.startswith("projects/"):
            return name
        return f"projects/{project_id}/{resource_type}/{name}"

    async def start(self, concurrency: int = 1) -> None:
        async with self._lifecycle_lock:
            if self._closed:
                raise RuntimeError("Pub/Sub queue is closed")
            if self._streaming_future is not None:
                return

            max_messages = max(1, concurrency)
            self._concurrency = max_messages
            self._loop = asyncio.get_running_loop()
            self._deliveries = asyncio.Queue(maxsize=max_messages)
            self._subscribe(max_messages)

    def _subscribe(self, max_messages: int) -> None:
        """Open the streaming pull. Called on start and on every reconnect."""
        flow_control = pubsub_v1.types.FlowControl(
            max_messages=max_messages,
            # Defaults to 3600s, against a subscription ack deadline of 60s. A
            # hung model call would otherwise hold a lease for an hour while the
            # only worker sat blocked on it.
            max_lease_duration=_MAX_LEASE_SECONDS,
        )
        self._streaming_future = self._subscriber.subscribe(
            self._subscription_path,
            callback=self._on_message,
            flow_control=flow_control,
            await_callbacks_on_shutdown=True,
        )
        self._streaming_future.add_done_callback(self._on_stream_done)
        self._stream_alive = True
        logger.info(
            "Pub/Sub streaming subscriber started",
            extra={
                "max_outstanding_messages": max_messages,
                "max_lease_seconds": _MAX_LEASE_SECONDS,
            },
        )

    def is_healthy(self) -> bool:
        """Whether this process is actually consuming.

        The stream can terminate permanently — google-cloud-pubsub treats
        Unauthenticated, PermissionDenied, NotFound, Cancelled *and any
        non-GoogleAPICallError exception* as terminal, so a Workload Identity
        token-refresh blip is enough. When that happened the pod stayed Running,
        Ready and Live with zero restarts, consuming nothing, indefinitely.

        Under the old autoscaler this self-healed by accident: the cooldown scaled
        to zero and the next backlog spike built a fresh pod. A fixed replica count
        removes that accident, which is why this signal has to be explicit.
        """
        return self._stream_alive and not self._closed

    async def _reconnect_after(self, delay: float) -> None:
        """Reopen the stream, backing off, until it holds or we shut down."""
        attempt = 0
        while not self._closing and not self._closed:
            attempt += 1
            await asyncio.sleep(delay)
            if self._closing or self._closed:
                return
            async with self._lifecycle_lock:
                if self._closing or self._closed or self._stream_alive:
                    return
                try:
                    self._subscribe(self._concurrency)
                    logger.info("Pub/Sub stream reconnected", extra={"attempt": attempt})
                    return
                except Exception:
                    logger.exception(
                        "Pub/Sub reconnect failed; will retry", extra={"attempt": attempt}
                    )
            delay = min(delay * 2, _MAX_RECONNECT_BACKOFF_SECONDS)

    async def enqueue(self, job: Job) -> None:
        if self._closed:
            raise RuntimeError("Pub/Sub queue is closed")
        future = self._publisher.publish(self._topic_path, job.model_dump_json().encode("utf-8"))
        await asyncio.to_thread(future.result)

    def _on_message(self, message: Message) -> None:
        """Validate in Pub/Sub's callback thread, then hand off without blocking it."""
        if self._closing:
            message.nack()
            return

        try:
            job = Job.model_validate_json(message.data)
            created_at = datetime.fromisoformat(job.created_at.replace("Z", "+00:00"))
            if created_at.tzinfo is None:
                raise ValueError("Job.created_at must include a timezone")
            queue_wait = max(
                0.0,
                (datetime.now(timezone.utc) - created_at.astimezone(timezone.utc)).total_seconds(),
            )
        except Exception:
            # Validation exceptions can embed the rejected input, so do not
            # attach exception text to this log record.
            logger.warning(
                "rejecting malformed Pub/Sub message",
                extra={"message_id": str(message.message_id)},
            )
            message.nack()
            return

        # Synchronize with close() so no callback handoff can be scheduled after
        # shutdown has started.
        with self._callback_lock:
            loop = self._loop
            if self._closing or loop is None:
                message.nack()
                return
            try:
                loop.call_soon_threadsafe(self._accept_message, message, job, queue_wait)
            except RuntimeError:
                # The event loop is already stopping; release the lease immediately.
                message.nack()

    def _accept_message(self, message: Message, job: Job, queue_wait: float) -> None:
        deliveries = self._deliveries
        if self._closing or deliveries is None:
            message.nack()
            return

        receipt = message.ack_id
        if (
            not receipt
            or receipt in self._buffered
            or receipt in self._in_flight
            or deliveries.full()
        ):
            logger.error(
                "releasing Pub/Sub message outside local flow-control capacity",
                extra={"message_id": str(message.message_id)},
            )
            message.nack()
            return

        # delivery_attempt is populated only when the subscription carries a
        # dead-letter policy, which Terraform configures. None otherwise.
        attempt = int(getattr(message, "delivery_attempt", None) or 0)
        delivery = QueueDelivery(job=job, receipt=receipt, attempt=attempt)
        self._buffered[receipt] = message
        deliveries.put_nowait(delivery)
        metrics.QUEUE_WAIT_DURATION.labels(mode=job.mode.value).observe(queue_wait)
        logger.info(
            "Pub/Sub message received",
            extra={
                "job_id": job.job_id,
                "mode": job.mode.value,
                "message_id": str(message.message_id),
                "queue_wait_s": round(queue_wait, 3),
                "attempt": attempt,
            },
        )

    async def dequeue(self, timeout: float = 1.0) -> QueueDelivery | None:
        deliveries = self._deliveries
        if deliveries is None:
            raise RuntimeError("Pub/Sub streaming subscriber has not been started")

        try:
            delivery = await asyncio.wait_for(deliveries.get(), timeout=max(0.0, timeout))
        except asyncio.TimeoutError:
            return None

        message = self._buffered.pop(delivery.receipt, None)
        if message is None:
            # Shutdown may have released this delivery before it was handed out.
            return None
        self._in_flight[delivery.receipt] = message
        return delivery

    async def ack(self, delivery: QueueDelivery) -> None:
        message = self._in_flight.pop(delivery.receipt, None)
        if message is not None:
            message.ack()

    async def nack(self, delivery: QueueDelivery) -> None:
        message = self._in_flight.pop(delivery.receipt, None)
        if message is not None:
            message.nack()

    async def close(self) -> None:
        async with self._lifecycle_lock:
            if self._closed:
                return
            with self._callback_lock:
                self._closing = True

            # Let already-scheduled callback handoffs observe the closing flag.
            await asyncio.sleep(0)

            # Release everything the process has leased before stopping the
            # stream so the dispatch thread can send the nacks.
            #
            # This ordering is load-bearing and invisible. It works because
            # Dispatcher.stop() joins its worker thread and QueueCallbackWorker
            # drains everything already queued ahead of the poison pill — so
            # nacks issued now are actually transmitted. Reversing these two
            # steps (cancel the stream, then nack) silently drops the nacks, and
            # every message waits out its ack deadline instead. Nothing fails
            # loudly if that happens; jobs just get slower to retry.
            messages = [*self._buffered.values(), *self._in_flight.values()]
            self._buffered.clear()
            self._in_flight.clear()
            for message in messages:
                message.nack()

            future = self._streaming_future
            self._streaming_future = None
            if future is not None:
                future.cancel()
                try:
                    await asyncio.wait_for(
                        asyncio.to_thread(
                            future.result,
                            timeout=_STREAM_SHUTDOWN_TIMEOUT_SECONDS,
                        ),
                        timeout=_STREAM_SHUTDOWN_TIMEOUT_SECONDS + 1.0,
                    )
                except (asyncio.TimeoutError, FutureTimeoutError):
                    logger.warning("Pub/Sub streaming subscriber shutdown timed out")
                except Exception:
                    if not future.cancelled():
                        logger.exception("Pub/Sub streaming subscriber stopped with an error")

            try:
                await asyncio.wait_for(asyncio.to_thread(self._subscriber.close), timeout=2.0)
            except asyncio.TimeoutError:
                logger.warning("Pub/Sub subscriber client close timed out")
            except Exception:
                logger.exception("Pub/Sub subscriber client close failed")

            try:
                self._publisher.stop()
            except Exception:
                logger.exception("Pub/Sub publisher client stop failed")
            self._closed = True
            logger.info(
                "Pub/Sub streaming subscriber stopped",
                extra={"released_messages": len(messages)},
            )

    def _on_stream_done(self, future: StreamingPullFuture) -> None:
        """Runs on a library thread when the stream terminates.

        Previously this logged one line and returned, leaving the pod alive and
        consuming nothing forever. Now it marks the process unhealthy — so /livez
        fails and Kubernetes restarts it — and also attempts a reconnect, so a
        transient blip costs seconds rather than a pod restart.
        """
        if self._closing or future.cancelled():
            return

        self._stream_alive = False
        self._streaming_future = None

        try:
            exception = future.exception()
        except Exception:
            logger.exception("Pub/Sub streaming subscriber future failed")
            exception = None
        if exception is not None:
            logger.error(
                "Pub/Sub streaming subscriber stopped unexpectedly",
                exc_info=(type(exception), exception, exception.__traceback__),
            )
        else:
            logger.error("Pub/Sub streaming subscriber stopped without an error")

        # Hand back to the event loop from this library thread. If the loop is
        # gone we are shutting down anyway, and /livez already reports unhealthy.
        loop = self._loop
        if loop is None:
            return
        try:
            self._supervisor = asyncio.run_coroutine_threadsafe(
                self._reconnect_after(_INITIAL_RECONNECT_BACKOFF_SECONDS), loop
            )
        except RuntimeError:
            logger.warning("event loop unavailable; not attempting a Pub/Sub reconnect")

    async def depth(self) -> int:
        # Pub/Sub exposes backlog size through Cloud Monitoring, not the
        # Subscriber API, so there is nothing cheap to return here. Reporting 0
        # is a lie the QUEUE_DEPTH gauge then publishes; both are slated for
        # removal in favour of the oldest_unacked_message_age alert.
        return 0

    async def check_ready(self) -> bool:
        if self._closed:
            return False
        try:
            await asyncio.to_thread(
                self._subscriber.get_subscription,
                request={"subscription": self._subscription_path},
            )
            return True
        except Exception:
            return False
