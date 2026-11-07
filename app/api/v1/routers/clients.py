import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require, tenant_db
from app.core.pagination import Page, keyset_page
from app.core.tenancy import TenantContext
from app.db.models.tenancy import Client, Firm
from app.domain.audit import service as audit
from app.domain.firms import service as firm_service
from app.domain.matters.service import get_or_404
from app.integrations.email.base import EmailSender, get_email_sender

router = APIRouter(prefix="/firms/{firm_id}/clients", tags=["clients"])
Db = Annotated[AsyncSession, Depends(tenant_db)]
Read = Annotated[TenantContext, Depends(require("clients.read"))]
Manage = Annotated[TenantContext, Depends(require("clients.manage"))]


class ClientIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    type: Literal["individual", "business"] = "individual"
    sensitivity: Literal["normal", "sensitive"] = "normal"


class ClientPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    sensitivity: Literal["normal", "sensitive"] | None = None


class ClientOut(BaseModel):
    id: uuid.UUID
    name: str
    type: str
    sensitivity: str


class PortalInviteIn(BaseModel):
    email: EmailStr
    role: Literal["client_admin", "client_user"] = "client_user"


def _out(c: Client) -> ClientOut:
    return ClientOut(id=c.id, name=c.name, type=c.type, sensitivity=c.sensitivity)


@router.get("", response_model=Page[ClientOut])
async def list_clients(
    db: Db, ctx: Read, cursor: str | None = None, limit: Annotated[int | None, Query(ge=1, le=200)] = None
) -> Page[ClientOut]:
    rows, nxt = await keyset_page(db, select(Client).where(Client.deleted_at.is_(None)), Client.id, cursor=cursor, limit=limit)
    return Page[ClientOut](items=[_out(c) for c in rows], next_cursor=nxt)


@router.post("", status_code=201, response_model=ClientOut)
async def create_client(body: ClientIn, db: Db, ctx: Manage) -> ClientOut:
    assert ctx.firm_id
    c = Client(firm_id=ctx.firm_id, **body.model_dump())
    db.add(c)
    await db.flush()
    await audit.record(db, ctx, "client.create", target=f"client:{c.id}")
    return _out(c)


@router.get("/{client_id}", response_model=ClientOut)
async def get_client(client_id: uuid.UUID, db: Db, ctx: Read) -> ClientOut:
    return _out(await get_or_404(db, Client, client_id, "client"))


@router.patch("/{client_id}", response_model=ClientOut)
async def patch_client(client_id: uuid.UUID, body: ClientPatch, db: Db, ctx: Manage) -> ClientOut:
    c = await get_or_404(db, Client, client_id, "client")
    changed = body.model_dump(exclude_unset=True)
    for k, v in changed.items():
        setattr(c, k, v)
    await audit.record(db, ctx, "client.update", target=f"client:{c.id}", meta={"fields": sorted(changed)})
    return _out(c)


@router.delete("/{client_id}", status_code=204)
async def archive_client(client_id: uuid.UUID, db: Db, ctx: Manage) -> None:
    c = await get_or_404(db, Client, client_id, "client")
    c.deleted_at = datetime.now(UTC)
    await audit.record(db, ctx, "client.archive", target=f"client:{c.id}")


@router.post("/{client_id}/invitations", status_code=201)
async def invite_portal_user(
    client_id: uuid.UUID, body: PortalInviteIn, db: Db, ctx: Manage,
    email: Annotated[EmailSender, Depends(get_email_sender)],
) -> dict[str, str | bool]:
    await get_or_404(db, Client, client_id, "client")
    firm = await db.get(Firm, ctx.firm_id)
    inv, sent = await firm_service.create_invitation(
        db, ctx, email, to=body.email, role=body.role, client_id=client_id, firm_name=firm.name if firm else ""
    )
    return {"id": str(inv.id), "email": inv.email, "role": inv.role, "email_sent": sent}
