"""BYO storage destinations: create (encrypt credentials), probe, set primary."""
import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import KeyProvider, encrypt_secret, new_wrapped_dek
from app.core.errors import NotFound, ProblemError
from app.core.tenancy import TenantContext
from app.db.models.documents import Document
from app.db.models.storage import StorageDestination as Row
from app.db.models.tenancy import Firm
from app.domain.audit import service as audit
from app.storage.resolver import CRED_PURPOSE, StorageResolver


async def ensure_dek(db: AsyncSession, firm_id: uuid.UUID, keys: KeyProvider) -> bytes:
    firm = (await db.execute(select(Firm).where(Firm.id == firm_id).with_for_update())).scalar_one()
    if firm.wrapped_dek is None:
        firm.wrapped_dek = new_wrapped_dek(keys, firm_id)
    return firm.wrapped_dek


async def create_destination(
    db: AsyncSession, ctx: TenantContext, keys: KeyProvider, *, name: str, region_country: str, transfer_basis: str,
    bucket: str, endpoint_url: str | None, region: str, prefix: str, access_key: str, secret_key: str,
) -> Row:
    assert ctx.firm_id
    country = region_country.upper()
    if country != "KE" and len(transfer_basis.strip()) < 10:
        raise ProblemError(
            422, "transfer_basis_required", "Transfer basis required",
            "Storing data outside Kenya is a cross-border transfer: record the legal basis (e.g. safeguards/consent) in transfer_basis.",
        )
    dek = await ensure_dek(db, ctx.firm_id, keys)
    blob = encrypt_secret(keys, dek, ctx.firm_id, CRED_PURPOSE, json.dumps({"access_key": access_key, "secret_key": secret_key}).encode())
    row = Row(
        firm_id=ctx.firm_id, name=name, region_country=country, transfer_basis=transfer_basis.strip(),
        config={"bucket": bucket, "endpoint_url": endpoint_url or "", "region": region, "prefix": prefix.strip("/")}, credentials_enc=blob,
    )
    db.add(row)
    try:
        await db.flush()
    except IntegrityError as exc:
        raise ProblemError(409, "name_taken", "Conflict", "A destination with this name already exists.") from exc
    await audit.record(db, ctx, "storage.create", target=f"storage:{row.id}", meta={"country": country})  # never log credentials
    return row


async def test_destination(db: AsyncSession, ctx: TenantContext, resolver: StorageResolver, dest_id: uuid.UUID) -> Row:
    row = await db.get(Row, dest_id)
    if row is None:
        raise NotFound("storage destination")
    try:
        result = await (await resolver.build(db, row)).health_check()
        health = {"ok": result.ok, "detail": result.detail[:300]}
    except Exception as exc:  # noqa: BLE001 - surface any adapter/credential failure as a failed probe
        health = {"ok": False, "detail": f"{type(exc).__name__}: {exc}"[:300]}
    row.health, row.last_tested_at = health, datetime.now(UTC)
    await audit.record(db, ctx, "storage.test", target=f"storage:{row.id}", meta={"ok": health["ok"]})
    return row


async def set_primary(db: AsyncSession, ctx: TenantContext, dest_id: uuid.UUID | None) -> None:
    """dest_id=None reverts to Lygo-managed storage. Existing documents stay where they are."""
    assert ctx.firm_id
    if dest_id is not None:
        row = await db.get(Row, dest_id)
        if row is None:
            raise NotFound("storage destination")
        if not row.health.get("ok"):
            raise ProblemError(409, "destination_untested", "Destination not healthy", "Run a successful connection test before making it primary.")
    await db.execute(update(Row).where(Row.firm_id == ctx.firm_id, Row.is_primary).values(is_primary=False))
    if dest_id is not None:
        await db.execute(update(Row).where(Row.id == dest_id).values(is_primary=True))
    await audit.record(db, ctx, "storage.primary", target=f"storage:{dest_id}" if dest_id else "storage:lygo-managed")


async def delete_destination(db: AsyncSession, ctx: TenantContext, dest_id: uuid.UUID) -> None:
    row = await db.get(Row, dest_id)
    if row is None:
        raise NotFound("storage destination")
    in_use = (await db.execute(select(Document.id).where(Document.storage_destination_id == str(dest_id)).limit(1))).first()
    if row.is_primary or in_use:
        raise ProblemError(409, "destination_in_use", "Destination in use", "It is the primary destination or still holds documents.")
    await db.delete(row)
    await audit.record(db, ctx, "storage.delete", target=f"storage:{dest_id}")
