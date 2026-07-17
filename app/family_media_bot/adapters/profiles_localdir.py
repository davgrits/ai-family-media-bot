"""LocalDirProfileStore — dev implementation of ProfileStore. One JSON file per
chat under `<base>/profiles/<chat_id>.json`, mirroring the S3 key layout."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from ..models import ChatProfile
from ..ports.profiles import ProfileStore

logger = logging.getLogger(__name__)


class LocalDirProfileStore(ProfileStore):
    def __init__(self, base_dir: str = "./var/media") -> None:
        self._dir = Path(base_dir) / "profiles"
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, chat_id: int) -> Path:
        return self._dir / f"{chat_id}.json"

    async def get(self, chat_id: int) -> ChatProfile:
        path = self._path(chat_id)
        try:
            raw = await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError:
            return ChatProfile()
        try:
            return ChatProfile.model_validate_json(raw)
        except Exception:
            logger.exception("corrupt profile — starting fresh", extra={"chat_id": chat_id})
            return ChatProfile()

    async def put(self, chat_id: int, profile: ChatProfile) -> None:
        path = self._path(chat_id)
        await asyncio.to_thread(path.write_text, profile.model_dump_json(), "utf-8")

    async def check_ready(self) -> bool:
        try:
            return os.access(self._dir, os.W_OK)
        except Exception:
            return False
