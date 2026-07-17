"""Prompt composition and the bedtime-content guardrails.

The age-appropriate framing lives here so every provider (fake or Bedrock) and
every mode inherits the same constraint from the contract: *gentle,
age-appropriate bedtime content*. This module owns what we say to the *model*;
what the bot says in *chat* lives in i18n.py.
"""

from __future__ import annotations

import random

from .i18n import Lang, resolve
from .models import Character, Mode

# System prompt per story language. Used by BedrockStoryProvider and shared as
# the contract for what a "good" story looks like. Characters are described in
# text only — never from photos of real children. The trailing ILLUSTRATION
# line is always requested in English (the image model's language).
_ILLUSTRATION_LINE_SPEC = (
    "ILLUSTRATION: <one-line English prompt for a children's book "
    "illustration of this story's key scene>"
)

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


def compose_prompt(
    mode: Mode, args: str = "", characters: list[Character] | None = None
) -> str:
    """Turn a command + its free text (+ the saved family cast, for fairytales)
    into the composed scene prompt (the job's `prompt` field). Always wrapped in
    the bedtime guardrail framing."""
    args = (args or "").strip()

    if mode is Mode.FAIRYTALE:
        if characters:
            cast = "; ".join(f"{c.name} — {c.description}" for c in characters)
            extra = f" Extra wishes from the family: {args}." if args else ""
            return (
                f"{_BEDTIME_FRAME} A fairytale starring this family as its heroes: "
                f"{cast}.{extra} Weave them all into one cozy little adventure "
                f"together, keeping each character recognizable."
            )
        roles = args or "mom = kind queen, dad = gentle king, me = brave little knight"
        return (
            f"{_BEDTIME_FRAME} A guided fairytale where the family play these "
            f"roles: {roles}. Weave them into a cozy little adventure."
        )

    if mode is Mode.CUSTOM:
        scene = args or "a calm, happy adventure right before bedtime"
        return f"{_BEDTIME_FRAME} Scene requested by the family: {scene}."

    # Mode.RANDOM — surprise me.
    return f"{_BEDTIME_FRAME} Surprise scenario: {random.choice(_SURPRISE_SCENARIOS)}."


def derive_illustration_prompt(story_text: str) -> str:
    """Derive a one-line illustration prompt *from the generated story* so the
    picture matches the words (contract: story first, then image)."""
    first = ""
    for chunk in story_text.replace("\n", " ").split("."):
        candidate = chunk.strip()
        if candidate:
            first = candidate
            break

    words = first.split()
    summary = " ".join(words[:14]) if words else "a cozy bedtime scene"
    return (
        f"{summary}. Soft watercolor children's book illustration, warm bedtime "
        f"palette, gentle lighting, cozy and reassuring."
    )
