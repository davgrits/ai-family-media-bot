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
    "/random": Mode.RANDOM,
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


def extract_photo(update: dict) -> tuple[int, str, list[dict]] | None:
    """Return (chat_id, caption, photo_sizes) for a photo message, else None.
    `photo_sizes` is Telegram's PhotoSize list, smallest first."""
    msg = update.get("message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    photos = msg.get("photo") or []
    if chat_id is None or not photos:
        return None
    return chat_id, (msg.get("caption") or "").strip(), photos


def extract_callback(update: dict) -> tuple[str, int, str] | None:
    """Return (callback_query_id, chat_id, data) for a button press, else None."""
    cq = update.get("callback_query") or {}
    chat_id = ((cq.get("message") or {}).get("chat") or {}).get("id")
    if not cq.get("id") or chat_id is None:
        return None
    return cq["id"], chat_id, cq.get("data") or ""


def extract_language_hint(update: dict) -> str:
    """Telegram's `from.language_code` for the update's sender ("" if absent).
    Used as the reply language before the user explicitly picks one."""
    source = update.get("message") or update.get("edited_message") or update.get(
        "callback_query"
    ) or {}
    return ((source.get("from") or {}).get("language_code")) or ""


def split_command(text: str) -> tuple[str, str]:
    """Split "/cmd@bot args" into ("/cmd", "args"); ("", text) if not a command."""
    text = text.strip()
    if not text.startswith("/"):
        return "", text
    head, _, rest = text.partition(" ")
    return head.split("@", 1)[0].lower(), rest.strip()


def parse(update: dict) -> ParsedCommand | None:
    """Return a ParsedCommand for a recognized mode `/command`, else None (which
    the caller treats as 'not a generation request')."""
    chat_id, text = extract_message(update)
    if chat_id is None or not text:
        return None

    head, rest = split_command(text)
    mode = _COMMAND_MODES.get(head)
    if mode is None:
        return None

    return ParsedCommand(chat_id=chat_id, mode=mode, args=rest)
