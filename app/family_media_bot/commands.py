"""Parse a Telegram update into a bot command.

Pure functions, no I/O. That is what lets the dispatcher be tested without a
Telegram client, a queue, or an event loop, and it is why this module stays free
of the registry and the i18n table — both of which are inputs to a *reply*, not
to parsing.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Mode

# Bot command -> generation mode.
_COMMAND_MODES = {
    "/fairytale": Mode.FAIRYTALE,
    "/custom": Mode.CUSTOM,
    "/surprise": Mode.RANDOM,
}

_LANGUAGE_PREFIX = "lang:"

# Queries answer from local state and enqueue nothing. `Mode` deliberately does
# not grow a member for these: it is the generation mode carried on the Job, and
# a listing is not a kind of story.
FAMILY_QUERY = "family"

_QUERY_COMMANDS = {
    "/family": FAMILY_QUERY,
}


@dataclass
class ParsedCommand:
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


def language_hint(update: dict) -> str:
    """Telegram's own `language_code` for the sender, if it offered one.

    Used only when the message carries no explicit token, so a first-time user
    gets their own language rather than a hardcoded default.
    """
    msg = update.get("message") or update.get("edited_message") or {}
    return ((msg.get("from") or {}).get("language_code")) or ""


def split_language_token(text: str) -> tuple[str, str]:
    """Split a leading `lang:xx` token off a message.

    Returns (language_code, remaining_text); the code is "" when absent. An
    explicit per-message token is the only stateless way to choose a language —
    a picker would imply remembering the choice.
    """
    stripped = text.strip()
    if not stripped.lower().startswith(_LANGUAGE_PREFIX):
        return "", stripped

    token, _, rest = stripped.partition(" ")
    return token[len(_LANGUAGE_PREFIX) :].lower(), rest.strip()


def parse_query(text: str) -> str | None:
    """Return a query name for a read-only command, else None.

    Checked before `parse_text` so a query never becomes a generation job.
    """
    head, _, _ = text.strip().partition(" ")
    return _QUERY_COMMANDS.get(head.split("@", 1)[0].lower())


def parse_text(text: str) -> ParsedCommand | None:
    """Interpret already-cleaned message text.

    A recognised `/command` maps to its mode. Plain text is a custom scene
    request, which is how the bot is actually used. An unrecognised `/command`
    returns None so the caller can answer rather than silently ignore.
    """
    text = text.strip()
    if not text:
        return None

    if not text.startswith("/"):
        return ParsedCommand(mode=Mode.CUSTOM, args=text)

    head, _, rest = text.partition(" ")
    head = head.split("@", 1)[0].lower()  # strip @botname suffix in group chats
    mode = _COMMAND_MODES.get(head)
    if mode is None:
        return None

    return ParsedCommand(mode=mode, args=rest.strip())
