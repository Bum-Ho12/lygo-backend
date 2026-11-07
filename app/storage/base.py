"""Storage destination interface. See docs/06-storage-and-byo.md."""
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol


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


class StorageError(Exception): ...


class ObjectNotFound(StorageError): ...


class InvalidKey(StorageError): ...


def validate_key(key: str) -> str:
    """Keys are relative, slash-separated, and may not escape their root."""
    if not key or key.startswith("/") or "\\" in key or "\x00" in key:
        raise InvalidKey(f"invalid storage key: {key!r}")
    if any(part in ("", ".", "..") for part in key.split("/")):
        raise InvalidKey(f"invalid storage key: {key!r}")
    return key


class StorageDestination(Protocol):
    destination_id: str

    async def put(
        self, key: str, data: AsyncIterator[bytes], *, content_type: str, metadata: dict[str, Any]
    ) -> StoredObject: ...
    def get(self, key: str) -> AsyncIterator[bytes]: ...
    async def delete(self, key: str) -> None: ...
    async def presign_get(self, key: str, ttl_s: int = 300) -> str | None: ...
    async def health_check(self) -> HealthResult: ...


async def iter_bytes(data: bytes, chunk: int = 1 << 20) -> AsyncIterator[bytes]:
    for i in range(0, len(data), chunk):
        yield data[i : i + chunk]
