"""FakeVisionProvider — returns a canned storybook-character description so the
photo → character flow runs locally with no model calls. Cost is $0."""

from __future__ import annotations

import asyncio

from ..ports.vision import VisionProvider, VisionResult

_CANNED = (
    "{name} is a cheerful storybook hero with a bright, warm smile, twinkling "
    "kind eyes, and a cozy sweater the color of autumn leaves."
)


class FakeVisionProvider(VisionProvider):
    def __init__(self, model_id: str = "fake-vision-v1") -> None:
        self._model_id = model_id

    async def describe_character(
        self, image_bytes: bytes, image_format: str, name: str
    ) -> VisionResult:
        await asyncio.sleep(0)  # stay cooperative on the event loop
        return VisionResult(
            description=_CANNED.format(name=name),
            model_id=self._model_id,
            cost_usd=0.0,
        )

    async def check_ready(self) -> bool:
        return True
