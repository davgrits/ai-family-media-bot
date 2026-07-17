from family_media_bot import commands
from family_media_bot.models import Mode

from .conftest import callback, message, photo


def test_parse_mode_commands():
    assert commands.parse(message("/custom про дракона")).mode is Mode.CUSTOM
    assert commands.parse(message("/custom про дракона")).args == "про дракона"
    assert commands.parse(message("/fairytale")).mode is Mode.FAIRYTALE
    assert commands.parse(message("/surprise")).mode is Mode.RANDOM
    assert commands.parse(message("/random")).mode is Mode.RANDOM


def test_parse_strips_botname_suffix():
    parsed = commands.parse(message("/FairyTale@MyBot дракон"))
    assert parsed.mode is Mode.FAIRYTALE
    assert parsed.args == "дракон"


def test_parse_rejects_non_mode_input():
    assert commands.parse(message("/start")) is None
    assert commands.parse(message("hello")) is None
    assert commands.parse(message("")) is None


def test_split_command():
    assert commands.split_command("/family add Dana: desc") == ("/family", "add Dana: desc")
    assert commands.split_command("plain text") == ("", "plain text")


def test_extract_callback():
    assert commands.extract_callback(callback("lang:he")) == ("cb-1", 10, "lang:he")
    assert commands.extract_callback(message("hi")) is None


def test_extract_photo():
    chat_id, caption, sizes = commands.extract_photo(photo("Dana"))
    assert (chat_id, caption) == (10, "Dana")
    assert len(sizes) == 3
    assert commands.extract_photo(message("hi")) is None


def test_extract_language_hint():
    assert commands.extract_language_hint(message("hi", language_code="he")) == "he"
    assert commands.extract_language_hint(message("hi")) == ""
