import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require, tenant_db
from app.core.errors import NotFound
from app.core.tenancy import TenantContext
from app.db.models.tenancy import Firm
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
