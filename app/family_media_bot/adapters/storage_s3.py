"""S3Storage — StoragePort backed by Amazon S3. Writes to
`s3://<bucket>/generated/<job_id>.<ext>` where the extension follows the
content type (the pipeline saves both the story text and the PNG)."""

from __future__ import annotations

import asyncio
import logging

import boto3

from ..ports.storage import StoragePort

logger = logging.getLogger(__name__)

_EXTENSIONS = {"image/png": "png", "text/plain": "txt"}


class S3Storage(StoragePort):
    def __init__(self, bucket: str, region: str) -> None:
        if not bucket:
            raise ValueError("STORAGE=s3 requires S3_BUCKET to be set")
        self._bucket = bucket
        self._client = boto3.client("s3", region_name=region)

    async def save(self, job_id: str, data: bytes, content_type: str = "image/png") -> str:
        ext = _EXTENSIONS.get(content_type, "bin")
        key = f"generated/{job_id}.{ext}"
        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self._bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )
        uri = f"s3://{self._bucket}/{key}"
        logger.info(
            "stored media in s3",
            extra={"job_id": job_id, "location": uri, "bytes": len(data)},
        )
        return uri

    async def check_ready(self) -> bool:
        try:
            await asyncio.to_thread(self._client.head_bucket, Bucket=self._bucket)
            return True
        except Exception:
            return False
