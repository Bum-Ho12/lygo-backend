import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require, tenant_db
from app.core.crypto import KeyProvider, get_key_provider
from app.core.tenancy import TenantContext
from app.db.models.storage import StorageDestination as Row
from app.domain.firms import storage_service as svc
from app.storage.resolver import StorageResolver, get_resolver

router = APIRouter(prefix="/firms/{firm_id}", tags=["storage"])
Db = Annotated[AsyncSession, Depends(tenant_db)]
Manage = Annotated[TenantContext, Depends(require("storage.manage"))]


class DestinationIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    region_country: str = Field(min_length=2, max_length=2, description="ISO country where the bucket physically is")
    transfer_basis: str = Field(default="", max_length=500, description="Required when outside Kenya")
    bucket: str = Field(min_length=3, max_length=63)
    endpoint_url: str | None = Field(default=None, max_length=300)
    region: str = Field(default="us-east-1", max_length=40)
    prefix: str = Field(default="", max_length=200)
    access_key: str = Field(min_length=1, max_length=200)
    secret_key: str = Field(min_length=1, max_length=400)


class DestinationOut(BaseModel):
    id: uuid.UUID
    name: str
    kind: str
    region_country: str
    transfer_basis: str
    config: dict[str, Any]
    is_primary: bool
    health: dict[str, Any]
    last_tested_at: datetime | None


class PrimaryIn(BaseModel):
    destination_id: uuid.UUID | None = None


def _out(r: Row) -> DestinationOut:  # credentials are deliberately not part of the output model
    return DestinationOut(id=r.id, name=r.name, kind=r.kind, region_country=r.region_country, transfer_basis=r.transfer_basis,
                          config=r.config, is_primary=r.is_primary, health=r.health, last_tested_at=r.last_tested_at)


@router.get("/storage-destinations", response_model=list[DestinationOut])
async def list_destinations(db: Db, ctx: Manage) -> list[DestinationOut]:
    return [_out(r) for r in (await db.execute(select(Row).order_by(Row.name))).scalars()]


@router.post("/storage-destinations", status_code=201, response_model=DestinationOut)
async def create_destination(body: DestinationIn, db: Db, ctx: Manage, keys: Annotated[KeyProvider, Depends(get_key_provider)]) -> DestinationOut:
    return _out(await svc.create_destination(db, ctx, keys, **body.model_dump()))


@router.post("/storage-destinations/{dest_id}/test", response_model=DestinationOut)
async def test_destination(dest_id: uuid.UUID, db: Db, ctx: Manage, resolver: Annotated[StorageResolver, Depends(get_resolver)]) -> DestinationOut:
    return _out(await svc.test_destination(db, ctx, resolver, dest_id))


@router.put("/storage-policy/primary")
async def set_primary(body: PrimaryIn, db: Db, ctx: Manage) -> dict[str, str | None]:
    await svc.set_primary(db, ctx, body.destination_id)
    return {"primary": str(body.destination_id) if body.destination_id else None}


@router.delete("/storage-destinations/{dest_id}", status_code=204)
async def delete_destination(dest_id: uuid.UUID, db: Db, ctx: Manage) -> None:
    await svc.delete_destination(db, ctx, dest_id)
