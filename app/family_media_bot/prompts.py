"""Prompt composition and the bedtime-content guardrails.

The age-appropriate framing lives here so every provider (fake or Vertex AI) and
every mode inherits the same constraint from the contract: *gentle,
age-appropriate bedtime content*. This module owns what we say to the *model*;
what the bot says in *chat* lives in i18n.py.
"""

from __future__ import annotations

import random

from .characters import Character
from .i18n import Lang, resolve
from .models import Mode

# The illustration line is requested in English in every story language, because
# English is the language the image model is good at. Keeping the sentinel itself
# ASCII also keeps the parser in story_common.py language-independent.
#
# The "by name only" clause prevents a conflict rather than resolving one: the
# hint supplies the *scene*, the registry supplies *identity*. Given disjoint
# jobs, the model's description and the committed description cannot contradict
# each other in the first place.
_ILLUSTRATION_LINE_SPEC = (
    "ILLUSTRATION: <one-line English prompt describing this story's key scene. "
    "Refer to characters by name only — do not describe their hair, clothing, "
    "age, or face. Their appearance is supplied separately.>"
)

# The system prompt per story language, and the shared contract for what a
# "good" story looks like. Characters are described in text only — never from
# photographs of real children.
BEDTIME_SYSTEM_PROMPTS: dict[Lang, str] = {
    Lang.EN: (
        "You are a kind bedtime storyteller for young children. Write a short, "
        "warm, calm story in English, about 150–200 words: simple words, "
        "nothing scary or frightening, a cozy ending that helps a child fall "
        "asleep. Characters are described in words only. After the story add "
        "exactly one separate final line strictly in the format:\n"
        f"{_ILLUSTRATION_LINE_SPEC}"
    ),
    Lang.RU: (
        "Ты — добрый рассказчик сказок на ночь для маленьких детей. "
        "Напиши по запросу короткую, тёплую и спокойную сказку на русском языке, "
        "примерно 150–200 слов: простые слова, ничего страшного и пугающего, "
        "уютная концовка, помогающая заснуть. Персонажи описываются только "
        "словами. После сказки добавь ровно одну отдельную последнюю строку "
        "строго в формате:\n"
        f"{_ILLUSTRATION_LINE_SPEC}"
    ),
    Lang.HE: (
        "אתה מספר סיפורים טוב־לב לפני השינה לילדים קטנים. כתוב סיפור קצר, חם "
        "ורגוע בעברית, בערך 150–200 מילים: מילים פשוטות, שום דבר מפחיד, וסוף "
        "נעים שעוזר להירדם. הדמויות מתוארות במילים בלבד. אחרי הסיפור הוסף "
        "בדיוק שורה אחרונה אחת, בפורמט הבא:\n"
        f"{_ILLUSTRATION_LINE_SPEC}"
    ),
}


def system_prompt(language: str) -> str:
    """The bedtime system prompt for a story language ('en' | 'ru' | 'he')."""
    return BEDTIME_SYSTEM_PROMPTS[resolve(language)]


_BEDTIME_FRAME = (
    "Write a short, gentle, age-appropriate bedtime story for young children "
    "(soothing tone, simple language, happy ending, nothing scary)."
)

_SURPRISE_SCENARIOS = [
    "a sleepy little fox who follows fireflies home through a quiet meadow",
    "a small cloud learning to make the softest rain for thirsty flowers",
    "two friendly snails racing very slowly under a big, friendly moon",
    "a baby star that is shy of the dark until the night sky gives it a hug",
    "a curious kitten who tidies the garden so the bees can sleep",
]


# Only the fairytale mode gets the cast. Injecting a character into /custom when
# the text happens to mention their name would need case-insensitive matching
# across `id` plus every name in three scripts, and it fails in both directions:
# a cat named Nika matches inside "Nikaragua", while "Папа" declined as "папе" is
# missed. A fuzzy matcher whose failures are silent is worse than a clear rule.
# Deferred deliberately, not forgotten.
CAST_MODES = frozenset({Mode.FAIRYTALE})

# Used when the registry is empty. Deliberately not the old
# "mom = kind queen, dad = gentle king" string: that was English text spliced
# into a Russian story, which is the ancestor of the bug this work exists to fix.
_NEUTRAL_CAST = "a family of gentle heroes: a parent, a child, and their small animal friend"

# Lifted verbatim from the deleted derive_illustration_prompt — the one part of
# that function worth keeping. "No text or lettering" is new: image models like
# to hallucinate storybook captions, and garbled Latin text baked into the
# picture of a Hebrew story reads as broken.
_IMAGE_STYLE_SUFFIX = (
    "Soft watercolor children's book illustration, warm bedtime palette, gentle "
    "lighting, cozy and reassuring. No text or lettering in the image."
)

_FALLBACK_SCENE = "a cozy bedtime scene from this story"

# Budget for the image prompt, which the adapter caps at 4,000 characters.
# 400 + 8 x ~320 + ~200 of scaffolding fits with room to spare.
_MAX_HINT_CHARS = 400


def compose_request(mode: Mode, args: str = "") -> str:
    """The family's request, framed — this is what goes into `Job.prompt`.

    Contains no character text. The cast is deployment configuration resolved in
    the worker, so a job that sat in the queue across a registry edit cannot be
    generated against a stale cast.
    """
    args = (args or "").strip()

    if mode is Mode.FAIRYTALE:
        # `args` is "extra wishes" now, not "the roles" — which is what a family
        # actually types after /fairytale once the cast lives in a registry.
        extra = f" Extra wishes from the family: {args}." if args else ""
        return f"{_BEDTIME_FRAME} A fairytale starring the family as its heroes.{extra}"

    if mode is Mode.CUSTOM:
        scene = args or "a calm, happy adventure right before bedtime"
        return f"{_BEDTIME_FRAME} Scene requested by the family: {scene}."

    # Mode.RANDOM — surprise me.
    return f"{_BEDTIME_FRAME} Surprise scenario: {random.choice(_SURPRISE_SCENARIOS)}."


def compose_story_prompt(request: str, cast: list[Character], language: str) -> str:
    """The user content for the story model, with `appearance` pasted verbatim.

    Verbatim is the requirement, not a stylistic preference: the exact substring
    in the YAML must be the exact substring in the prompt, because that is what
    makes the same person recognisable across separate generations.

    Names are language-switched; appearance is not. The model reads an English
    description and writes Hebrew or Russian prose, which these models do
    reliably — and an English description is what the *image* model needs, so
    keeping one canonical form avoids two sources of truth.
    """
    if not cast:
        return f"{request}\n\nThe cast: {_NEUTRAL_CAST}."

    # "this character" rather than a pronoun: inferring he/she/it from an
    # appearance description is the kind of guess that is wrong in a way nobody
    # notices until it reaches the child. The model can read the description.
    lines = []
    for character in cast:
        name = character.display_name(language)
        lines.append(
            f'- Call this character "{name}" in the story. '
            f"Appearance: {character.appearance}. Personality: {character.traits}."
        )

    return (
        f"{request}\n\n"
        "The cast, described exactly:\n" + "\n".join(lines) + "\n\n"
        "Weave them all into one cozy little adventure together, keeping each "
        "character recognisable."
    )


def compose_image_prompt(hint: str, cast: list[Character]) -> str:
    """The image-model prompt: the model's scene, then identity, then style.

    Ordering is load-bearing. The scene comes from the story model and varies run
    to run; the appearance comes from a git-committed file reviewed by a human.
    When they disagree about what a child looks like, the reviewed artifact has to
    win, so it is stated last and explicitly marked as an override.
    """
    scene = (hint or "").strip()[:_MAX_HINT_CHARS] or _FALLBACK_SCENE

    parts = [f"Children's book illustration of this scene: {scene}"]

    if cast:
        # Always the English name here, never names[lang]: in an image prompt the
        # name is only a label binding an appearance to a role in the scene, and
        # Cyrillic or Hebrew tokens are noise to the image model at best.
        described = "\n".join(f"- {c.display_name('en')}: {c.appearance}." for c in cast)
        parts.append(f"The characters must look exactly like this, unchanged:\n{described}")

    parts.append(_IMAGE_STYLE_SUFFIX)
    return "\n\n".join(parts)
