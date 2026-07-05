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
      "created_at": "iso8601"
    }
    """

    job_id: str
    chat_id: int
    mode: Mode
    prompt: str
    created_at: str


def new_job(chat_id: int, mode: Mode, prompt: str) -> Job:
    """Mint a job with a fresh id and UTC timestamp."""
    return Job(
        job_id=str(uuid.uuid4()),
        chat_id=chat_id,
        mode=mode,
        prompt=prompt,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
