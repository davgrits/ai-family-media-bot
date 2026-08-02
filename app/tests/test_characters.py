from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from family_media_bot.characters import (
    CharacterRegistry,
    RegistryError,
    load_registry,
    render_family_list,
)

FIXTURES = Path(__file__).parent / "fixtures"
VALID = str(FIXTURES / "characters_valid.yaml")


def _write(body: str) -> str:
    """Write a registry to a temp file and return its path."""
    handle = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8")
    handle.write(body)
    handle.close()
    return handle.name


class LoadRegistryTests(unittest.TestCase):
    def test_valid_registry_loads_every_character(self) -> None:
        registry = load_registry(VALID, required=True)

        self.assertEqual(registry.ids(), ["mila", "dad", "nika"])
        self.assertFalse(registry.is_empty())

    def test_appearance_is_preserved_verbatim(self) -> None:
        # This is the whole contract of the registry: the string reaches the
        # image prompt unchanged, which is what makes a character repeatable.
        registry = load_registry(VALID)
        mila = registry.cast()[0]

        self.assertEqual(
            mila.appearance,
            "5-year-old girl, curly red hair in two short braids, freckles, green hooded cloak",
        )

    def test_absent_registry_is_tolerated_when_not_required(self) -> None:
        # make run and the kind smoke test legitimately mount no registry.
        registry = load_registry("/nonexistent/characters.yaml", required=False)

        self.assertTrue(registry.is_empty())

    def test_absent_registry_is_fatal_when_required(self) -> None:
        with self.assertRaisesRegex(RegistryError, "not found"):
            load_registry("/nonexistent/characters.yaml", required=True)

    def test_unset_path_is_fatal_when_required(self) -> None:
        with self.assertRaisesRegex(RegistryError, "required but unset"):
            load_registry("", required=True)

    def test_malformed_yaml_is_fatal_even_when_not_required(self) -> None:
        # Malformed is never degraded: a typo must not reach a child, and the
        # already-running pod should keep running instead.
        path = _write("version: 1\ncharacters: [oops\n")

        with self.assertRaisesRegex(RegistryError, "not valid YAML"):
            load_registry(path, required=False)

    def test_empty_cast_is_legal(self) -> None:
        # An operator emptying the cast is a coherent intent; refusing to boot
        # over it would be worse than starting with no characters.
        path = _write("version: 1\ncharacters: []\n")

        registry = load_registry(path)

        self.assertTrue(registry.is_empty())


class ValidationTests(unittest.TestCase):
    def _expect_error(self, body: str, pattern: str) -> None:
        with self.assertRaisesRegex(RegistryError, pattern):
            load_registry(_write(body))

    def _one_character(self, **overrides: str) -> str:
        fields = {
            "id": "mila",
            "appearance": "5-year-old girl, curly red hair, freckles, green cloak",
            "traits": "curious and brave",
        }
        fields.update(overrides)
        return (
            "version: 1\n"
            "characters:\n"
            f"  - id: {fields['id']}\n"
            f'    appearance: "{fields["appearance"]}"\n'
            f'    traits: "{fields["traits"]}"\n'
            "    names:\n"
            "      en: Mila\n"
        )

    def test_cyrillic_appearance_is_rejected(self) -> None:
        # The single most valuable validation rule here. A Cyrillic appearance
        # string still generates an image, just a less repeatable one — so
        # without this the feature degrades invisibly.
        self._expect_error(
            self._one_character(appearance="девочка пяти лет с рыжими кудрями и веснушками"),
            "CYRILLIC",
        )

    def test_hebrew_traits_are_rejected(self) -> None:
        self._expect_error(
            self._one_character(traits="ילדה סקרנית ואמיצה מאוד"),
            "HEBREW",
        )

    def test_latin_accents_are_allowed(self) -> None:
        registry = load_registry(
            _write(self._one_character(appearance="girl with café-au-lait skin and dark curls"))
        )

        self.assertEqual(len(registry.cast()), 1)

    def test_duplicate_id_is_rejected(self) -> None:
        body = (
            "version: 1\n"
            "characters:\n"
            '  - id: mila\n    appearance: "girl with red curly hair and freckles"\n'
            '    traits: "brave"\n    names:\n      en: Mila\n'
            '  - id: mila\n    appearance: "boy with dark hair and a striped shirt"\n'
            '    traits: "quiet"\n    names:\n      en: Milo\n'
        )
        self._expect_error(body, "duplicates")

    def test_uppercase_id_is_rejected(self) -> None:
        self._expect_error(self._one_character(id="Mila"), r"id must match")

    def test_short_appearance_is_rejected(self) -> None:
        self._expect_error(self._one_character(appearance="girl"), "appearance must be")

    def test_missing_english_name_is_rejected(self) -> None:
        body = (
            "version: 1\n"
            "characters:\n"
            '  - id: mila\n    appearance: "girl with red curly hair and freckles"\n'
            '    traits: "brave"\n    names:\n      ru: Мила\n'
        )
        self._expect_error(body, "names.en is required")

    def test_unsupported_language_key_is_rejected(self) -> None:
        # Catches a `heb:`/`rus:` typo, which would otherwise silently fall back
        # to English forever with no signal that anything was wrong.
        body = (
            "version: 1\n"
            "characters:\n"
            '  - id: mila\n    appearance: "girl with red curly hair and freckles"\n'
            '    traits: "brave"\n    names:\n      en: Mila\n      heb: מילה\n'
        )
        self._expect_error(body, "unsupported language keys")

    def test_unknown_field_is_rejected(self) -> None:
        body = (
            "version: 1\n"
            "characters:\n"
            '  - id: mila\n    appearance: "girl with red curly hair and freckles"\n'
            '    traits: "brave"\n    favourite_colour: green\n    names:\n      en: Mila\n'
        )
        self._expect_error(body, "invalid at")

    def test_unsupported_version_is_rejected(self) -> None:
        self._expect_error("version: 2\ncharacters: []\n", "unsupported registry version")

    def test_error_messages_never_echo_personal_data(self) -> None:
        # Validation errors are logged. Names and descriptions are children's
        # personal data, so a message may name the field but never the value.
        with self.assertRaises(RegistryError) as caught:
            load_registry(_write(self._one_character(appearance="девочка пяти лет")))

        message = str(caught.exception)
        self.assertNotIn("девочка", message)
        self.assertIn("characters[0].appearance", message)


class DisplayNameTests(unittest.TestCase):
    def test_names_fall_back_from_language_to_english_to_id(self) -> None:
        registry = load_registry(VALID)
        mila, _dad, nika = registry.cast()

        self.assertEqual(mila.display_name("ru"), "Мила")
        self.assertEqual(mila.display_name("he"), "מילה")
        # nika has only names.en, so ru falls back rather than failing.
        self.assertEqual(nika.display_name("ru"), "Nika")

    def test_render_family_list_uses_the_active_language(self) -> None:
        registry = load_registry(VALID)

        self.assertEqual(render_family_list(registry, "ru"), ["Мила", "Папа", "Nika"])
        self.assertEqual(render_family_list(registry, "en"), ["Mila", "Dad", "Nika"])

    def test_render_family_list_returns_names_only(self) -> None:
        # Appearance is prompt material, not chat material.
        registry = load_registry(VALID)

        rendered = " ".join(render_family_list(registry, "en"))

        self.assertNotIn("freckles", rendered)

    def test_empty_registry_renders_nothing(self) -> None:
        self.assertEqual(render_family_list(CharacterRegistry(), "en"), [])


if __name__ == "__main__":
    unittest.main()
