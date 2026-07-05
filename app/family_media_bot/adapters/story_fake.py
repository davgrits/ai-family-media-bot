"""FakeStoryProvider — returns a canned, age-appropriate bedtime story so the
full flow runs locally with no model calls. Cost is reported as $0."""

from __future__ import annotations

import asyncio

from ..models import Mode
from ..ports.story import StoryProvider, StoryResult

_CANNED = (
    "Once upon a quiet evening, when the stars were just beginning to blink "
    "awake, a little rabbit named Pip tucked a daisy behind one ear and set off "
    "to say goodnight to the meadow. \"Goodnight, tall grass,\" Pip whispered, "
    "and the grass swayed softly back. \"Goodnight, sleepy stream,\" and the "
    "water gurgled a gentle reply. The moon rose round and kind, wrapping the "
    "hills in a silver blanket. Pip yawned, curled up in a nest of soft clover, "
    "and listened to the crickets sing a slow, cozy song. One by one, the meadow "
    "friends closed their eyes. And as the warm night held them close, Pip "
    "drifted off to sleep, dreaming of soft clouds and tomorrow's gentle sun. "
    "Sweet dreams, little one."
)


class FakeStoryProvider(StoryProvider):
    def __init__(self, model_id: str = "fake-story-v1") -> None:
        self._model_id = model_id

    async def generate(self, mode: Mode, prompt: str) -> StoryResult:
        await asyncio.sleep(0)  # stay cooperative on the event loop
        return StoryResult(
            text=_CANNED,
            model_id=self._model_id,
            cost_usd=0.0,
            tokens_in=len(prompt.split()),
            tokens_out=len(_CANNED.split()),
        )

    async def check_ready(self) -> bool:
        return True
