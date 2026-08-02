"""StoragePort — persists the generated image.

Dev impl: LocalDirStorage. Prod impl: GcsStorage (gs://<bucket>/generated/<id>.png).
"""

from __future__ import annotations

import abc


class StoragePort(abc.ABC):
    @abc.abstractmethod
    async def save(self, job_id: str, data: bytes, content_type: str = "image/png") -> str:
        """Persist bytes for a job and return a locator (local path or gs:// URI)."""

    @abc.abstractmethod
    async def check_ready(self) -> bool:
        """Readiness probe — is the storage backend reachable/writable?"""
