from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from family_media_bot import commands
from family_media_bot.characters import CharacterRegistry, load_registry
from family_media_bot.dispatch import ACCEPTED, HANDLED, IGNORED, Dispatcher
from family_media_bot.i18n import Lang, t
from family_media_bot.models import Mode


def update(text: str, chat_id: int = 123, language_code: str | None = None) -> dict:
    message: dict = {"chat": {"id": chat_id}, "text": text}
    if language_code:
        message["from"] = {"language_code": language_code}
    return {"update_id": 1, "message": message}


class ParseTextTests(unittest.TestCase):
    def test_plain_text_is_a_custom_scene_request(self) -> None:
        parsed = commands.parse_text("a dragon and a lighthouse")

        self.assertEqual(parsed.mode, Mode.CUSTOM)
        self.assertEqual(parsed.args, "a dragon and a lighthouse")

    def test_known_commands_map_to_their_modes(self) -> None:
        for text, mode in (
            ("/fairytale", Mode.FAIRYTALE),
            ("/custom a castle", Mode.CUSTOM),
            ("/surprise", Mode.RANDOM),
        ):
            with self.subTest(text=text):
                self.assertEqual(commands.parse_text(text).mode, mode)

    def test_botname_suffix_is_stripped(self) -> None:
        # Group chats deliver /fairytale@my_bot.
        self.assertEqual(commands.parse_text("/fairytale@family_bot").mode, Mode.FAIRYTALE)

    def test_unknown_command_returns_none_so_the_caller_can_answer(self) -> None:
        self.assertIsNone(commands.parse_text("/start"))
        self.assertIsNone(commands.parse_text("/nonsense"))


class LanguageTokenTests(unittest.TestCase):
    def test_token_is_split_off_and_lowercased(self) -> None:
        self.assertEqual(commands.split_language_token("lang:EN a dragon"), ("en", "a dragon"))

    def test_absent_token_leaves_the_text_intact(self) -> None:
        self.assertEqual(commands.split_language_token("a dragon"), ("", "a dragon"))

    def test_token_may_be_the_whole_message(self) -> None:
        self.assertEqual(commands.split_language_token("lang:he"), ("he", ""))

    def test_token_only_counts_at_the_start(self) -> None:
        # Otherwise "a story about lang:en" would silently change language.
        self.assertEqual(
            commands.split_language_token("a story about lang:en"),
            ("", "a story about lang:en"),
        )


REGISTRY = load_registry(
    str(Path(__file__).parent / "fixtures" / "characters_valid.yaml"), required=True
)


class DispatcherTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.telegram = AsyncMock()
        self.queue = AsyncMock()
        self.dispatcher = Dispatcher(self.telegram, self.queue, REGISTRY)

    async def _enqueued(self):
        self.queue.enqueue.assert_awaited_once()
        return self.queue.enqueue.await_args.args[0]

    async def test_plain_text_enqueues_a_custom_job_and_acknowledges(self) -> None:
        result = await self.dispatcher.handle_update(update("a dragon and a lighthouse"))

        self.assertEqual(result, ACCEPTED)
        job = await self._enqueued()
        self.assertEqual(job.mode, Mode.CUSTOM)
        self.assertEqual(job.chat_id, 123)
        self.telegram.send_text.assert_awaited_once_with(123, t(Lang.RU, "ack"))

    async def test_language_token_sets_the_job_language_and_the_reply(self) -> None:
        result = await self.dispatcher.handle_update(update("lang:en /fairytale"))

        self.assertEqual(result, ACCEPTED)
        job = await self._enqueued()
        self.assertEqual(job.language, "en")
        self.assertEqual(job.mode, Mode.FAIRYTALE)
        # The acknowledgement is in the requested language, not the default.
        self.telegram.send_text.assert_awaited_once_with(123, t(Lang.EN, "ack"))

    async def test_telegram_language_hint_is_used_when_no_token_is_given(self) -> None:
        await self.dispatcher.handle_update(update("/surprise", language_code="he-IL"))

        job = await self._enqueued()
        self.assertEqual(job.language, "he")

    async def test_explicit_token_beats_the_telegram_hint(self) -> None:
        await self.dispatcher.handle_update(update("lang:ru /surprise", language_code="en"))

        job = await self._enqueued()
        self.assertEqual(job.language, "ru")

    async def test_unsupported_hint_falls_back_to_the_default(self) -> None:
        await self.dispatcher.handle_update(update("/surprise", language_code="fr"))

        job = await self._enqueued()
        self.assertEqual(job.language, "ru")

    async def test_unknown_command_is_answered_without_enqueueing(self) -> None:
        result = await self.dispatcher.handle_update(update("/start"))

        self.assertEqual(result, HANDLED)
        self.queue.enqueue.assert_not_awaited()
        self.telegram.send_text.assert_awaited_once_with(123, t(Lang.RU, "greeting"))

    async def test_a_bare_language_token_shows_the_menu_in_that_language(self) -> None:
        result = await self.dispatcher.handle_update(update("lang:he"))

        self.assertEqual(result, HANDLED)
        self.queue.enqueue.assert_not_awaited()
        self.telegram.send_text.assert_awaited_once_with(123, t(Lang.HE, "menu"))

    async def test_updates_without_a_chat_or_text_are_ignored(self) -> None:
        for payload in ({"update_id": 1}, {"update_id": 1, "message": {"chat": {"id": 1}}}):
            with self.subTest(payload=payload):
                self.assertEqual(await self.dispatcher.handle_update(payload), IGNORED)

        self.queue.enqueue.assert_not_awaited()
        self.telegram.send_text.assert_not_awaited()

    async def test_family_list_answers_without_enqueueing(self) -> None:
        # A listing is a query, not a kind of story.
        result = await self.dispatcher.handle_update(update("/family"))

        self.assertEqual(result, HANDLED)
        self.queue.enqueue.assert_not_awaited()

    async def test_family_list_uses_the_requested_language(self) -> None:
        await self.dispatcher.handle_update(update("lang:ru /family"))

        sent = self.telegram.send_text.await_args.args[1]
        self.assertIn("Мила", sent)
        self.assertIn(t(Lang.RU, "family_list_header"), sent)

    async def test_family_list_never_reveals_appearance(self) -> None:
        # Appearance is prompt material. A chat log is one more place a child's
        # physical description would otherwise come to live.
        await self.dispatcher.handle_update(update("lang:en /family"))

        sent = self.telegram.send_text.await_args.args[1]
        self.assertIn("Mila", sent)
        self.assertNotIn("freckles", sent)
        self.assertNotIn("loves animals", sent)

    async def test_family_list_on_an_empty_registry_explains_itself(self) -> None:
        dispatcher = Dispatcher(self.telegram, self.queue, CharacterRegistry())

        await dispatcher.handle_update(update("lang:en /family"))

        self.telegram.send_text.assert_awaited_once_with(123, t(Lang.EN, "family_empty"))

    async def test_no_character_text_travels_on_the_queue(self) -> None:
        # The cast is deployment configuration resolved in the worker, so a job
        # sitting in the queue across a registry edit cannot use a stale cast.
        await self.dispatcher.handle_update(update("lang:en /fairytale"))

        job = await self._enqueued()
        self.assertNotIn("appearance", job.prompt)
        self.assertNotIn("David", job.prompt)


if __name__ == "__main__":
    unittest.main()
