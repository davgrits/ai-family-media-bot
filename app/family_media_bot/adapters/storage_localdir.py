"""LocalDirStorage — dev implementation of StoragePort. Writes PNGs under
`<base>/generated/<job_id>.png`, mirroring the S3 key layout from the contract."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from ..ports.storage import StoragePort

logger = logging.getLogger(__name__)


class LocalDirStorage(StoragePort):
    def __init__(self, base_dir: str = "./var/media") -> None:
        self._base = Path(base_dir)
        (self._base / "generated").mkdir(parents=True, exist_ok=True)

    async def save(self, job_id: str, data: bytes, content_type: str = "image/png") -> str:
        ext = {"image/png": "png", "text/plain": "txt"}.get(content_type, "bin")
        path = self._base / "generated" / f"{job_id}.{ext}"
        await asyncio.to_thread(path.write_bytes, data)
        logger.info(
            "stored media locally",
            extra={"job_id": job_id, "path": str(path), "bytes": len(data)},
        )
        return str(path)

    async def check_ready(self) -> bool:
        try:
            return os.access(self._base, os.W_OK)
        except Exception:
            return False
