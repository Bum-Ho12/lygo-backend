import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require, tenant_db
from app.core.tenancy import TenantContext
from app.db.models.tenancy import Firm
from app.db.models.work import Invitation
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
    email_sent: bool = True  # False => not delivered by email
    invite_link: str | None = None  # only present when email_sent is false: give it to the invitee through a channel you trust


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
    r = await service.create_invitation(db, ctx, email, to=body.email, role=body.role, firm_name=firm.name if firm else "")
    return InviteOut(id=r.invitation.id, email=r.invitation.email, role=r.invitation.role, email_sent=r.emailed, invite_link=None if r.emailed else r.link)


@router.get("/invitations")
async def list_invitations(db: Db, ctx: Annotated[TenantContext, Depends(require("members.manage"))]) -> dict[str, list[dict[str, object]]]:
    rows = (await db.execute(
        select(Invitation).where(Invitation.accepted_at.is_(None), Invitation.revoked_at.is_(None)).order_by(Invitation.created_at.desc()).limit(200)
    )).scalars()
    return {"items": [{"id": str(i.id), "email": i.email, "role": i.role, "client_id": str(i.client_id) if i.client_id else None,
                       "expires_at": i.expires_at.isoformat(), "expired": i.expires_at <= datetime.now(UTC)} for i in rows]}


@router.post("/invitations/{invitation_id}/resend", response_model=InviteOut)
async def resend_invitation(
    invitation_id: uuid.UUID, db: Db, email: Annotated[EmailSender, Depends(get_email_sender)], ctx: Annotated[TenantContext, Depends(require("members.manage"))]
) -> InviteOut:
    firm = await db.get(Firm, ctx.firm_id)
    r = await service.resend_invitation(db, ctx, email, invitation_id, firm_name=firm.name if firm else "")
    inv = r.invitation
    return InviteOut(id=inv.id, email=inv.email, role=inv.role, client_id=inv.client_id, email_sent=r.emailed, invite_link=None if r.emailed else r.link)


@router.delete("/invitations/{invitation_id}", status_code=204)
async def revoke_invitation(invitation_id: uuid.UUID, db: Db, ctx: Annotated[TenantContext, Depends(require("members.manage"))]) -> None:
    """Revoke a pending invitation (firm or client portal). The link stops working at once."""
    await service.revoke_invitation(db, ctx, invitation_id)


@router.patch("/members/{user_id}")
async def patch_member(
    user_id: uuid.UUID, body: MemberPatch, db: Db, ctx: Annotated[TenantContext, Depends(require("members.manage"))]
) -> dict[str, str]:
    m = await service.update_member(db, ctx, user_id, role=body.role, status=body.status)
    return {"user_id": str(m.user_id), "role": m.role, "status": m.status}


@router.delete("/members/{user_id}", status_code=204)
async def delete_member(user_id: uuid.UUID, db: Db, ctx: Annotated[TenantContext, Depends(require("members.manage"))]) -> None:
    await service.remove_member(db, ctx, user_id)
