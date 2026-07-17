"""VisionProvider — turns a family member's photo into a *text* character card.

The photo is processed in memory and discarded; only the description is ever
stored (the repo's "no real photos persisted" constraint). Descriptions are in
English regardless of chat language — they feed both the story prompt (the
text model translates naturally) and the English-only illustration prompt.

Dev impl: FakeVisionProvider. Prod impl: BedrockVisionProvider (Claude, vision).
"""

from __future__ import annotations

import abc
from dataclasses import dataclass


@dataclass
class VisionResult:
    description: str
    model_id: str
    cost_usd: float = 0.0


class VisionProvider(abc.ABC):
    @abc.abstractmethod
    async def describe_character(
        self, image_bytes: bytes, image_format: str, name: str
    ) -> VisionResult:
        """Produce a short, stylized storybook-character description of the
        person in the photo. `image_format` is e.g. "jpeg" or "png"."""

    @abc.abstractmethod
    async def check_ready(self) -> bool:
        """Readiness probe — is the vision backend reachable/usable?"""
