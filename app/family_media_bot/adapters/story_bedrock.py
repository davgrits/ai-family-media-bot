"""BedrockStoryProvider — Claude on Amazon Bedrock via the Converse API.

The system prompt asks for the story plus a trailing "ILLUSTRATION: ..." line
(an English one-liner for the image model); we strip that line out of the
story text and expose it as StoryResult.illustration_hint.
"""

from __future__ import annotations

import asyncio
import logging

import boto3
from botocore.config import Config

from ..config import Settings
from ..models import Mode
from ..ports.story import StoryProvider, StoryResult
from ..prompts import BEDTIME_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

# Claude Haiku 4.5 on-demand pricing, USD per 1M tokens.
_PRICE_IN_PER_MTOK = 1.00
_PRICE_OUT_PER_MTOK = 5.00

_ILLUSTRATION_MARKER = "ILLUSTRATION:"

# 60s read timeout + one retry on retryable failures (throttling, timeouts).
_BOTO_CONFIG = Config(
    connect_timeout=5,
    read_timeout=60,
    retries={"max_attempts": 2, "mode": "standard"},
)


def _split_illustration_hint(raw: str) -> tuple[str, str]:
    """Separate the story body from the trailing ILLUSTRATION line (if any)."""
    story_lines: list[str] = []
    hint = ""
    for line in raw.splitlines():
        if line.strip().upper().startswith(_ILLUSTRATION_MARKER):
            hint = line.strip()[len(_ILLUSTRATION_MARKER) :].strip()
        else:
            story_lines.append(line)
    return "\n".join(story_lines).strip(), hint


class BedrockStoryProvider(StoryProvider):
    def __init__(self, settings: Settings) -> None:
        self._model_id = settings.bedrock_text_model_id
        self._max_words = settings.story_max_words
        self._client = boto3.client(
            "bedrock-runtime", region_name=settings.aws_region, config=_BOTO_CONFIG
        )

    def _invoke(self, prompt: str) -> dict:
        return self._client.converse(
            modelId=self._model_id,
            system=[{"text": BEDTIME_SYSTEM_PROMPT}],
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            # Russian words average several tokens; x4 leaves comfortable room.
            inferenceConfig={"maxTokens": max(1024, self._max_words * 4)},
        )

    async def generate(self, mode: Mode, prompt: str) -> StoryResult:
        resp = await asyncio.to_thread(self._invoke, prompt)
        raw = resp["output"]["message"]["content"][0]["text"]
        text, hint = _split_illustration_hint(raw)
        usage = resp.get("usage", {})
        tokens_in = int(usage.get("inputTokens", 0))
        tokens_out = int(usage.get("outputTokens", 0))
        cost = (tokens_in * _PRICE_IN_PER_MTOK + tokens_out * _PRICE_OUT_PER_MTOK) / 1_000_000
        return StoryResult(
            text=text,
            model_id=self._model_id,
            cost_usd=round(cost, 6),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            illustration_hint=hint,
        )

    async def check_ready(self) -> bool:
        # Cheap local probe: credentials resolvable. No Bedrock call from
        # /readyz (each invoke costs money and takes seconds).
        try:
            return boto3.Session().get_credentials() is not None
        except Exception:
            return False
