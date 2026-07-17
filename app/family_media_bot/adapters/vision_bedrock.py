"""BedrockVisionProvider — Claude on Bedrock (Converse API, image input) turns a
family photo into a short stylized character card.

Privacy is the design driver here (see README "No real photos of children
anywhere"): the image bytes go to the model call and nowhere else — never to
storage, never into logs — and the instruction explicitly forbids identifying
details in the output."""

from __future__ import annotations

import asyncio
import logging

import boto3
from botocore.config import Config

from ..config import Settings
from ..ports.vision import VisionProvider, VisionResult

logger = logging.getLogger(__name__)

# Claude Haiku 4.5 on-demand pricing, USD per 1M tokens (images bill as input
# tokens — roughly (w*h)/750 per image).
_PRICE_IN_PER_MTOK = 1.00
_PRICE_OUT_PER_MTOK = 5.00

_BOTO_CONFIG = Config(
    connect_timeout=5,
    read_timeout=60,
    retries={"max_attempts": 2, "mode": "standard"},
)

_INSTRUCTION = (
    "Look at the person in this photo and invent a whimsical storybook "
    "character based on them, named {name}. Reply with 1–2 sentences in "
    "English describing the character for a children's bedtime tale and its "
    "illustrations: hair, glasses if any, smile, clothing colors, overall "
    "vibe — warm and positive. Stylized fairytale description only: no age "
    "estimates, no real-world identifying details, no mention of a photo. "
    "Reply with the description only."
)


class BedrockVisionProvider(VisionProvider):
    def __init__(self, settings: Settings) -> None:
        self._model_id = settings.bedrock_text_model_id
        self._client = boto3.client(
            "bedrock-runtime", region_name=settings.aws_region, config=_BOTO_CONFIG
        )

    def _invoke(self, image_bytes: bytes, image_format: str, name: str) -> dict:
        return self._client.converse(
            modelId=self._model_id,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"image": {"format": image_format, "source": {"bytes": image_bytes}}},
                        {"text": _INSTRUCTION.format(name=name)},
                    ],
                }
            ],
            inferenceConfig={"maxTokens": 300},
        )

    async def describe_character(
        self, image_bytes: bytes, image_format: str, name: str
    ) -> VisionResult:
        resp = await asyncio.to_thread(self._invoke, image_bytes, image_format, name)
        text = resp["output"]["message"]["content"][0]["text"].strip()
        usage = resp.get("usage", {})
        tokens_in = int(usage.get("inputTokens", 0))
        tokens_out = int(usage.get("outputTokens", 0))
        cost = (tokens_in * _PRICE_IN_PER_MTOK + tokens_out * _PRICE_OUT_PER_MTOK) / 1_000_000
        return VisionResult(description=text, model_id=self._model_id, cost_usd=round(cost, 6))

    async def check_ready(self) -> bool:
        # Same cheap local probe as the story provider — no paid Bedrock call.
        try:
            return boto3.Session().get_credentials() is not None
        except Exception:
            return False
