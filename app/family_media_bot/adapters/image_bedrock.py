"""BedrockImageProvider — Stability text-to-image on Amazon Bedrock.

Request/response shape is Stability's (stability.stable-image-core / sd3.5 /
ultra). If generation fails after the built-in retry, we fall back to the
placeholder PNG so the demo flow never dies on the image step.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging

import boto3
from botocore.config import Config

from ..config import Settings
from ..ports.image import ImageProvider, ImageResult
from .image_fake import _placeholder_png

logger = logging.getLogger(__name__)

# stability.stable-image-core on-demand pricing, USD per image.
_COST_PER_IMAGE = 0.04

# Stability caps prompts; stay well under the limit.
_MAX_PROMPT_CHARS = 2000

# 60s read timeout + one retry on retryable failures (throttling, timeouts).
_BOTO_CONFIG = Config(
    connect_timeout=5,
    read_timeout=60,
    retries={"max_attempts": 2, "mode": "standard"},
)


class BedrockImageProvider(ImageProvider):
    def __init__(self, settings: Settings) -> None:
        self._model_id = settings.bedrock_image_model_id
        region = settings.bedrock_image_region or settings.aws_region
        self._client = boto3.client(
            "bedrock-runtime", region_name=region, config=_BOTO_CONFIG
        )

    def _invoke(self, prompt: str) -> bytes:
        body = json.dumps(
            {
                "prompt": prompt[:_MAX_PROMPT_CHARS],
                "mode": "text-to-image",
                "aspect_ratio": "1:1",
                "output_format": "png",
            }
        )
        resp = self._client.invoke_model(modelId=self._model_id, body=body)
        payload = json.loads(resp["body"].read())
        return base64.b64decode(payload["images"][0])

    async def generate(self, prompt: str) -> ImageResult:
        try:
            png = await asyncio.to_thread(self._invoke, prompt)
            return ImageResult(
                png_bytes=png, model_id=self._model_id, cost_usd=_COST_PER_IMAGE
            )
        except Exception:
            logger.exception(
                "bedrock image generation failed — using placeholder image"
            )
            return ImageResult(
                png_bytes=_placeholder_png(), model_id="placeholder-fallback", cost_usd=0.0
            )

    async def check_ready(self) -> bool:
        try:
            return boto3.Session().get_credentials() is not None
        except Exception:
            return False
