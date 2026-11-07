"""Filesystem destination for local development and tests. Not for production use."""
import asyncio
import hashlib
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from app.storage.base import HealthResult, InvalidKey, ObjectNotFound, StoredObject, validate_key


class LocalFsStorage:
    def __init__(self, root: str | Path, destination_id: str = "local") -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.destination_id = destination_id

    def _path(self, key: str) -> Path:
        p = (self.root / validate_key(key)).resolve()
        if not p.is_relative_to(self.root):
            raise InvalidKey(f"key escapes storage root: {key!r}")
        return p

    async def put(
        self, key: str, data: AsyncIterator[bytes], *, content_type: str, metadata: dict[str, Any]
    ) -> StoredObject:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        h, size = hashlib.sha256(), 0
        try:
            with tmp.open("wb") as f:
                async for chunk in data:
                    h.update(chunk)
                    size += len(chunk)
                    await asyncio.to_thread(f.write, chunk)
            tmp.replace(path)  # atomic publish
        finally:
            tmp.unlink(missing_ok=True)
        return StoredObject(key=key, size=size, sha256=h.hexdigest(), destination_id=self.destination_id)

    async def get(self, key: str) -> AsyncIterator[bytes]:  # type: ignore[override]
        path = self._path(key)
        if not path.is_file():
            raise ObjectNotFound(key)
        with path.open("rb") as f:
            while chunk := await asyncio.to_thread(f.read, 1 << 20):
                yield chunk

    async def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    async def presign_get(self, key: str, ttl_s: int = 300) -> str | None:
        return None  # no direct URLs; the API streams the object

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
