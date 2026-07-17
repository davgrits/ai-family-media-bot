"""Minimal Telegram Bot API client. With no token configured (local dev) it logs
what it *would* send instead of calling the network — so the full flow runs with
no external dependencies."""

from __future__ import annotations

import json
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
        params: dict = {
            "timeout": timeout,
            # callback_query carries the language-picker button presses.
            "allowed_updates": '["message","callback_query"]',
        }
        if offset is not None:
            params["offset"] = offset
        # Read timeout must exceed the server-side long-poll window.
        resp = await self._client.get(
            self._url("getUpdates"), params=params, timeout=timeout + 10
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("result", []) if data.get("ok") else []

    async def send_text(
        self, chat_id: int, text: str, inline_keyboard: list[list[dict]] | None = None
    ) -> None:
        """Send a plain text message (acks, greetings, error notices). Pass
        `inline_keyboard` (rows of {"text", "callback_data"}) for button menus
        like the language picker."""
        if not self.enabled:
            logger.info(
                "telegram disabled — would send text",
                extra={
                    "chat_id": chat_id,
                    "chars": len(text),
                    "buttons": bool(inline_keyboard),
                },
            )
            return
        await self._send_message(chat_id, text, inline_keyboard)

    async def answer_callback_query(self, callback_query_id: str) -> None:
        """Ack a button press so the client stops showing its spinner."""
        if not self.enabled:
            logger.info("telegram disabled — would answer callback")
            return
        resp = await self._client.post(
            self._url("answerCallbackQuery"), data={"callback_query_id": callback_query_id}
        )
        resp.raise_for_status()

    async def download_file(self, file_id: str) -> bytes:
        """Fetch a file's bytes by file_id (getFile → download). Used for the
        family-photo → character flow; bytes stay in memory, never on disk."""
        if not self.enabled:
            raise RuntimeError("telegram is disabled — no token configured")
        resp = await self._client.get(self._url("getFile"), params={"file_id": file_id})
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"getFile failed: {data}")
        file_path = data["result"]["file_path"]
        resp = await self._client.get(f"{self._api_base}/file/bot{self._token}/{file_path}")
        resp.raise_for_status()
        return resp.content

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
                    "chat_id": chat_id,
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

    async def _send_message(
        self, chat_id: int, text: str, inline_keyboard: list[list[dict]] | None = None
    ) -> None:
        data: dict = {"chat_id": chat_id, "text": text}
        if inline_keyboard:
            data["reply_markup"] = json.dumps({"inline_keyboard": inline_keyboard})
        resp = await self._client.post(self._url("sendMessage"), data=data)
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
