"""Members and invitations."""
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Forbidden, NotFound, ProblemError
from app.core.permissions import CLIENT_ROLES, FIRM_ROLES
from app.core.security import hash_refresh_token
from app.core.tenancy import TenantContext
from app.db.models.tenancy import Client, ClientMembership, Firm, FirmMembership, User
from app.db.models.work import Invitation
from app.domain.audit import service as audit
from app.integrations.email.base import EmailMessage, EmailSender

INVITE_TTL = timedelta(days=7)


def _conflict(code: str, detail: str) -> ProblemError:
    return ProblemError(409, code, "Conflict", detail)


async def list_members(db: AsyncSession, firm_id: uuid.UUID) -> list[dict[str, str]]:
    # Identity tables have no RLS (ADR 0006): this query MUST stay filtered by firm_id.
    rows = (
        await db.execute(
            select(FirmMembership.user_id, FirmMembership.role, FirmMembership.status, User.email, User.name)
            .join(User, User.id == FirmMembership.user_id)
            .where(FirmMembership.firm_id == firm_id)
            .order_by(User.email)
        )
    ).all()
    return [{"user_id": str(u), "role": r, "status": s, "email": e, "name": n} for u, r, s, e, n in rows]


async def _active_owner_ids(db: AsyncSession, firm_id: uuid.UUID) -> list[uuid.UUID]:
    q = (
        select(FirmMembership.user_id)
        .where(FirmMembership.firm_id == firm_id, FirmMembership.role == "firm_owner", FirmMembership.status == "active")
        .with_for_update()  # serialise concurrent owner changes
    )
    return list((await db.execute(q)).scalars())


def _check_assignable(actor_role: str, target_role: str) -> None:
    if target_role not in FIRM_ROLES:
        raise ProblemError(422, "invalid_role", "Invalid role", f"Role must be one of {sorted(FIRM_ROLES)}.")
    if target_role == "firm_owner" and actor_role != "firm_owner":
        raise Forbidden("Only a firm owner can grant the owner role.")


async def update_member(
    db: AsyncSession, ctx: TenantContext, user_id: uuid.UUID, *, role: str | None, status: str | None
) -> FirmMembership:
    assert ctx.firm_id
    owners = await _active_owner_ids(db, ctx.firm_id)
    m = (
        await db.execute(
            select(FirmMembership).where(FirmMembership.firm_id == ctx.firm_id, FirmMembership.user_id == user_id)
        )
    ).scalar_one_or_none()
    if m is None:
        raise NotFound("member")
    if m.role == "firm_owner" and ctx.role != "firm_owner":
        raise Forbidden("Only a firm owner can change an owner.")
    if role is not None:
        _check_assignable(ctx.role, role)
    losing_owner = m.status == "active" and m.role == "firm_owner" and (
        (role is not None and role != "firm_owner") or (status is not None and status != "active")
    )
    if losing_owner and owners == [user_id]:
        raise _conflict("last_owner", "A firm must keep at least one active owner.")
    if role is not None:
        m.role = role
    if status is not None:
        if status not in {"active", "suspended"}:
            raise ProblemError(422, "invalid_status", "Invalid status", "Status must be active or suspended.")
        m.status = status
    await audit.record(db, ctx, "member.update", target=f"user:{user_id}", meta={"role": role, "status": status})
    return m


async def remove_member(db: AsyncSession, ctx: TenantContext, user_id: uuid.UUID) -> None:
    assert ctx.firm_id
    owners = await _active_owner_ids(db, ctx.firm_id)
    m = (
        await db.execute(
            select(FirmMembership).where(FirmMembership.firm_id == ctx.firm_id, FirmMembership.user_id == user_id)
        )
    ).scalar_one_or_none()
    if m is None:
        raise NotFound("member")
    if m.role == "firm_owner" and ctx.role != "firm_owner":
        raise Forbidden("Only a firm owner can remove an owner.")
    if m.role == "firm_owner" and m.status == "active" and owners == [user_id]:
        raise _conflict("last_owner", "A firm must keep at least one active owner.")
    await db.delete(m)
    await audit.record(db, ctx, "member.remove", target=f"user:{user_id}")


async def create_invitation(
    db: AsyncSession, ctx: TenantContext, email: EmailSender, *, to: str, role: str, client_id: uuid.UUID | None = None,
    firm_name: str = "",
) -> Invitation:
    assert ctx.firm_id
    to = to.strip().lower()
    if client_id is None:
        _check_assignable(ctx.role, role)
    else:
        if role not in CLIENT_ROLES:
            raise ProblemError(422, "invalid_role", "Invalid role", f"Role must be one of {sorted(CLIENT_ROLES)}.")
        if await db.get(Client, client_id) is None:
            raise NotFound("client")
    token = secrets.token_urlsafe(32)
    inv = Invitation(
        firm_id=ctx.firm_id, client_id=client_id, email=to, role=role, token_hash=hash_refresh_token(token),
        invited_by=ctx.user_id, expires_at=datetime.now(UTC) + INVITE_TTL,
    )
    db.add(inv)
    await db.flush()
    await audit.record(db, ctx, "invitation.create", target=f"invitation:{inv.id}", meta={"role": role, "client_id": str(client_id) if client_id else None})
    await email.send(
        EmailMessage(
            to=to, subject=f"You have been invited to {firm_name or 'Lygo'}",
            body=f"Accept your invitation with this code (valid 7 days): {token}",
        )
    )
    return inv


async def accept_invitation(db: AsyncSession, user: User, token: str) -> dict[str, str | None]:
    """`db` must be a fresh transaction. RLS only lets us see the invitation whose hash we present."""
    digest = hash_refresh_token(token)
    await db.execute(text("SELECT set_config('app.invite_token_hash', :h, true)"), {"h": digest})
    inv = (await db.execute(select(Invitation).where(Invitation.token_hash == digest).with_for_update())).scalar_one_or_none()
    bad = ProblemError(400, "invalid_invitation", "Invalid invitation", "This invitation is invalid, expired or already used.")
    if inv is None or inv.accepted_at is not None or inv.expires_at <= datetime.now(UTC):
        raise bad
    if inv.email != user.email.lower():
        raise bad  # a forwarded token must not grant access to a different account
    if inv.client_id is None:
        exists = (
            await db.execute(select(func.count()).select_from(FirmMembership).where(
                FirmMembership.firm_id == inv.firm_id, FirmMembership.user_id == user.id))
        ).scalar_one()
        if exists:
            raise _conflict("already_member", "You are already a member of this firm.")
        db.add(FirmMembership(firm_id=inv.firm_id, user_id=user.id, role=inv.role))
    else:
        exists = (
            await db.execute(select(func.count()).select_from(ClientMembership).where(
                ClientMembership.client_id == inv.client_id, ClientMembership.user_id == user.id))
        ).scalar_one()
        if exists:
            raise _conflict("already_member", "You already have access to this client.")
        db.add(ClientMembership(client_id=inv.client_id, firm_id=inv.firm_id, user_id=user.id, role=inv.role))
    inv.accepted_at = datetime.now(UTC)
    await db.flush()
    return {"firm_id": str(inv.firm_id), "client_id": str(inv.client_id) if inv.client_id else None, "role": inv.role}





async def create_firm(db: AsyncSession, ctx: TenantContext, *, slug: str, name: str) -> Firm:
    firm = Firm(slug=slug, name=name, kms_key_ref=f"local/{uuid.uuid4()}")
    db.add(firm)
    try:
        await db.flush()
    except IntegrityError as exc:  # unique slug
        raise _conflict("slug_taken", "A firm with this slug already exists.") from exc
    await audit.record(db, ctx, "firm.create", target=f"firm:{firm.id}")
    return firm
