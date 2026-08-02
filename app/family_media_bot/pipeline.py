"""The per-job worker flow (contract §Result):

    compose prompt (done at enqueue) → story (text) → derive one-line image
    prompt from the story → one image → save to storage → send story + image to
    Telegram → emit per-job cost.
"""

from __future__ import annotations

import logging
import time

from . import metrics
from .models import Job
from .ports.image import ImageProvider
from .ports.storage import StoragePort
from .ports.story import StoryProvider
from .prompts import derive_illustration_prompt
from .telegram import TelegramClient
from .telemetry import get_tracer

logger = logging.getLogger(__name__)
tracer = get_tracer(__name__)


class Pipeline:
    def __init__(
        self,
        story: StoryProvider,
        image: ImageProvider,
        storage: StoragePort,
        telegram: TelegramClient,
    ) -> None:
        self._story = story
        self._image = image
        self._storage = storage
        self._telegram = telegram

    async def process(self, job: Job, notify_on_failure: bool = True) -> bool:
        """Run one job. `notify_on_failure` is False while retries remain, so a
        job that fails and is redelivered apologises to the chat once rather
        than once per delivery attempt."""
        mode = job.mode.value
        started = time.perf_counter()

        with tracer.start_as_current_span("job.process") as span:
            span.set_attribute("job.id", job.job_id)
            span.set_attribute("job.mode", mode)
            try:
                logger.info(
                    "job started",
                    extra={"job_id": job.job_id, "mode": mode},
                )

                # 1. Story first (text model).
                story = await self._story.generate(job.mode, job.prompt)
                logger.info(
                    "story done",
                    extra={
                        "job_id": job.job_id,
                        "chars": len(story.text),
                        "model": story.model_id,
                        "tokens_out": story.tokens_out,
                        "tokens_thought": story.tokens_thought,
                    },
                )
                # 2. Illustration prompt: prefer the model's own English hint,
                # fall back to deriving one from the story text.
                illustration_prompt = story.illustration_hint or derive_illustration_prompt(
                    story.text
                )
                # 3. One image (image model).
                image = await self._image.generate(illustration_prompt)
                logger.info(
                    "image done",
                    extra={
                        "job_id": job.job_id,
                        "bytes": len(image.png_bytes),
                        "model": image.model_id,
                    },
                )
                # 4. Persist the story text and the PNG.
                await self._storage.save(job.job_id, story.text.encode("utf-8"), "text/plain")
                location = await self._storage.save(job.job_id, image.png_bytes, image.content_type)
                logger.info("saved", extra={"job_id": job.job_id, "location": location})
                # 5. Send story text + image back to the chat.
                await self._telegram.send_story_and_image(
                    job.chat_id, story.text, image.png_bytes, f"{job.job_id}.png"
                )
                logger.info("replied", extra={"job_id": job.job_id})

                # 6. Per-job cost = text call + image call.
                cost = round(story.cost_usd + image.cost_usd, 6)
                duration = time.perf_counter() - started

                metrics.JOBS_PROCESSED.labels(mode=mode, status="success").inc()
                metrics.JOB_DURATION.labels(mode=mode).observe(duration)
                metrics.JOB_COST.labels(mode=mode).observe(cost)
                metrics.JOB_COST_TOTAL.labels(mode=mode).inc(cost)
                span.set_attribute("job.cost_usd", cost)

                logger.info(
                    "job completed",
                    extra={
                        "job_id": job.job_id,
                        "mode": mode,
                        "location": location,
                        "cost_usd": cost,
                        "duration_s": round(duration, 3),
                    },
                )
                return True
            except Exception as exc:
                metrics.JOBS_PROCESSED.labels(mode=mode, status="error").inc()
                span.record_exception(exc)
                logger.exception(
                    "job failed",
                    extra={"job_id": job.job_id, "mode": mode, "final": notify_on_failure},
                )
                # Tell the chat instead of failing silently — but only once the
                # retries are exhausted. Apologising on every delivery attempt
                # would send the same message five times before the DLQ.
                if not notify_on_failure:
                    return False
                try:
                    await self._telegram.send_text(
                        job.chat_id,
                        "Простите, сказка сейчас не получилась 😔 "
                        "Попробуйте ещё раз через минутку.",
                    )
                except Exception:
                    logger.exception(
                        "failed to send error message", extra={"job_id": job.job_id}
                    )
                return False
