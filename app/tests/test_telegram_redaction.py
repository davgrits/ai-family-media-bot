"""The bot token lives in every Telegram request URL, so an httpx error message
is a credential leak the moment it is logged. These tests pin both layers of the
defence: TelegramClient never raises a token-bearing exception, and the log
formatters scrub one if it reaches them by another route."""

from __future__ import annotations

import asyncio
import logging
import unittest

import httpx

from family_media_bot.logging_setup import ConsoleFormatter, JsonFormatter, redact_token
from family_media_bot.telegram import TelegramClient, TelegramError

TOKEN = "123456789:AAFakeSecretTokenValueForTests_0123456789"
SECRET = TOKEN.split(":", 1)[1]


def _client(handler) -> TelegramClient:
    client = TelegramClient(token=TOKEN)
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client


class TelegramClientRedactionTests(unittest.TestCase):
    def test_get_updates_http_error_omits_token(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, text='{"ok":false,"description":"Unauthorized"}')

        client = _client(handler)
        with self.assertRaises(TelegramError) as ctx:
            asyncio.run(client.get_updates())

        self._assert_clean(ctx.exception)
        self.assertIn("401", str(ctx.exception))

    def test_get_updates_transport_error_omits_token(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError(f"failed to connect to {request.url}", request=request)

        client = _client(handler)
        with self.assertRaises(TelegramError) as ctx:
            asyncio.run(client.get_updates())

        self._assert_clean(ctx.exception)

    def test_send_text_http_error_omits_token(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="internal error")

        client = _client(handler)
        with self.assertRaises(TelegramError) as ctx:
            asyncio.run(client.send_text(42, "hello"))

        self._assert_clean(ctx.exception)

    def _assert_clean(self, exc: BaseException) -> None:
        """No token in the message, and no chained httpx cause carrying one."""
        seen: list[str] = []
        current: BaseException | None = exc
        while current is not None:
            seen.append(str(current))
            current = current.__cause__ or current.__context__
        rendered = "\n".join(seen)
        self.assertNotIn(SECRET, rendered)
        self.assertNotIn(TOKEN, rendered)


class LogFormatterRedactionTests(unittest.TestCase):
    def _record(self) -> logging.LogRecord:
        try:
            raise httpx.HTTPStatusError(
                f"Client error '401 Unauthorized' for url "
                f"'https://api.telegram.org/bot{TOKEN}/getUpdates?timeout=30'",
                request=None,  # type: ignore[arg-type]
                response=None,  # type: ignore[arg-type]
            )
        except httpx.HTTPStatusError:
            import sys

            return logging.LogRecord(
                name="family_media_bot.poller",
                level=logging.ERROR,
                pathname=__file__,
                lineno=1,
                msg="getUpdates failed — retrying in 3s",
                args=(),
                exc_info=sys.exc_info(),
            )

    def test_json_formatter_redacts_token_in_traceback(self) -> None:
        out = JsonFormatter().format(self._record())
        self.assertNotIn(SECRET, out)
        self.assertIn("bot123456789:<redacted>", out)

    def test_console_formatter_redacts_token_in_traceback(self) -> None:
        out = ConsoleFormatter().format(self._record())
        self.assertNotIn(SECRET, out)
        self.assertIn("bot123456789:<redacted>", out)

    def test_redact_token_keeps_surrounding_text(self) -> None:
        url = f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
        self.assertEqual(
            redact_token(url),
            "https://api.telegram.org/bot123456789:<redacted>/sendPhoto",
        )


if __name__ == "__main__":
    unittest.main()
