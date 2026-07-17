"""Domain models. `Job` is the **frozen** contract shape — keep it stable so the
stub and the later full app both satisfy it (see docs/api-contract.md)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel


class Mode(str, Enum):
    """Generation mode. Maps 1:1 to the bot commands."""

    FAIRYTALE = "fairytale"
    CUSTOM = "custom"
    RANDOM = "random"


class Job(BaseModel):
    """Frozen job shape (language added additively — defaulted, so messages
    queued by an older web tier still parse):

    {
      "job_id": "uuid",
      "chat_id": 123456,
      "mode": "fairytale | custom | random",
      "prompt": "composed scene description (text)",
      "language": "en | ru | he",
      "created_at": "iso8601"
    }
    """

    job_id: str
    chat_id: int
    mode: Mode
    prompt: str
    # Story output language. Defaults to Russian: every job queued before this
    # field existed was generated in Russian.
    language: str = "ru"
    created_at: str


def new_job(chat_id: int, mode: Mode, prompt: str, language: str = "ru") -> Job:
    """Mint a job with a fresh id and UTC timestamp."""
    return Job(
        job_id=str(uuid.uuid4()),
        chat_id=chat_id,
        mode=mode,
        prompt=prompt,
        language=language,
        created_at=datetime.now(timezone.utc).isoformat(),
    )


class Character(BaseModel):
    """One family member as a story character. `description` is a stylized
    *text* card (from the vision model or typed by hand) — per the product
    constraint, no photo is ever persisted."""

    name: str
    description: str


class ChatProfile(BaseModel):
    """Per-chat state: chosen language + the family story cast. Stored as JSON
    by a ProfileStore adapter (local dir in dev, S3 in prod)."""

    language: str = ""  # empty = not chosen yet (fall back to Telegram's hint)
    characters: list[Character] = []

    def upsert_character(self, name: str, description: str) -> None:
        for c in self.characters:
            if c.name.casefold() == name.casefold():
                c.description = description
                return
        self.characters.append(Character(name=name, description=description))

    def remove_character(self, name: str) -> bool:
        before = len(self.characters)
        self.characters = [c for c in self.characters if c.name.casefold() != name.casefold()]
        return len(self.characters) < before
