"""TelegramPoller — long-polls getUpdates and hands each update to the shared
Router (the same brain the /webhook endpoint uses, so the two entry points
behave identically). Only one polling instance may run per bot token —
Telegram returns 409 for concurrent getUpdates consumers."""

from __future__ import annotations

import asyncio
import logging

from .router import Router
from .telegram import TelegramClient

logger = logging.getLogger(__name__)


class TelegramPoller:
    def __init__(self, telegram: TelegramClient, router: Router) -> None:
        self._telegram = telegram
        self._router = router
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
                    await self._router.handle_update(update)
                except Exception:
                    logger.exception(
                        "failed to handle update",
                        extra={"update_id": update.get("update_id")},
                    )

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("telegram poller stopped")
