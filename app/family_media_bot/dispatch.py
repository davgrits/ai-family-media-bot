"""The one conversation brain, shared by `/webhook` and the long-polling twin.

Both entry points previously parsed and enqueued independently, which is a
standing invitation to drift: a fix applied to one path silently leaves the other
behind. There is now exactly one place that decides what an update means.

Everything the bot *says* comes from i18n.py, in the language the message asked
for. Nothing here is per-chat state: the language is read off the message and
then travels on the Job.
"""

from __future__ import annotations

import logging

from . import commands, metrics
from .i18n import Lang, resolve, t
from .models import Mode, new_job
from .ports.queue import QueuePort
from .prompts import compose_request
from .telegram import TelegramClient

logger = logging.getLogger(__name__)

# What handling an update did, for the webhook's metric label.
ACCEPTED = "accepted"  # a generation job was enqueued
HANDLED = "handled"  # answered in chat, nothing enqueued
IGNORED = "ignored"  # not for us


class Dispatcher:
    def __init__(self, telegram: TelegramClient, queue: QueuePort) -> None:
        self._telegram = telegram
        self._queue = queue

    async def handle_update(self, update: dict) -> str:
        chat_id, raw = commands.extract_message(update)
        if chat_id is None or not (raw or "").strip():
            return IGNORED

        # An explicit per-message token, not a saved preference: a saved choice
        # would mean per-chat state, and the language still has to reach the
        # worker somehow, which the Job field already does.
        language, text = commands.split_language_token(raw.strip())
        lang = resolve(language) if language else resolve(commands.language_hint(update))

        if not text:
            # The message was only a language token.
            await self._telegram.send_text(chat_id, t(lang, "menu"))
            return HANDLED

        parsed = commands.parse_text(text)
        if parsed is None:
            # An unrecognised /command — /start included.
            await self._telegram.send_text(chat_id, t(lang, "greeting"))
            return HANDLED

        return await self._enqueue(chat_id, parsed.mode, parsed.args, lang)

    async def _enqueue(self, chat_id: int, mode: Mode, args: str, lang: Lang) -> str:
        prompt = compose_request(mode, args)
        job = new_job(chat_id, mode, prompt, language=lang.value)
        await self._queue.enqueue(job)

        metrics.JOBS_ENQUEUED.labels(mode=mode.value).inc()
        logger.info(
            "job enqueued",
            extra={"job_id": job.job_id, "mode": mode.value, "language": lang.value},
        )
        await self._telegram.send_text(chat_id, t(lang, "ack"))
        return ACCEPTED
