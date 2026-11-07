"""Storage destination interface. See docs/06-storage-and-byo.md."""
from dataclasses import dataclass
from typing import AsyncIterator, Protocol


@dataclass
class StoredObject:
    key: str
    size: int
    sha256: str
    destination_id: str


@dataclass
class HealthResult:
    ok: bool
    detail: str = ""


class StorageDestination(Protocol):
    async def put(self, key: str, data: AsyncIterator[bytes], *, content_type: str, metadata: dict) -> StoredObject: ...
    async def get(self, key: str) -> AsyncIterator[bytes]: ...
    async def delete(self, key: str) -> None: ...
    async def presign_get(self, key: str, ttl_s: int = 300) -> str: ...
    async def health_check(self) -> HealthResult: ...
