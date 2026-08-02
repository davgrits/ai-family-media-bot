"""FastAPI application: the web tier and (in RUN_MODE=all/worker) the in-process
worker. Endpoints follow the frozen contract:

    GET  /healthz   liveness — MUST NOT touch Bedrock/SQS/S3
    GET  /readyz    readiness — checks the swap-point deps
    GET  /metrics   Prometheus scrape
    POST /webhook   Telegram update → validate → enqueue → 200 fast
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from . import commands, factory, metrics
from .config import Settings, get_settings
from .logging_setup import setup_logging
from .models import new_job
from .pipeline import Pipeline
from .prompts import compose_prompt
from .telemetry import setup_telemetry
from .worker import Worker

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings.log_level, settings.log_format)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        setup_telemetry(settings.otel_service_name, settings.otel_exporter_otlp_endpoint)

        # Wire the swap points once, at startup.
        app.state.settings = settings
        app.state.queue = factory.build_queue(settings)
        app.state.story = factory.build_story(settings)
        app.state.image = factory.build_image(settings)
        app.state.storage = factory.build_storage(settings)
        app.state.telegram = factory.build_telegram(settings)
        app.state.worker = None

        app.state.poller = None

        if settings.run_mode in ("all", "worker"):
            pipeline = Pipeline(
                app.state.story, app.state.image, app.state.storage, app.state.telegram
            )
            worker = Worker(
                app.state.queue,
                pipeline,
                settings.worker_concurrency,
                settings.pubsub_max_delivery_attempts,
            )
            await worker.start()
            app.state.worker = worker

        if settings.telegram_polling and settings.telegram_bot_token:
            from .poller import TelegramPoller

            poller = TelegramPoller(app.state.telegram, app.state.queue)
            await poller.start()
            app.state.poller = poller

        logger.info(
            "startup complete",
            extra={
                "run_mode": settings.run_mode,
                "story_provider": settings.story_provider,
                "image_provider": settings.image_provider,
                "queue": settings.queue,
                "storage": settings.storage,
                "telegram_enabled": bool(settings.telegram_bot_token),
                "telegram_polling": settings.telegram_polling,
            },
        )
        try:
            yield
        finally:
            if app.state.poller:
                await app.state.poller.stop()
            if app.state.worker:
                await app.state.worker.stop()
            await app.state.queue.close()
            await app.state.telegram.aclose()

    app = FastAPI(title="AI Family Media Bot", version="0.1.0", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz():
        # Liveness only. MUST NOT check Bedrock/SQS/S3 (avoids restart loops).
        return {"status": "ok"}

    @app.get("/metrics")
    async def metrics_endpoint():
        # Prometheus scrape. A plain route (not a mount) so `/metrics` with no
        # trailing slash returns 200 directly instead of a 307 redirect.
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/readyz")
    async def readyz():
        # Readiness — are the downstream deps reachable?
        checks: dict[str, bool] = {}
        for name, component in (
            ("queue", app.state.queue),
            ("storage", app.state.storage),
            ("story", app.state.story),
            ("image", app.state.image),
        ):
            try:
                checks[name] = bool(await component.check_ready())
            except Exception:
                checks[name] = False

        ready = all(checks.values())
        return JSONResponse(
            {"status": "ready" if ready else "not_ready", "checks": checks},
            status_code=status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    @app.post("/webhook")
    async def webhook(request: Request):
        # Optional shared-secret check (Telegram sets this header if configured).
        secret = settings.telegram_webhook_secret
        if secret and request.headers.get("x-telegram-bot-api-secret-token") != secret:
            metrics.WEBHOOK_UPDATES.labels(result="invalid").inc()
            return JSONResponse({"ok": False}, status_code=status.HTTP_403_FORBIDDEN)

        try:
            update = await request.json()
        except Exception:
            # Ack with 200 so Telegram doesn't retry a malformed update.
            metrics.WEBHOOK_UPDATES.labels(result="invalid").inc()
            return {"ok": True}

        parsed = commands.parse(update)
        if parsed is None:
            metrics.WEBHOOK_UPDATES.labels(result="ignored").inc()
            return {"ok": True}

        # Compose the prompt and enqueue — then return immediately.
        prompt = compose_prompt(parsed.mode, parsed.args)
        job = new_job(parsed.chat_id, parsed.mode, prompt)
        await app.state.queue.enqueue(job)

        metrics.WEBHOOK_UPDATES.labels(result="accepted").inc()
        metrics.JOBS_ENQUEUED.labels(mode=parsed.mode.value).inc()
        logger.info(
            "job enqueued",
            extra={"job_id": job.job_id, "mode": job.mode.value},
        )
        return {"ok": True}

    return app


# Importable ASGI target: `uvicorn family_media_bot.app:app`
app = create_app()
