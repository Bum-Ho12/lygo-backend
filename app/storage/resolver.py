"""Resolve which physical storage a firm/document uses (Lygo-managed default or the firm's own bucket)."""
import json
import uuid
from typing import Annotated

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import KeyProvider, decrypt_secret, get_key_provider
from app.db.models.storage import StorageDestination as DestinationRow
from app.db.models.tenancy import Firm
from app.storage.adapters.s3 import S3Storage
from app.storage.base import StorageDestination, StorageError
from app.storage.endpoint_guard import EndpointNotAllowed, check_endpoint
from app.storage.factory import get_storage

MANAGED = "lygo-managed"
CRED_PURPOSE = "storage-credentials"


class StorageResolver:
    def __init__(self, default: StorageDestination, keys: KeyProvider | None = None) -> None:
        self.default = default
        self.keys = keys or get_key_provider()

    async def primary(self, db: AsyncSession, firm_id: uuid.UUID) -> tuple[str, StorageDestination]:
        """Where new uploads go: the firm's primary BYO destination if it has one, else Lygo-managed.

        There is deliberately no silent fallback to Lygo-managed storage when the firm's bucket is down: a firm that chose
        its own bucket (often for data residency) must not have files stored elsewhere without deciding to. Uploads fail
        with a retryable 503 until the bucket is back or the firm switches to Lygo storage."""
        row = (await db.execute(select(DestinationRow).where(DestinationRow.firm_id == firm_id, DestinationRow.is_primary))).scalar_one_or_none()
        if row is None:
            return self.default.destination_id, self.default
        return str(row.id), await self.build(db, row)

    async def for_destination(self, db: AsyncSession, firm_id: uuid.UUID, destination_id: str) -> StorageDestination:
        if destination_id in (MANAGED, self.default.destination_id):
            return self.default
        try:
            did = uuid.UUID(destination_id)
        except ValueError as exc:
            raise StorageError(f"unknown storage destination {destination_id!r}") from exc
        row = await db.get(DestinationRow, did)  # RLS: only this firm's destinations are visible
        if row is None or row.firm_id != firm_id:
            raise StorageError("storage destination not found (deleted?)")
        return await self.build(db, row)

    async def build(self, db: AsyncSession, row: DestinationRow) -> StorageDestination:
        firm = await db.get(Firm, row.firm_id)
        if firm is None or firm.wrapped_dek is None:
            raise StorageError("encryption key for this firm is unavailable (crypto-shredded?)")
        creds = json.loads(decrypt_secret(self.keys, firm.wrapped_dek, row.firm_id, CRED_PURPOSE, row.credentials_enc))
        cfg = row.config
        try:
            await check_endpoint(cfg.get("endpoint_url") or None)  # again at use time: DNS may have changed since saving
        except EndpointNotAllowed as exc:
            raise StorageError(f"storage endpoint not allowed: {exc}") from exc
        return S3Storage(
            bucket=cfg["bucket"], endpoint_url=cfg.get("endpoint_url") or None, region=cfg.get("region", "us-east-1"),
            access_key=creds["access_key"], secret_key=creds["secret_key"], prefix=cfg.get("prefix", ""), destination_id=str(row.id),
        )


def get_resolver(
    default: Annotated[StorageDestination, Depends(get_storage)], keys: Annotated[KeyProvider, Depends(get_key_provider)]
) -> StorageResolver:
    """FastAPI dependency; override in tests."""
    return StorageResolver(default, keys)
