"""ProfileStore — persists per-chat state (language + family story cast).

Dev impl: LocalDirProfileStore. Prod impl: S3ProfileStore (profiles/<chat_id>.json,
outside the 30-day-expiry prefix — profiles are durable, generated media is not).
"""

from __future__ import annotations

import abc

from ..models import ChatProfile


class ProfileStore(abc.ABC):
    @abc.abstractmethod
    async def get(self, chat_id: int) -> ChatProfile:
        """Load the chat's profile; a default (empty) profile if none exists."""

    @abc.abstractmethod
    async def put(self, chat_id: int, profile: ChatProfile) -> None:
        """Persist the chat's profile."""

    @abc.abstractmethod
    async def check_ready(self) -> bool:
        """Readiness probe — is the profile backend reachable/writable?"""
