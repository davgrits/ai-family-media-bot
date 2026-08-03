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
    """Frozen job shape:

    {
      "job_id": "uuid",
      "chat_id": 123456,
      "mode": "fairytale | custom | random",
      "prompt": "composed scene description (text)",
      "language": "en | ru | he",
      "created_at": "iso8601"
    }

    `language` was added additively, with a default, so a message published by an
    older web tier still parses in a newer worker. It is the one sanctioned change
    to this shape; see docs/api-contract.md on what is frozen and what is not.

    Note what is *not* here: character descriptions. Those are deployment
    configuration the worker resolves from its own copy, so a job that sat in the
    queue across a registry edit cannot be generated against a stale cast.
    """

    job_id: str
    chat_id: int
    mode: Mode
    prompt: str
    # Defaults to Russian because every job queued before this field existed was
    # generated in Russian. A different default would silently re-language jobs
    # already in flight at deploy time.
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
