"""FakeImageProvider — emits a real, valid placeholder PNG built with the stdlib
only (no Pillow). A soft vertical gradient so it reads as a placeholder picture
rather than a flat block. Cost is reported as $0."""

from __future__ import annotations

import asyncio
import struct
import zlib

from ..ports.image import ImageProvider, ImageResult


def _placeholder_png(width: int = 640, height: int = 400, top_rgb=(120, 150, 200)) -> bytes:
    """Hand-encode an 8-bit RGB PNG with a gentle gradient (stdlib zlib/struct)."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # bit depth 8, color type 2 (RGB)

    r0, g0, b0 = top_rgb
    rows = bytearray()
    for y in range(height):
        t = y / max(1, height - 1)
        r = int(r0 * (1 - t) + 245 * t)
        g = int(g0 * (1 - t) + 235 * t)
        b = int(b0 * (1 - t) + 215 * t)
        rows.append(0)  # per-scanline filter type 0 (None)
        rows.extend(bytes((r, g, b)) * width)

    idat = zlib.compress(bytes(rows), 9)
    return signature + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


class FakeImageProvider(ImageProvider):
    def __init__(self, model_id: str = "fake-image-v1") -> None:
        self._model_id = model_id

    async def generate(self, prompt: str) -> ImageResult:
        await asyncio.sleep(0)
        return ImageResult(png_bytes=_placeholder_png(), model_id=self._model_id, cost_usd=0.0)

    async def check_ready(self) -> bool:
        return True
