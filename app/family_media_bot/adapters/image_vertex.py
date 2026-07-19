"""Vertex AI implementation of ImageProvider.

Gemini image models use generate_content while Imagen models use
generate_images. Supporting both keeps the provider portable across projects
whose Model Garden catalogs expose different publisher models.
"""

from __future__ import annotations

import asyncio
import logging

from google import genai
from google.genai.types import GenerateContentConfig, GenerateImagesConfig

from ..config import Settings
from ..ports.image import ImageProvider, ImageResult
from .image_fake import _placeholder_png

logger = logging.getLogger(__name__)

_MAX_PROMPT_CHARS = 2000
_IMAGE_COST_PER_IMAGE_USD = {
    "gemini-2.5-flash-image": 0.039,
    "imagen-4.0-fast-generate-001": 0.02,
    "imagen-4.0-generate-001": 0.04,
    "imagen-4.0-ultra-generate-001": 0.06,
}


class VertexImageProvider(ImageProvider):
    def __init__(self, settings: Settings) -> None:
        if not settings.gcp_project_id:
            raise ValueError("IMAGE_PROVIDER=vertex requires GCP_PROJECT_ID to be set")
        self._model_id = settings.vertex_image_model_id
        self._client = genai.Client(
            vertexai=True,
            project=settings.gcp_project_id,
            location=settings.vertex_image_location,
        )

    def _invoke_imagen(self, prompt: str) -> tuple[bytes, str]:
        response = self._client.models.generate_images(
            model=self._model_id,
            prompt=prompt[:_MAX_PROMPT_CHARS],
            config=GenerateImagesConfig(
                number_of_images=1,
                aspect_ratio="1:1",
                image_size="1K",
                output_mime_type="image/png",
            ),
        )
        if not response.generated_images:
            raise RuntimeError("Vertex AI returned no generated image")
        image = response.generated_images[0].image
        return image.image_bytes, image.mime_type or "image/png"

    def _invoke_gemini(self, prompt: str) -> tuple[bytes, str]:
        response = self._client.models.generate_content(
            model=self._model_id,
            contents=prompt[:_MAX_PROMPT_CHARS],
            config=GenerateContentConfig(response_modalities=["IMAGE"]),
        )
        for candidate in response.candidates or []:
            for part in candidate.content.parts or []:
                inline_data = getattr(part, "inline_data", None)
                if inline_data and inline_data.data:
                    return inline_data.data, inline_data.mime_type or "image/png"
        raise RuntimeError("Vertex AI returned no generated image")

    def _invoke(self, prompt: str) -> tuple[bytes, str]:
        if self._model_id.startswith("imagen-"):
            return self._invoke_imagen(prompt)
        return self._invoke_gemini(prompt)

    async def generate(self, prompt: str) -> ImageResult:
        try:
            image_bytes, content_type = await asyncio.to_thread(self._invoke, prompt)
            return ImageResult(
                png_bytes=image_bytes,
                model_id=self._model_id,
                cost_usd=_IMAGE_COST_PER_IMAGE_USD.get(self._model_id, 0.0),
                content_type=content_type,
            )
        except Exception:
            logger.exception("Vertex image generation failed — using placeholder image")
            return ImageResult(
                png_bytes=_placeholder_png(), model_id="placeholder-fallback", cost_usd=0.0
            )

    async def check_ready(self) -> bool:
        try:
            import google.auth

            await asyncio.to_thread(google.auth.default)
            return True
        except Exception:
            return False
