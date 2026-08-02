"""Gemini on Vertex AI implementation of StoryProvider."""

from __future__ import annotations

import asyncio

from google import genai
from google.genai.types import (
    FinishReason,
    GenerateContentConfig,
    ThinkingConfig,
    ThinkingLevel,
)

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

# Two things make a naive token budget too small, and both bite at once:
#
# 1. Thinking tokens are drawn from the *same* max_output_tokens budget as the
#    visible answer. A budget sized only for the story leaves the model no room
#    to finish it, and the reply is silently cut off mid-sentence.
# 2. Cyrillic and Hebrew tokenize far worse than English — roughly 2-3 tokens
#    per word against ~1.3 — so a word-count budget calibrated on English is
#    short by a factor of two before thinking is even considered.
#
# Budgeting for both is close to free: tokens are billed as generated, not as
# reserved, so the headroom costs nothing on a story that ends early.
_TOKENS_PER_WORD = 4
_THINKING_TOKEN_RESERVE = 2048

# A bedtime story is a low-difficulty generation. MINIMAL still reasons enough
# to follow the ILLUSTRATION output contract without spending the budget.
_THINKING_LEVEL = ThinkingLevel.MINIMAL


class VertexStoryProvider(StoryProvider):
    def __init__(self, settings: Settings) -> None:
        if not settings.gcp_project_id:
            raise ValueError("STORY_PROVIDER=vertex requires GCP_PROJECT_ID to be set")
        self._model_id = settings.vertex_text_model_id
        # Fail at startup, not per job. Falling back to a $0 price meant a
        # model-id bump silently zeroed cost reporting and nothing noticed;
        # crashing here surfaces it at deploy time, while --atomic can still
        # roll the release back.
        if self._model_id not in _TEXT_PRICES_PER_MILLION_TOKENS:
            raise ValueError(
                f"no price entry for text model {self._model_id!r}. Add it to "
                "_TEXT_PRICES_PER_MILLION_TOKENS so per-job cost stays honest. "
                f"Priced: {sorted(_TEXT_PRICES_PER_MILLION_TOKENS)}"
            )
        self._max_words = settings.story_max_words
        self._max_output_tokens = self._max_words * _TOKENS_PER_WORD + _THINKING_TOKEN_RESERVE
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
                max_output_tokens=self._max_output_tokens,
                thinking_config=ThinkingConfig(thinking_level=_THINKING_LEVEL),
            ),
        )

    @staticmethod
    def _finish_reason(response) -> FinishReason | None:
        for candidate in getattr(response, "candidates", None) or []:
            return getattr(candidate, "finish_reason", None)
        return None

    async def generate(self, mode: Mode, prompt: str) -> StoryResult:
        response = await asyncio.to_thread(self._invoke, prompt)

        # Check *why* the model stopped before looking at what it produced.
        # The canonical failure here — thinking consumes the whole budget and
        # no story is emitted — yields empty text with finish_reason
        # MAX_TOKENS, and reporting that as "empty response" hides the cause.
        #
        # Allow only STOP. Rejecting just MAX_TOKENS would still let SAFETY,
        # RECITATION, and BLOCKLIST ship whatever partial text came before the
        # model stopped. A partial story is worse than none: it reaches the
        # child mid-sentence. Fail the job and let the queue retry.
        finish_reason = self._finish_reason(response)
        if finish_reason is not None and finish_reason is not FinishReason.STOP:
            raise RuntimeError(
                f"Vertex AI stopped early: finish_reason={finish_reason!r}, "
                f"max_output_tokens={self._max_output_tokens}"
            )

        raw = response.text or ""
        if not raw:
            raise RuntimeError("Vertex AI returned an empty story response")
        text, hint = split_illustration_hint(raw)
        usage = getattr(response, "usage_metadata", None)
        tokens_in = int(getattr(usage, "prompt_token_count", 0) or 0)
        tokens_out = int(getattr(usage, "candidates_token_count", 0) or 0)
        # Thinking tokens are billed at the output rate but are not part of
        # candidates_token_count, so charging only for visible output
        # understates the real cost of every job.
        tokens_thought = int(getattr(usage, "thoughts_token_count", 0) or 0)
        input_price, output_price = _TEXT_PRICES_PER_MILLION_TOKENS[self._model_id]
        if self._location != "global":
            input_price *= 1.1
            output_price *= 1.1
        billed_out = tokens_out + tokens_thought
        cost = (tokens_in * input_price + billed_out * output_price) / 1_000_000
        return StoryResult(
            text=text,
            model_id=self._model_id,
            cost_usd=round(cost, 6),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            tokens_thought=tokens_thought,
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
