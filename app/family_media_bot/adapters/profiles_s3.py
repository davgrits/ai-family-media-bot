"""S3ProfileStore — ProfileStore backed by the media bucket, under
`profiles/<chat_id>.json`. The bucket's 30-day expiry rule is scoped to the
`generated/` prefix (see infra/s3.tf) so profiles are durable."""

from __future__ import annotations

import asyncio
import logging

import boto3

from ..models import ChatProfile
from ..ports.profiles import ProfileStore

logger = logging.getLogger(__name__)


class S3ProfileStore(ProfileStore):
    def __init__(self, bucket: str, region: str) -> None:
        if not bucket:
            raise ValueError("profile store on s3 requires S3_BUCKET to be set")
        self._bucket = bucket
        self._client = boto3.client("s3", region_name=region)

    def _key(self, chat_id: int) -> str:
        return f"profiles/{chat_id}.json"

    async def get(self, chat_id: int) -> ChatProfile:
        try:
            resp = await asyncio.to_thread(
                self._client.get_object, Bucket=self._bucket, Key=self._key(chat_id)
            )
            raw = await asyncio.to_thread(resp["Body"].read)
        except self._client.exceptions.NoSuchKey:
            return ChatProfile()
        except Exception:
            # A transient S3 read error must not crash the chat flow; behave
            # like a new chat and let the next write repair the state.
            logger.exception("profile read failed — using empty", extra={"chat_id": chat_id})
            return ChatProfile()
        try:
            return ChatProfile.model_validate_json(raw)
        except Exception:
            logger.exception("corrupt profile — starting fresh", extra={"chat_id": chat_id})
            return ChatProfile()

    async def put(self, chat_id: int, profile: ChatProfile) -> None:
        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self._bucket,
            Key=self._key(chat_id),
            Body=profile.model_dump_json().encode("utf-8"),
            ContentType="application/json",
        )

    async def check_ready(self) -> bool:
        try:
            await asyncio.to_thread(self._client.head_bucket, Bucket=self._bucket)
            return True
        except Exception:
            return False
