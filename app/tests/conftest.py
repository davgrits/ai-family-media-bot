"""Shared test fixtures: a recording Telegram double + real dev adapters
(in-memory queue, local-dir profiles, fake vision) wired into the Router."""

from __future__ import annotations

import pytest

from family_media_bot.adapters.profiles_localdir import LocalDirProfileStore
from family_media_bot.adapters.queue_inmemory import InMemoryQueue
from family_media_bot.adapters.vision_fake import FakeVisionProvider
from family_media_bot.router import Router
from family_media_bot.telegram import TelegramClient

FAKE_JPEG = b"\xff\xd8\xff\xe0 not really a jpeg"


class RecordingTelegram(TelegramClient):
    """Records outgoing messages instead of calling the Bot API."""

    def __init__(self) -> None:
        super().__init__(token="")
        self.sent: list[dict] = []
        self.answered: list[str] = []

    async def send_text(self, chat_id, text, inline_keyboard=None) -> None:
        self.sent.append({"chat_id": chat_id, "text": text, "keyboard": inline_keyboard})

    async def answer_callback_query(self, callback_query_id) -> None:
        self.answered.append(callback_query_id)

    async def download_file(self, file_id) -> bytes:
        return FAKE_JPEG

    @property
    def texts(self) -> list[str]:
        return [m["text"] for m in self.sent]


@pytest.fixture
def telegram() -> RecordingTelegram:
    return RecordingTelegram()


@pytest.fixture
def queue() -> InMemoryQueue:
    return InMemoryQueue()


@pytest.fixture
def profiles(tmp_path) -> LocalDirProfileStore:
    return LocalDirProfileStore(str(tmp_path))


@pytest.fixture
def router(telegram, queue, profiles) -> Router:
    return Router(telegram, queue, profiles, FakeVisionProvider())


def message(text: str, chat_id: int = 10, language_code: str = "") -> dict:
    upd = {"update_id": 1, "message": {"chat": {"id": chat_id}, "text": text, "from": {}}}
    if language_code:
        upd["message"]["from"]["language_code"] = language_code
    return upd


def photo(caption: str = "", chat_id: int = 10) -> dict:
    msg = {
        "chat": {"id": chat_id},
        "from": {},
        "photo": [
            {"file_id": "small", "file_size": 1_000},
            {"file_id": "big", "file_size": 90_000},
            {"file_id": "huge", "file_size": 9_000_000},  # over the Converse cap
        ],
    }
    if caption:
        msg["caption"] = caption
    return {"update_id": 2, "message": msg}


def callback(data: str, chat_id: int = 10) -> dict:
    return {
        "update_id": 3,
        "callback_query": {
            "id": "cb-1",
            "data": data,
            "from": {},
            "message": {"chat": {"id": chat_id}},
        },
    }
