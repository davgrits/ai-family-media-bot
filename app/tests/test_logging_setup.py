from __future__ import annotations

import logging
import unittest

from family_media_bot.logging_setup import setup_logging


class LoggingSetupTests(unittest.TestCase):
    def test_http_dependency_request_logs_are_suppressed(self) -> None:
        root = logging.getLogger()
        original_handlers = list(root.handlers)
        original_root_level = root.level
        original_levels = {name: logging.getLogger(name).level for name in ("httpx", "httpcore")}
        try:
            setup_logging(level="INFO", fmt="json")

            self.assertEqual(logging.getLogger("httpx").level, logging.WARNING)
            self.assertEqual(logging.getLogger("httpcore").level, logging.WARNING)
        finally:
            root.handlers.clear()
            root.handlers.extend(original_handlers)
            root.setLevel(original_root_level)
            for name, level in original_levels.items():
                logging.getLogger(name).setLevel(level)


if __name__ == "__main__":
    unittest.main()
