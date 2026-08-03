"""Vertex AI implementation of ImageProvider.

Gemini image models use generate_content while Imagen models use
generate_images. Supporting both keeps the provider portable across projects
whose Model Garden catalogs expose different publisher models.
"""

from __future__ import annotations

import asyncio
import logging

from google import genai
from google.genai.types import GenerateContentConfig, GenerateImagesConfig, HttpOptions

from ..config import Settings
from ..ports.image import ImageProvider, ImageResult

logger = logging.getLogger(__name__)

# Was 2000, which was a harmless backstop until the cast block existed. Eight
# characters at 300 characters each plus the scene and style suffix runs to ~3,000,
# so the old cap would have silently amputated the tail of the cast *and* the
# entire style suffix — quietly breaking the feature it was supposed to guard.
# Gemini accepts far more than this; the cap exists only to bound accidents.
_MAX_PROMPT_CHARS = 4000

# Milliseconds. See story_vertex for why this, and not the worker's wait_for, is
# the bound that actually stops a hung call: to_thread is not cancellable.
_REQUEST_TIMEOUT_MS = 40_000
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
        # See VertexStoryProvider: an unpriced model is a configuration error,
        # not a $0 image.
        if self._model_id not in _IMAGE_COST_PER_IMAGE_USD:
            raise ValueError(
                f"no price entry for image model {self._model_id!r}. Add it to "
                "_IMAGE_COST_PER_IMAGE_USD so per-job cost stays honest. "
                f"Priced: {sorted(_IMAGE_COST_PER_IMAGE_USD)}"
            )
        self._client = genai.Client(
            vertexai=True,
            project=settings.gcp_project_id,
            location=settings.vertex_image_location,
            http_options=HttpOptions(timeout=_REQUEST_TIMEOUT_MS),
        )

    def _cap(self, prompt: str) -> str:
        """Bound the prompt, but say so. A silent slice here removes the style
        suffix and the tail of the cast, which is exactly the kind of quiet
        degradation this project keeps finding."""
        if len(prompt) > _MAX_PROMPT_CHARS:
            logger.warning(
                "image prompt truncated",
                extra={"chars": len(prompt), "limit": _MAX_PROMPT_CHARS},
            )
        return prompt[:_MAX_PROMPT_CHARS]

    def _invoke_imagen(self, prompt: str) -> tuple[bytes, str]:
        response = self._client.models.generate_images(
            model=self._model_id,
            prompt=self._cap(prompt),
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
            contents=self._cap(prompt),
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
        # Deliberately no fallback. Substituting a placeholder gradient here
        # turned every image failure into a job that acked, logged "job
        # completed", reported $0, and sent a child a blank blue rectangle —
        # a provider failure disguised as a success. Raise instead, so the
        # queue retries and the failure is visible.
        image_bytes, content_type = await asyncio.to_thread(self._invoke, prompt)
        return ImageResult(
            png_bytes=image_bytes,
            model_id=self._model_id,
            cost_usd=_IMAGE_COST_PER_IMAGE_USD[self._model_id],
            content_type=content_type,
        )

    async def check_ready(self) -> bool:
        try:
            import google.auth

            await asyncio.to_thread(google.auth.default)
            return True
        except Exception:
            return False
