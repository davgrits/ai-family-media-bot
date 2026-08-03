from __future__ import annotations

import unittest

from family_media_bot.i18n import DEFAULT_LANG, STRING_KEYS, Lang, resolve, t
from family_media_bot.prompts import BEDTIME_SYSTEM_PROMPTS, system_prompt


class CompletenessTests(unittest.TestCase):
    """The highest-value test in this file.

    A missing translation would otherwise surface as a KeyError at the moment the
    bot tries to speak to a child in that language.
    """

    def test_every_language_defines_every_string(self) -> None:
        for lang in Lang:
            with self.subTest(lang=lang.value):
                for key in STRING_KEYS:
                    self.assertTrue(t(lang, key).strip(), f"{lang.value}/{key} is empty")

    def test_every_language_has_a_system_prompt(self) -> None:
        self.assertEqual(set(BEDTIME_SYSTEM_PROMPTS), set(Lang))

    def test_every_system_prompt_requests_the_english_illustration_line(self) -> None:
        # The sentinel stays English in every story language: it is parsed by
        # story_common, and the image model is the audience for what follows it.
        for lang in Lang:
            with self.subTest(lang=lang.value):
                self.assertIn("ILLUSTRATION:", system_prompt(lang.value))


class ResolveTests(unittest.TestCase):
    def test_supported_codes_resolve_to_themselves(self) -> None:
        for code in ("en", "ru", "he"):
            self.assertEqual(resolve(code).value, code)

    def test_regional_variants_resolve_to_the_base_language(self) -> None:
        # Telegram sends codes like ru-RU in its language_code hint.
        self.assertEqual(resolve("ru-RU"), Lang.RU)
        self.assertEqual(resolve("EN-GB"), Lang.EN)

    def test_unknown_and_missing_codes_fall_back_to_the_default(self) -> None:
        for code in ("fr", "", None, "not-a-language"):
            with self.subTest(code=code):
                self.assertEqual(resolve(code), DEFAULT_LANG)

    def test_default_is_russian_to_match_the_job_field_default(self) -> None:
        # Job.language defaults to "ru" because every job queued before the field
        # existed was Russian. These two defaults must not drift apart.
        self.assertEqual(DEFAULT_LANG, Lang.RU)


class TranslateTests(unittest.TestCase):
    def test_languages_produce_different_text(self) -> None:
        rendered = {t(lang, "ack") for lang in Lang}

        self.assertEqual(len(rendered), len(Lang))

    def test_a_string_key_accepts_either_an_enum_or_a_code(self) -> None:
        self.assertEqual(t(Lang.RU, "ack"), t("ru", "ack"))


if __name__ == "__main__":
    unittest.main()
