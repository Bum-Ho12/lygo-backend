import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require, tenant_db
from app.core.tenancy import TenantContext
from app.db.models.tenancy import Firm
from app.domain.firms import service
from app.integrations.email.base import EmailSender, get_email_sender

router = APIRouter(prefix="/firms/{firm_id}", tags=["members"])
Db = Annotated[AsyncSession, Depends(tenant_db)]


class InviteIn(BaseModel):
    email: EmailStr
    role: str


class InviteOut(BaseModel):
    id: uuid.UUID
    email: str
    role: str
    client_id: uuid.UUID | None = None


class MemberPatch(BaseModel):
    role: str | None = None
    status: str | None = None


@router.get("/members")
async def list_members(db: Db, ctx: Annotated[TenantContext, Depends(require("members.read"))]) -> dict[str, list[dict[str, str]]]:
    assert ctx.firm_id
    return {"items": await service.list_members(db, ctx.firm_id)}


@router.post("/invitations", status_code=201, response_model=InviteOut)
async def invite_member(
    body: InviteIn, db: Db, email: Annotated[EmailSender, Depends(get_email_sender)],
    ctx: Annotated[TenantContext, Depends(require("members.manage"))],
) -> InviteOut:
    firm = await db.get(Firm, ctx.firm_id)
    inv = await service.create_invitation(db, ctx, email, to=body.email, role=body.role, firm_name=firm.name if firm else "")
    return InviteOut(id=inv.id, email=inv.email, role=inv.role)


@router.patch("/members/{user_id}")
async def patch_member(
    user_id: uuid.UUID, body: MemberPatch, db: Db, ctx: Annotated[TenantContext, Depends(require("members.manage"))]
) -> dict[str, str]:
    m = await service.update_member(db, ctx, user_id, role=body.role, status=body.status)
    return {"user_id": str(m.user_id), "role": m.role, "status": m.status}


@router.delete("/members/{user_id}", status_code=204)
async def delete_member(user_id: uuid.UUID, db: Db, ctx: Annotated[TenantContext, Depends(require("members.manage"))]) -> None:
    await service.remove_member(db, ctx, user_id)
