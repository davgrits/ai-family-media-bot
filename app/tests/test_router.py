"""Conversation-flow tests: the Router against real dev adapters and a
recording Telegram double. Every flow the three modes + language picker +
family cast management exercise."""

from __future__ import annotations

import json

from family_media_bot.i18n import CHOOSE_LANGUAGE, t
from family_media_bot.models import Mode

from .conftest import callback, message, photo


async def test_start_offers_language_keyboard(router, telegram):
    result = await router.handle_update(message("/start"))
    assert result == "handled"
    assert telegram.sent[0]["text"] == CHOOSE_LANGUAGE
    buttons = telegram.sent[0]["keyboard"][0]
    assert [b["callback_data"] for b in buttons] == ["lang:en", "lang:ru", "lang:he"]


async def test_language_choice_is_saved_and_menu_localized(router, telegram, profiles):
    await router.handle_update(callback("lang:he"))
    assert telegram.answered == ["cb-1"]
    profile = await profiles.get(10)
    assert profile.language == "he"
    assert t("he", "menu") in telegram.texts[-1]

    # Subsequent replies use the saved language, ignoring Telegram's hint.
    await router.handle_update(message("/custom", language_code="ru"))
    assert telegram.texts[-1] == t("he", "ask_custom")


async def test_reply_language_falls_back_to_telegram_hint(router, telegram):
    await router.handle_update(message("/custom", language_code="ru"))
    assert telegram.texts[-1] == t("ru", "ask_custom")
    # Unsupported hint → English.
    await router.handle_update(message("/custom", language_code="fr"))
    assert telegram.texts[-1] == t("en", "ask_custom")


async def test_plain_text_becomes_custom_job_in_chat_language(router, telegram, queue):
    await router.handle_update(callback("lang:ru"))
    result = await router.handle_update(message("про дракона и маяк"))
    assert result == "accepted"
    job = await queue.dequeue()
    assert job.mode is Mode.CUSTOM
    assert "про дракона и маяк" in job.prompt
    assert job.language == "ru"
    assert telegram.texts[-1] == t("ru", "ack")


async def test_surprise_asks_nothing_and_enqueues(router, telegram, queue):
    result = await router.handle_update(message("/surprise"))
    assert result == "accepted"
    job = await queue.dequeue()
    assert job.mode is Mode.RANDOM
    assert telegram.texts == [t("en", "ack")]  # the only message — no questions


async def test_fairytale_without_cast_asks_for_family(router, telegram, queue):
    result = await router.handle_update(message("/fairytale"))
    assert result == "handled"
    assert telegram.texts[-1] == t("en", "fairytale_need_family")
    assert await queue.depth() == 0


async def test_photo_with_caption_builds_character_and_discards_photo(
    router, telegram, profiles, tmp_path
):
    result = await router.handle_update(photo("Dana"))
    assert result == "handled"

    profile = await profiles.get(10)
    assert [c.name for c in profile.characters] == ["Dana"]
    assert "Dana" in profile.characters[0].description
    assert "Dana" in telegram.texts[-1]

    # The product constraint: nothing binary is persisted — only the JSON card.
    stored = list(tmp_path.rglob("*"))
    assert all(p.suffix == ".json" for p in stored if p.is_file())
    for p in stored:
        if p.is_file():
            json.loads(p.read_text())  # valid JSON, no embedded photo bytes


async def test_photo_without_caption_asks_for_name(router, telegram, profiles):
    result = await router.handle_update(photo(""))
    assert result == "handled"
    assert telegram.texts[-1] == t("en", "photo_needs_caption")
    assert (await profiles.get(10)).characters == []


async def test_fairytale_with_cast_stars_the_family(router, telegram, queue):
    await router.handle_update(photo("Dana"))
    await router.handle_update(message("/family add Papa: a tall gentle giant"))
    result = await router.handle_update(message("/fairytale"))
    assert result == "accepted"
    job = await queue.dequeue()
    assert job.mode is Mode.FAIRYTALE
    assert "Dana" in job.prompt
    assert "Papa — a tall gentle giant" in job.prompt


async def test_family_list_remove_clear(router, telegram, profiles):
    await router.handle_update(message("/family"))
    assert telegram.texts[-1] == t("en", "family_empty")

    await router.handle_update(message("/family add Dana: a cheerful girl"))
    await router.handle_update(message("/family"))
    assert "Dana" in telegram.texts[-1]

    await router.handle_update(message("/family remove Dana"))
    assert telegram.texts[-1] == t("en", "family_removed", name="Dana")

    await router.handle_update(message("/family add Papa: a giant"))
    await router.handle_update(message("/family clear"))
    assert (await profiles.get(10)).characters == []


async def test_family_add_requires_name_colon_description(router, telegram):
    await router.handle_update(message("/family add Dana"))
    assert telegram.texts[-1] == t("en", "family_add_usage")


async def test_photo_caption_upserts_existing_character(router, profiles):
    await router.handle_update(photo("Dana"))
    await router.handle_update(photo("dana"))  # same person, case-insensitive
    assert len((await profiles.get(10)).characters) == 1


async def test_unknown_command_shows_menu(router, telegram):
    result = await router.handle_update(message("/help"))
    assert result == "handled"
    assert telegram.texts[-1] == t("en", "menu")


async def test_language_command_reopens_picker(router, telegram):
    await router.handle_update(callback("lang:ru"))
    await router.handle_update(message("/language"))
    assert telegram.texts[-1] == CHOOSE_LANGUAGE
    assert telegram.sent[-1]["keyboard"] is not None
