"""The highest-severity finding in the reliability review.

The Pub/Sub streaming subscriber can terminate permanently. google-cloud-pubsub
treats Unauthenticated, PermissionDenied, NotFound, Cancelled — and any
non-GoogleAPICallError exception at all — as terminal, so a Workload Identity
token-refresh blip is enough. When it happened, the pod stayed Running, Ready and
Live with zero restarts, consuming nothing, indefinitely.

Both probes stayed green through it: /healthz is static, and the readiness check
calls get_subscription, which is a control-plane RPC that succeeds whether or not
the data-plane stream is alive.

Under the old autoscaler this self-healed by accident — the cooldown scaled to
zero and the next backlog spike built a fresh pod. Removing it removed the
accident, so the signal has to be explicit.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import MagicMock, patch

from google.api_core import exceptions as gapi_exceptions

from family_media_bot.adapters.queue_inmemory import InMemoryQueue
from family_media_bot.adapters.queue_pubsub import _MAX_LEASE_SECONDS, PubSubQueue


class StreamHealthTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        publisher = patch("family_media_bot.adapters.queue_pubsub.pubsub_v1.PublisherClient")
        subscriber = patch("family_media_bot.adapters.queue_pubsub.pubsub_v1.SubscriberClient")
        self.addCleanup(publisher.stop)
        self.addCleanup(subscriber.stop)
        publisher.start()
        self.subscriber = subscriber.start().return_value
        self.future = MagicMock()
        self.future.cancelled.return_value = False
        self.subscriber.subscribe.return_value = self.future
        self.queue = PubSubQueue("demo-project", "jobs", "jobs")

    async def asyncTearDown(self) -> None:
        await self.queue.close()

    async def test_a_started_stream_reports_healthy(self) -> None:
        self.assertFalse(self.queue.is_healthy())

        await self.queue.start(concurrency=1)

        self.assertTrue(self.queue.is_healthy())

    async def test_terminal_stream_error_makes_the_process_unhealthy(self) -> None:
        # The exact class the library treats as terminal — a token refresh
        # failure surfaces as Unauthenticated.
        await self.queue.start(concurrency=1)
        self.future.exception.return_value = gapi_exceptions.Unauthenticated("token expired")

        self.queue._on_stream_done(self.future)

        self.assertFalse(
            self.queue.is_healthy(),
            "a dead stream must fail the liveness probe, not sit Ready forever",
        )

    async def test_a_stream_that_ends_with_no_error_is_still_unhealthy(self) -> None:
        # Ending cleanly but unexpectedly is the same outcome for the family:
        # nothing is being consumed.
        await self.queue.start(concurrency=1)
        self.future.exception.return_value = None

        self.queue._on_stream_done(self.future)

        self.assertFalse(self.queue.is_healthy())

    async def test_a_dead_stream_is_reconnected(self) -> None:
        await self.queue.start(concurrency=3)
        self.future.exception.return_value = gapi_exceptions.PermissionDenied("nope")

        self.queue._on_stream_done(self.future)
        self.assertFalse(self.queue.is_healthy())

        # The reconnect is scheduled onto the loop from a library thread.
        await asyncio.sleep(1.2)

        self.assertTrue(self.queue.is_healthy())
        self.assertEqual(self.subscriber.subscribe.call_count, 2)
        # Concurrency is preserved across the reconnect, not silently reset to 1.
        kwargs = self.subscriber.subscribe.call_args.kwargs
        self.assertEqual(kwargs["flow_control"].max_messages, 3)

    async def test_shutdown_does_not_trigger_a_reconnect(self) -> None:
        await self.queue.start(concurrency=1)
        await self.queue.close()

        self.queue._on_stream_done(self.future)
        await asyncio.sleep(0.1)

        self.assertFalse(self.queue.is_healthy())

    async def test_a_closed_queue_is_never_healthy(self) -> None:
        await self.queue.start(concurrency=1)
        await self.queue.close()

        self.assertFalse(self.queue.is_healthy())

    async def test_lease_duration_stays_under_the_ack_deadline(self) -> None:
        # FlowControl defaults max_lease_duration to 3600s. Against a 60s ack
        # deadline that lets a hung call hold a lease for an hour while the only
        # worker is blocked on it.
        await self.queue.start(concurrency=1)

        kwargs = self.subscriber.subscribe.call_args.kwargs
        self.assertEqual(kwargs["flow_control"].max_lease_duration, _MAX_LEASE_SECONDS)
        self.assertLessEqual(_MAX_LEASE_SECONDS, 60)


class InMemoryHealthTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_dev_queue_reports_healthy(self) -> None:
        # It cannot silently stop consuming, so the port's default is correct.
        queue = InMemoryQueue()

        self.assertTrue(queue.is_healthy())


if __name__ == "__main__":
    unittest.main()
