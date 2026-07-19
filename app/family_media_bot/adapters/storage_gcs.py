"""GCS implementation of StoragePort using Workload Identity credentials."""

from __future__ import annotations

import asyncio
import logging

from google.cloud import storage

from ..ports.storage import StoragePort

logger = logging.getLogger(__name__)

_EXTENSIONS = {"image/png": "png", "text/plain": "txt"}


class GcsStorage(StoragePort):
    def __init__(self, bucket: str, project_id: str) -> None:
        if not bucket:
            raise ValueError("STORAGE=gcs requires GCS_BUCKET to be set")
        self._bucket_name = bucket
        self._client = storage.Client(project=project_id or None)

    async def save(self, job_id: str, data: bytes, content_type: str = "image/png") -> str:
        extension = _EXTENSIONS.get(content_type, "bin")
        key = f"generated/{job_id}.{extension}"

        def upload() -> None:
            blob = self._client.bucket(self._bucket_name).blob(key)
            blob.upload_from_string(data, content_type=content_type)

        await asyncio.to_thread(upload)
        uri = f"gs://{self._bucket_name}/{key}"
        logger.info(
            "stored media in gcs",
            extra={"job_id": job_id, "location": uri, "bytes": len(data)},
        )
        return uri

    async def check_ready(self) -> bool:
        try:
            # Object Admin can list objects but cannot read bucket metadata, so
            # Bucket.exists() would incorrectly return 403 for this identity.
            await asyncio.to_thread(
                lambda: next(
                    iter(self._client.list_blobs(self._bucket_name, max_results=1)),
                    None,
                )
            )
            return True
        except Exception:
            return False
