"""Gemini on Vertex AI implementation of StoryProvider."""

from __future__ import annotations

import asyncio

from google import genai
from google.genai.types import GenerateContentConfig

from ..config import Settings
from ..models import Mode
from ..ports.story import StoryProvider, StoryResult
from ..prompts import BEDTIME_SYSTEM_PROMPT
from .story_common import split_illustration_hint

_TEXT_PRICES_PER_MILLION_TOKENS = {
    # Standard online inference pricing. Gemini 3+ regional endpoints have a
    # 10% surcharge; the global endpoint keeps the base price.
    "gemini-3.5-flash": (1.50, 9.00),
}


class VertexStoryProvider(StoryProvider):
    def __init__(self, settings: Settings) -> None:
        if not settings.gcp_project_id:
            raise ValueError("STORY_PROVIDER=vertex requires GCP_PROJECT_ID to be set")
        self._model_id = settings.vertex_text_model_id
        self._max_words = settings.story_max_words
        self._location = settings.vertex_text_location
        self._client = genai.Client(
            vertexai=True,
            project=settings.gcp_project_id,
            location=self._location,
        )

    def _invoke(self, prompt: str):
        return self._client.models.generate_content(
            model=self._model_id,
            contents=prompt,
            config=GenerateContentConfig(
                system_instruction=BEDTIME_SYSTEM_PROMPT,
                max_output_tokens=max(1024, self._max_words * 4),
            ),
        )

    async def generate(self, mode: Mode, prompt: str) -> StoryResult:
        response = await asyncio.to_thread(self._invoke, prompt)
        raw = response.text or ""
        if not raw:
            raise RuntimeError("Vertex AI returned an empty story response")
        text, hint = split_illustration_hint(raw)
        usage = getattr(response, "usage_metadata", None)
        tokens_in = int(getattr(usage, "prompt_token_count", 0) or 0)
        tokens_out = int(getattr(usage, "candidates_token_count", 0) or 0)
        input_price, output_price = _TEXT_PRICES_PER_MILLION_TOKENS.get(
            self._model_id, (0.0, 0.0)
        )
        if self._location != "global":
            input_price *= 1.1
            output_price *= 1.1
        cost = (tokens_in * input_price + tokens_out * output_price) / 1_000_000
        return StoryResult(
            text=text,
            model_id=self._model_id,
            cost_usd=round(cost, 6),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            illustration_hint=hint,
        )

    async def check_ready(self) -> bool:
        # ADC resolution is local and free; readiness must not invoke a billed
        # Vertex model on every probe.
        try:
            import google.auth

            await asyncio.to_thread(google.auth.default)
            return True
        except Exception:
            return False
