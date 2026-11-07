import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require, tenant_db
from app.core.errors import NotFound
from app.core.pagination import Page, clamp_limit, decode_cursor, encode_cursor
from app.core.tenancy import TenantContext
from app.db.models.tenancy import Client, Firm
from app.domain.audit import service as audit

router = APIRouter(prefix="/firms/{firm_id}", tags=["firms"])
Db = Annotated[AsyncSession, Depends(tenant_db)]


class FirmOut(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    plan: str
    branding: dict[str, object]


class FirmPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    branding: dict[str, object] | None = None


class ClientOut(BaseModel):
    id: uuid.UUID
    name: str
    type: str


@router.get("", response_model=FirmOut)
async def get_firm(db: Db, ctx: Annotated[TenantContext, Depends(require("firm.read"))]) -> FirmOut:
    firm = await db.get(Firm, ctx.firm_id)
    if firm is None:
        raise NotFound("firm")
    return FirmOut(id=firm.id, slug=firm.slug, name=firm.name, plan=firm.plan, branding=firm.branding)


@router.patch("", response_model=FirmOut)
async def patch_firm(
    body: FirmPatch, db: Db, ctx: Annotated[TenantContext, Depends(require("firm.manage"))]
) -> FirmOut:
    firm = await db.get(Firm, ctx.firm_id)
    if firm is None:
        raise NotFound("firm")
    changed = body.model_dump(exclude_unset=True)
    for k, v in changed.items():
        setattr(firm, k, v)
    await audit.record(db, ctx, "firm.update", target=f"firm:{firm.id}", meta={"fields": sorted(changed)})
    return FirmOut(id=firm.id, slug=firm.slug, name=firm.name, plan=firm.plan, branding=firm.branding)


@router.get("/clients", response_model=Page[ClientOut])
async def list_clients(
    db: Db,
    ctx: Annotated[TenantContext, Depends(require("clients.read"))],
    cursor: str | None = None,
    limit: Annotated[int | None, Query(ge=1, le=200)] = None,
) -> Page[ClientOut]:
    n = clamp_limit(limit)
    q = select(Client).where(Client.deleted_at.is_(None)).order_by(Client.id).limit(n + 1)
    after = decode_cursor(cursor)
    if after:
        q = q.where(Client.id > uuid.UUID(str(after.get("after"))))
    rows = list((await db.execute(q)).scalars())
    more = len(rows) > n
    rows = rows[:n]
    return Page[ClientOut](
        items=[ClientOut(id=c.id, name=c.name, type=c.type) for c in rows],
        next_cursor=encode_cursor({"after": str(rows[-1].id)}) if more else None,
    )
