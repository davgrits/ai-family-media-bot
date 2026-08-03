from __future__ import annotations

import unittest
from pathlib import Path

from family_media_bot.characters import load_registry
from family_media_bot.i18n import Lang
from family_media_bot.models import Mode
from family_media_bot.prompts import (
    CAST_MODES,
    compose_image_prompt,
    compose_request,
    compose_story_prompt,
    system_prompt,
)

REGISTRY = load_registry(
    str(Path(__file__).parent / "fixtures" / "characters_valid.yaml"), required=True
)
CAST = REGISTRY.cast()


class AppearanceVerbatimTests(unittest.TestCase):
    """FR-2's regression test.

    The requirement is one assertion: the exact substring in the YAML is the exact
    substring in both prompts. Anything that summarises, reorders, or translates
    `appearance` breaks character consistency, and character consistency is the
    entire reason the registry exists.
    """

    def test_appearance_is_verbatim_in_the_story_prompt(self) -> None:
        composed = compose_story_prompt(compose_request(Mode.FAIRYTALE), CAST, "ru")

        for character in CAST:
            with self.subTest(character=character.id):
                self.assertIn(character.appearance, composed)

    def test_appearance_is_verbatim_in_the_image_prompt(self) -> None:
        composed = compose_image_prompt("a moonlit pier", CAST)

        for character in CAST:
            with self.subTest(character=character.id):
                self.assertIn(character.appearance, composed)

    def test_appearance_stays_english_in_every_story_language(self) -> None:
        # The model reads an English description and writes localised prose. One
        # canonical appearance means the story and the image agree.
        for lang in Lang:
            composed = compose_story_prompt(compose_request(Mode.FAIRYTALE), CAST, lang.value)
            with self.subTest(lang=lang.value):
                self.assertIn(CAST[0].appearance, composed)


class StoryPromptTests(unittest.TestCase):
    def test_names_are_localised_per_language(self) -> None:
        russian = compose_story_prompt("req", CAST, "ru")
        english = compose_story_prompt("req", CAST, "en")

        self.assertIn("Мила", russian)
        self.assertNotIn("Мила", english)
        self.assertIn("Mila", english)

    def test_traits_reach_the_story_prompt(self) -> None:
        composed = compose_story_prompt("req", CAST, "en")

        self.assertIn("curious, brave, loves animals", composed)

    def test_empty_cast_uses_a_neutral_english_fallback(self) -> None:
        composed = compose_story_prompt("req", [], "ru")

        # Not the old "mom = kind queen, dad = gentle king" string: English text
        # spliced into a Russian story is the ancestor of the bug being fixed.
        self.assertNotIn("kind queen", composed)
        self.assertIn("gentle heroes", composed)

    def test_no_pronoun_is_guessed_from_the_description(self) -> None:
        composed = compose_story_prompt("req", CAST, "en")

        self.assertIn("Call this character", composed)


class ImagePromptTests(unittest.TestCase):
    def test_identity_comes_after_the_scene_and_is_marked_as_an_override(self) -> None:
        composed = compose_image_prompt("Mila walks along a pier", CAST)

        scene_at = composed.index("Mila walks along a pier")
        identity_at = composed.index("must look exactly like this, unchanged")
        style_at = composed.index("Soft watercolor")

        # Later, more specific instructions dominate in practice, and the
        # registry is the human-reviewed artifact, so it must come last.
        self.assertLess(scene_at, identity_at)
        self.assertLess(identity_at, style_at)

    def test_image_prompt_always_uses_english_names(self) -> None:
        # In an image prompt a name is only a label binding an appearance to a
        # role; Cyrillic or Hebrew tokens are noise to the image model.
        composed = compose_image_prompt("a pier", CAST)

        self.assertIn("Mila", composed)
        self.assertNotIn("Мила", composed)

    def test_missing_hint_falls_back_without_losing_the_cast(self) -> None:
        composed = compose_image_prompt("", CAST)

        self.assertIn("a cozy bedtime scene", composed)
        self.assertIn(CAST[0].appearance, composed)

    def test_lettering_is_suppressed(self) -> None:
        # Garbled Latin captions baked into a Hebrew story's picture read as broken.
        self.assertIn("No text or lettering", compose_image_prompt("a pier", CAST))

    def test_a_full_cast_fits_inside_the_adapter_prompt_cap(self) -> None:
        # The arithmetic the annex flagged: 8 characters x 300 chars plus scene
        # and style overran the old 2,000-character cap, which would have sliced
        # off the style suffix and the tail of the cast in silence.
        from family_media_bot.adapters.image_vertex import _MAX_PROMPT_CHARS
        from family_media_bot.characters import _MAX_CHARACTERS

        worst_case = compose_image_prompt("x" * 500, CAST * 3)

        self.assertLessEqual(len(CAST) * 3, _MAX_CHARACTERS * 3)
        self.assertLess(len(worst_case), _MAX_PROMPT_CHARS)


class ComposeRequestTests(unittest.TestCase):
    def test_fairytale_request_carries_no_character_text(self) -> None:
        # This runs in the web tier and its output is stored on the queue.
        composed = compose_request(Mode.FAIRYTALE, "something with a lighthouse")

        for character in CAST:
            with self.subTest(character=character.id):
                self.assertNotIn(character.appearance, composed)
                self.assertNotIn(character.display_name("en"), composed)

    def test_hardcoded_family_roles_are_gone(self) -> None:
        self.assertNotIn("kind queen", compose_request(Mode.FAIRYTALE))

    def test_fairytale_args_become_extra_wishes(self) -> None:
        composed = compose_request(Mode.FAIRYTALE, "something with a lighthouse")

        self.assertIn("Extra wishes", composed)
        self.assertIn("something with a lighthouse", composed)

    def test_only_the_fairytale_mode_gets_the_cast(self) -> None:
        self.assertEqual(CAST_MODES, frozenset({Mode.FAIRYTALE}))

    def test_custom_and_surprise_are_unchanged_in_shape(self) -> None:
        self.assertIn("Scene requested", compose_request(Mode.CUSTOM, "a castle"))
        self.assertIn("Surprise scenario", compose_request(Mode.RANDOM))


class SystemPromptTests(unittest.TestCase):
    def test_the_illustration_line_forbids_describing_appearance(self) -> None:
        # Prevents the conflict rather than resolving it: the hint supplies the
        # scene, the registry supplies identity, so they cannot contradict.
        for lang in Lang:
            with self.subTest(lang=lang.value):
                self.assertIn("by name only", system_prompt(lang.value))


if __name__ == "__main__":
    unittest.main()
