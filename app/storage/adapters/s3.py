"""S3-compatible destination (RumptyCloud Object Storage, MinIO, AWS S3, Wasabi...).

boto3 is synchronous, so calls run in worker threads. Objects are uploaded via a spooled temp file so
memory stays bounded while the SHA-256 is computed on the way in.
"""
import asyncio
import hashlib
import tempfile
import uuid
from collections.abc import AsyncIterator
from typing import Any

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from app.storage.base import HealthResult, ObjectNotFound, StorageError, StoredObject, validate_key

_SPOOL = 8 << 20


class S3Storage:
    def __init__(
        self, *, bucket: str, endpoint_url: str | None = None, region: str = "us-east-1",
        access_key: str | None = None, secret_key: str | None = None, prefix: str = "", destination_id: str = "s3",
    ) -> None:
        self.bucket, self.prefix, self.destination_id = bucket, prefix.strip("/"), destination_id
        self._client = boto3.client(
            "s3", endpoint_url=endpoint_url or None, region_name=region, aws_access_key_id=access_key or None,
            aws_secret_access_key=secret_key or None, config=Config(signature_version="s3v4", retries={"max_attempts": 3}),
        )

    def _k(self, key: str) -> str:
        key = validate_key(key)
        return f"{self.prefix}/{key}" if self.prefix else key

    async def put(
        self, key: str, data: AsyncIterator[bytes], *, content_type: str, metadata: dict[str, Any]
    ) -> StoredObject:
        h, size = hashlib.sha256(), 0
        with tempfile.SpooledTemporaryFile(max_size=_SPOOL) as buf:
            async for chunk in data:
                h.update(chunk)
                size += len(chunk)
                buf.write(chunk)
            buf.seek(0)
            try:
                await asyncio.to_thread(
                    self._client.upload_fileobj, buf, self.bucket, self._k(key),
                    ExtraArgs={"ContentType": content_type, "Metadata": {k: str(v) for k, v in metadata.items()}},
                )
            except ClientError as exc:
                raise StorageError(str(exc)) from exc
        return StoredObject(key=key, size=size, sha256=h.hexdigest(), destination_id=self.destination_id)

    async def get(self, key: str) -> AsyncIterator[bytes]:  # type: ignore[override]
        try:
            obj = await asyncio.to_thread(self._client.get_object, Bucket=self.bucket, Key=self._k(key))
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"NoSuchKey", "404"}:
                raise ObjectNotFound(key) from exc
            raise StorageError(str(exc)) from exc
        body = obj["Body"]
        while chunk := await asyncio.to_thread(body.read, 1 << 20):
            yield chunk

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._client.delete_object, Bucket=self.bucket, Key=self._k(key))

    async def presign_get(self, key: str, ttl_s: int = 300) -> str | None:
        return str(
            self._client.generate_presigned_url(
                "get_object", Params={"Bucket": self.bucket, "Key": self._k(key)}, ExpiresIn=ttl_s
            )
        )

    async def health_check(self) -> HealthResult:
        probe = f"_health/{uuid.uuid4().hex}"
        try:
            async def one() -> AsyncIterator[bytes]:
                yield b"ok"

            await self.put(probe, one(), content_type="text/plain", metadata={})
            ok = b"".join([c async for c in self.get(probe)]) == b"ok"
            await self.delete(probe)
            return HealthResult(ok=ok)
        except Exception as exc:  # noqa: BLE001
            return HealthResult(ok=False, detail=str(exc))
