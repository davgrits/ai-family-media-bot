"""Prompt composition and the bedtime-content guardrails.

The age-appropriate framing lives here so every provider (fake or Bedrock) and
every mode inherits the same constraint from the contract: *gentle,
age-appropriate bedtime content*.
"""

from __future__ import annotations

import random

from .models import Mode

# Used as the system prompt by BedrockStoryProvider, and as the shared contract
# for what a "good" story looks like. Characters are described in text only —
# never from photos of real children.
BEDTIME_SYSTEM_PROMPT = (
    "Ты — добрый рассказчик сказок на ночь для маленьких детей. "
    "Напиши по запросу короткую, тёплую и спокойную сказку на русском языке, "
    "примерно 150–200 слов: простые слова, ничего страшного и пугающего, "
    "уютная концовка, помогающая заснуть. Персонажи описываются только "
    "словами. После сказки добавь ровно одну отдельную последнюю строку "
    "строго в формате:\n"
    "ILLUSTRATION: <one-line English prompt for a children's book "
    "illustration of this story's key scene>"
)

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


def compose_prompt(mode: Mode, args: str = "") -> str:
    """Turn a command + its free text into the composed scene prompt (the job's
    `prompt` field). Always wrapped in the bedtime guardrail framing."""
    args = (args or "").strip()

    if mode is Mode.FAIRYTALE:
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
