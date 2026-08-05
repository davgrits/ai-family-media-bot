"""Structured JSON logging. Emits one JSON object per line on stdout, enriches
records with any `extra={...}` fields, and attaches the active OTel trace/span
ids when a trace is in progress."""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timezone

# Attributes present on a vanilla LogRecord — everything else is treated as a
# caller-supplied `extra` and gets merged into the JSON payload.
_RESERVED = set(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName"}

# Telegram API URLs embed the bot token in the path (`/bot<id>:<secret>/method`),
# so anything that echoes a request URL — an httpx exception message, a
# traceback, a hand-written log line — leaks the credential. The bot id (the
# digits before the colon) is not secret and is kept for correlation.
_TOKEN_RE = re.compile(r"(bot)(\d+):[A-Za-z0-9_-]+")


def redact_token(text: str) -> str:
    """Replace any Telegram bot token in `text` with a placeholder."""
    return _TOKEN_RE.sub(r"\1\2:<redacted>", text)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }

        # Best-effort OTel correlation; never fail logging because of it.
        try:
            from opentelemetry import trace

            ctx = trace.get_current_span().get_span_context()
            if ctx and ctx.is_valid:
                payload["trace_id"] = format(ctx.trace_id, "032x")
                payload["span_id"] = format(ctx.span_id, "016x")
        except Exception:
            pass

        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)

        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value

        return redact_token(json.dumps(payload, default=str, ensure_ascii=False))


class ConsoleFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)-7s %(name)s :: %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        return redact_token(super().format(record))


def setup_logging(level: str = "INFO", fmt: str = "json") -> None:
    """(Re)configure root logging. Idempotent — safe to call from both the
    `__main__` entrypoint and `create_app()`."""
    root = logging.getLogger()
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if fmt == "json" else ConsoleFormatter())
    root.addHandler(handler)
    root.setLevel(level.upper())

    # Let uvicorn's loggers flow through our root handler instead of their own.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True

    # httpx includes full request URLs in INFO records. Telegram embeds the bot
    # token in the URL path, so dependency request logs must never be emitted.
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
