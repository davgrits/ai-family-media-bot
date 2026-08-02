"""ImageProvider — generates the single illustration for a story.

Dev impl: FakeImageProvider. Prod impl: VertexImageProvider (image model).
"""

from __future__ import annotations

import abc
from dataclasses import dataclass


@dataclass
class ImageResult:
    png_bytes: bytes
    model_id: str
    cost_usd: float = 0.0
    content_type: str = "image/png"


class ImageProvider(abc.ABC):
    @abc.abstractmethod
    async def generate(self, prompt: str) -> ImageResult:
        """Produce one illustration (PNG bytes) for the one-line image prompt."""

    @abc.abstractmethod
    async def check_ready(self) -> bool:
        """Readiness probe — is the image backend reachable/usable?"""
