"""TelegramPoller — long-polls getUpdates and enqueues jobs.

The polling twin of the /webhook endpoint, for local runs where Telegram can't
reach us. Plain text (no leading "/") is treated as a custom scene request;
recognized /commands behave exactly like the webhook path; other /commands
(e.g. /start) get a short greeting. Only one polling instance may run per bot
token — Telegram returns 409 for concurrent getUpdates consumers.
"""

from __future__ import annotations

import asyncio
import logging

from . import commands, metrics
from .models import Mode, new_job
from .ports.queue import QueuePort
from .prompts import compose_prompt
from .telegram import TelegramClient

logger = logging.getLogger(__name__)

GREETING = (
    "Привет! Я рассказываю сказки на ночь. 🌙\n"
    "Просто напиши, о чём должна быть сказка — например: "
    "«история про дракона и маяк»."
)

ACK = "✨ Придумываю сказку и рисую картинку — это займёт около минуты..."


class TelegramPoller:
    def __init__(self, telegram: TelegramClient, queue: QueuePort) -> None:
        self._telegram = telegram
        self._queue = queue
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._offset: int | None = None

    async def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._run())
        logger.info("telegram poller started")

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                updates = await self._telegram.get_updates(offset=self._offset, timeout=30)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("getUpdates failed — retrying in 3s")
                await asyncio.sleep(3)
                continue
            for update in updates:
                self._offset = update["update_id"] + 1
                try:
                    await self._handle(update)
                except Exception:
                    logger.exception(
                        "failed to handle update",
                        extra={"update_id": update.get("update_id")},
                    )

    async def _handle(self, update: dict) -> None:
        parsed = commands.parse(update)
        if parsed is None:
            chat_id, text = commands.extract_message(update)
            if chat_id is None or not (text or "").strip():
                return
            text = text.strip()
            if text.startswith("/"):
                # /start or an unknown command — explain how to use the bot.
                await self._telegram.send_text(chat_id, GREETING)
                return
            # Plain message → custom scene request.
            parsed = commands.ParsedCommand(chat_id=chat_id, mode=Mode.CUSTOM, args=text)

        logger.info(
            "message received",
            # NB: "args" and "message" are reserved LogRecord attributes.
            extra={"chat_id": parsed.chat_id, "mode": parsed.mode.value, "request_text": parsed.args[:80]},
        )
        prompt = compose_prompt(parsed.mode, parsed.args)
        job = new_job(parsed.chat_id, parsed.mode, prompt)
        await self._queue.enqueue(job)
        metrics.JOBS_ENQUEUED.labels(mode=parsed.mode.value).inc()
        logger.info(
            "job enqueued",
            extra={"job_id": job.job_id, "mode": job.mode.value, "chat_id": parsed.chat_id},
        )
        await self._telegram.send_text(parsed.chat_id, ACK)

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("telegram poller stopped")
