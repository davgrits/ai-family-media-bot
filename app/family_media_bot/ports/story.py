"""StoryProvider — generates the bedtime story text.

Dev impl: FakeStoryProvider. Prod impl: VertexStoryProvider (text model).
"""

from __future__ import annotations

import abc
from dataclasses import dataclass

from ..models import Mode


@dataclass
class StoryResult:
    text: str
    model_id: str
    cost_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    # Reasoning tokens, when the model produces them. Billed at the output
    # rate but excluded from the visible output count, so they are tracked
    # separately rather than folded into tokens_out.
    tokens_thought: int = 0
    # Optional English one-liner for the image model, produced in the same
    # generation as the story so the picture matches the words.
    illustration_hint: str = ""


class StoryProvider(abc.ABC):
    @abc.abstractmethod
    async def generate(self, mode: Mode, prompt: str) -> StoryResult:
        """Produce an age-appropriate bedtime story for the composed prompt."""

    @abc.abstractmethod
    async def check_ready(self) -> bool:
        """Readiness probe — is the story backend reachable/usable?"""
