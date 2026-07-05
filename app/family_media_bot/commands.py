"""Parse a Telegram update into a bot command. Pure functions — no I/O — so the
webhook handler stays fast and easy to test."""

from __future__ import annotations

from dataclasses import dataclass

from .models import Mode

# Bot command -> generation mode (MVP set from the contract).
_COMMAND_MODES = {
    "/fairytale": Mode.FAIRYTALE,
    "/custom": Mode.CUSTOM,
    "/surprise": Mode.RANDOM,
}


@dataclass
class ParsedCommand:
    chat_id: int
    mode: Mode
    args: str


def extract_message(update: dict) -> tuple[int | None, str]:
    """Pull (chat_id, text) out of a Telegram update, tolerating edited messages
    and captions."""
    msg = update.get("message") or update.get("edited_message") or {}
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    text = msg.get("text") or msg.get("caption") or ""
    return chat_id, text


def parse(update: dict) -> ParsedCommand | None:
    """Return a ParsedCommand for a recognized `/command`, else None (which the
    webhook treats as 'ignore but still 200')."""
    chat_id, text = extract_message(update)
    if chat_id is None or not text:
        return None

    text = text.strip()
    if not text.startswith("/"):
        return None

    head, _, rest = text.partition(" ")
    head = head.split("@", 1)[0].lower()  # strip @botname suffix in group chats
    mode = _COMMAND_MODES.get(head)
    if mode is None:
        return None

    return ParsedCommand(chat_id=chat_id, mode=mode, args=rest.strip())
