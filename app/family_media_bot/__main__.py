"""Entrypoint: `python -m family_media_bot`. Configures logging, then hands the
ASGI app to uvicorn (whose lifespan starts the worker)."""

from __future__ import annotations

import uvicorn

from .config import get_settings
from .logging_setup import setup_logging


def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level, settings.log_format)
    # Pass the import string so uvicorn owns the app lifecycle; log_config=None
    # keeps our JSON logging instead of uvicorn's default config.
    uvicorn.run(
        "family_media_bot.app:app",
        host=settings.http_host,
        port=settings.http_port,
        log_config=None,
    )


if __name__ == "__main__":
    main()
