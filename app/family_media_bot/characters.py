"""The family's story cast.

Deployment configuration, deliberately not part of `models.py`: that module holds
the frozen queue contract, and the registry sits on the other side of that
boundary. Character text never travels on the queue — the worker resolves it from
its own copy, so a job cannot be generated against a stale cast.

`appearance` and `traits` are English on purpose. Image models are trained
overwhelmingly on English captions, so an English string is both better and, more
importantly, more *repeatable* — which is the whole point: the same child has to
look like the same child in every illustration.

Privacy: character names are children's given names. Only `id` and counts are
ever safe to log. See `_LOGGABLE` below.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

logger = logging.getLogger(__name__)

SUPPORTED_LANGUAGES = ("en", "ru", "he")

_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")

# A cast has to fit in the image prompt alongside the scene. Eight is generous
# for a family and still leaves the model room to compose.
_MAX_CHARACTERS = 8

_APPEARANCE_RANGE = (10, 300)
_TRAITS_RANGE = (3, 200)
_MAX_NAME_LENGTH = 40

# Scripts that indicate `appearance`/`traits` were written in the story language
# rather than English. This is an error and not a warning: a Cyrillic appearance
# string silently degrades the exact property the registry exists to provide, and
# a silent degradation is indistinguishable from success. Latin-1 accents stay
# legal, so "café-au-lait" is fine.
_NON_LATIN_SCRIPTS = ("CYRILLIC", "HEBREW", "ARABIC", "CJK", "HIRAGANA", "KATAKANA", "HANGUL")


class RegistryError(ValueError):
    """The registry is present but unusable. Fatal at startup, never degraded.

    A partial registry is worse than none: the failure mode is a child quietly
    missing from their own bedtime story.
    """


class Character(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    appearance: str
    traits: str
    names: dict[str, str]

    def display_name(self, language: str) -> str:
        """`names[language]` → `names['en']` → `id`."""
        return self.names.get(language) or self.names.get("en") or self.id


class CharacterRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    characters: list[Character] = []

    def is_empty(self) -> bool:
        return not self.characters

    def cast(self) -> list[Character]:
        return list(self.characters)

    def ids(self) -> list[str]:
        return [c.id for c in self.characters]


def _contains_non_latin_script(value: str) -> str | None:
    """Return the offending script name, or None. Never returns the value."""
    for char in value:
        if char.isascii():
            continue
        try:
            name = unicodedata.name(char)
        except ValueError:
            continue
        for script in _NON_LATIN_SCRIPTS:
            if name.startswith(script):
                return script
    return None


def _validate(registry: CharacterRegistry) -> None:
    """Validation beyond what the model shape enforces.

    Error messages name the field and index only. They must never echo the
    value, because the values are personal data.
    """
    if registry.version != 1:
        raise RegistryError(f"unsupported registry version {registry.version!r}; expected 1")

    if len(registry.characters) > _MAX_CHARACTERS:
        raise RegistryError(
            f"{len(registry.characters)} characters exceeds the maximum of {_MAX_CHARACTERS}"
        )

    seen: set[str] = set()
    for index, character in enumerate(registry.characters):
        where = f"characters[{index}]"

        if not _ID_PATTERN.match(character.id):
            raise RegistryError(
                f"{where}.id must match {_ID_PATTERN.pattern} (lowercase, slug-safe)"
            )
        if character.id in seen:
            raise RegistryError(f"{where}.id duplicates an earlier character: {character.id!r}")
        seen.add(character.id)

        for field, (low, high) in (
            ("appearance", _APPEARANCE_RANGE),
            ("traits", _TRAITS_RANGE),
        ):
            text = getattr(character, field)
            if not low <= len(text) <= high:
                raise RegistryError(
                    f"{where}.{field} must be {low}-{high} characters, got {len(text)}"
                )
            script = _contains_non_latin_script(text)
            if script:
                raise RegistryError(
                    f"{where}.{field} contains {script} characters and must be written in "
                    "English — it is reused verbatim in the image prompt, where a "
                    "non-English description silently degrades character consistency"
                )

        if "en" not in character.names or not character.names["en"].strip():
            raise RegistryError(f"{where}.names.en is required")
        unknown = sorted(set(character.names) - set(SUPPORTED_LANGUAGES))
        if unknown:
            raise RegistryError(
                f"{where}.names has unsupported language keys {unknown}; "
                f"expected any of {list(SUPPORTED_LANGUAGES)}"
            )
        for language, name in character.names.items():
            if not name.strip():
                raise RegistryError(f"{where}.names.{language} is empty")
            if len(name) > _MAX_NAME_LENGTH:
                raise RegistryError(
                    f"{where}.names.{language} exceeds {_MAX_NAME_LENGTH} characters"
                )


def load_registry(path: str | None, *, required: bool = False) -> CharacterRegistry:
    """Read and validate the registry.

    Malformed (present, does not validate) is always fatal: the pod fails to
    start, the release rolls back, and the bot that was already running keeps
    running. A typo'd registry must never reach a child.

    Absent is governed by `required` rather than inferred from which providers
    are configured. Inference would be one line shorter and one concept harder
    to explain.
    """
    if not path:
        if required:
            raise RegistryError("CHARACTERS_FILE is required but unset")
        logger.warning("character registry not configured", extra={"characters_loaded": 0})
        return CharacterRegistry()

    file = Path(path)
    if not file.is_file():
        if required:
            raise RegistryError(f"character registry not found at {path!r} and it is required")
        logger.warning("character registry absent", extra={"path": path, "characters_loaded": 0})
        return CharacterRegistry()

    try:
        raw = yaml.safe_load(file.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        # Do not attach the exception: a YAML parse error can quote the document,
        # and the document is personal data.
        raise RegistryError(f"character registry at {path!r} is not valid YAML") from None
    except OSError as exc:
        raise RegistryError(f"character registry at {path!r} could not be read: {exc}") from None

    if raw is None:
        raise RegistryError(f"character registry at {path!r} is empty")
    if not isinstance(raw, dict):
        raise RegistryError(f"character registry at {path!r} must be a mapping")

    try:
        registry = CharacterRegistry.model_validate(raw)
    except ValidationError as exc:
        # Report locations, never values — pydantic's default message echoes input.
        locations = sorted({".".join(str(p) for p in e["loc"]) for e in exc.errors()})
        raise RegistryError(
            f"character registry at {path!r} is invalid at: {', '.join(locations)}"
        ) from None

    _validate(registry)

    if registry.is_empty():
        logger.warning("character registry is empty", extra={"characters_loaded": 0})
    else:
        logger.info(
            "character registry loaded",
            extra={"characters_loaded": len(registry.characters), "character_ids": registry.ids()},
        )
    return registry


def render_family_list(registry: CharacterRegistry, language: str) -> list[str]:
    """Display names for `/family list`, in the active language.

    Returns names only. Appearance and traits are prompt material, not chat
    material — and echoing a child's description back into a chat log is exactly
    what the logging discipline avoids.
    """
    return [c.display_name(language) for c in registry.cast()]
