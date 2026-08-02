"""Minimal Telegram Bot API client. With no token configured (local dev) it logs
what it *would* send instead of calling the network — so the full flow runs with
no external dependencies."""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

# Telegram caption limit; longer stories are sent as a separate message.
_CAPTION_LIMIT = 1024


class TelegramClient:
    def __init__(
        self,
        token: str = "",
        api_base: str = "https://api.telegram.org",
        timeout: float = 30.0,
    ) -> None:
        self._token = token
        self._api_base = api_base.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout) if token else None

    @property
    def enabled(self) -> bool:
        return bool(self._token)

    def _url(self, method: str) -> str:
        return f"{self._api_base}/bot{self._token}/{method}"

    async def get_updates(self, offset: int | None = None, timeout: int = 30) -> list[dict]:
        """Long-poll getUpdates (polling mode). Returns raw update dicts."""
        if not self.enabled:
            return []
        params: dict = {"timeout": timeout, "allowed_updates": '["message"]'}
        if offset is not None:
            params["offset"] = offset
        # Read timeout must exceed the server-side long-poll window.
        resp = await self._client.get(
            self._url("getUpdates"), params=params, timeout=timeout + 10
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("result", []) if data.get("ok") else []

    async def send_text(self, chat_id: int, text: str) -> None:
        """Send a plain text message (acks, greetings, error notices)."""
        if not self.enabled:
            logger.info(
                "telegram disabled — would send text",
                extra={"chars": len(text)},
            )
            return
        await self._send_message(chat_id, text)

    async def send_story_and_image(
        self,
        chat_id: int,
        story_text: str,
        png_bytes: bytes,
        filename: str = "bedtime.png",
    ) -> None:
        """Send the story text + illustration back to the chat. Image carries the
        story as its caption, or the story is a preceding message if it's long."""
        if not self.enabled:
            logger.info(
                "telegram disabled — would send story+image",
                extra={
                    "story_chars": len(story_text),
                    "image_bytes": len(png_bytes),
                },
            )
            return

        if len(story_text) <= _CAPTION_LIMIT:
            await self._send_photo(chat_id, png_bytes, filename, caption=story_text)
        else:
            await self._send_message(chat_id, story_text)
            await self._send_photo(chat_id, png_bytes, filename)

    async def _send_message(self, chat_id: int, text: str) -> None:
        resp = await self._client.post(
            self._url("sendMessage"), data={"chat_id": chat_id, "text": text}
        )
        resp.raise_for_status()

    async def _send_photo(
        self, chat_id: int, png_bytes: bytes, filename: str, caption: str | None = None
    ) -> None:
        data = {"chat_id": str(chat_id)}
        if caption:
            data["caption"] = caption
        files = {"photo": (filename, png_bytes, "image/png")}
        resp = await self._client.post(self._url("sendPhoto"), data=data, files=files)
        resp.raise_for_status()

    async def aclose(self) -> None:
        if self._client:
            await self._client.aclose()
