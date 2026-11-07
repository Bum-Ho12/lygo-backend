"""Identity operations. Identity tables are not tenant-RLS tables (ADR 0006): every query here is
scoped by the authenticated/credentialed user explicitly."""
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ProblemError
from app.core.security import (
    create_access_token,
    hash_password,
    hash_refresh_token,
    needs_rehash,
    new_refresh_token,
    verify_password,
)
from app.db.models.auth import RefreshToken
from app.db.models.tenancy import ClientMembership, FirmMembership, User


@dataclass
class TokenPair:
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = settings.jwt_access_ttl_min * 60


def _unauthorized(detail: str = "Invalid credentials.") -> ProblemError:
    return ProblemError(401, "unauthorized", "Unauthorized", detail)


async def register_user(session: AsyncSession, *, email: str, password: str, name: str = "") -> User:
    user = User(email=email.strip().lower(), password_hash=hash_password(password), name=name.strip())
    session.add(user)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise ProblemError(409, "email_taken", "Conflict", "An account with this email already exists.") from exc
    return user


async def _issue(session: AsyncSession, user_id: uuid.UUID, family_id: uuid.UUID | None, ip: str | None, ua: str | None) -> TokenPair:
    plain, digest = new_refresh_token()
    session.add(
        RefreshToken(
            user_id=user_id, family_id=family_id or uuid.uuid4(), token_hash=digest, ip=ip, user_agent=(ua or "")[:300],
            expires_at=datetime.now(UTC) + timedelta(days=settings.jwt_refresh_ttl_days),
        )
    )
    await session.flush()
    return TokenPair(access_token=create_access_token(user_id), refresh_token=plain)


async def login(session: AsyncSession, *, email: str, password: str, ip: str | None = None, ua: str | None = None) -> TokenPair:
    user = (await session.execute(select(User).where(User.email == email.strip().lower()))).scalar_one_or_none()
    if not verify_password(password, user.password_hash if user else None) or user is None:
        raise _unauthorized()
    if user.password_hash and needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    return await _issue(session, user.id, None, ip, ua)


async def refresh(session: AsyncSession, *, refresh_token: str, ip: str | None = None, ua: str | None = None) -> TokenPair:
    row = (
        await session.execute(select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(refresh_token)).with_for_update())
    ).scalar_one_or_none()
    if row is None:
        raise _unauthorized("Invalid refresh token.")
    now = datetime.now(UTC)
    if row.revoked_at is not None:
        # Reuse of a rotated token: assume theft and kill the whole family.
        await session.execute(
            update(RefreshToken).where(RefreshToken.family_id == row.family_id, RefreshToken.revoked_at.is_(None)).values(revoked_at=now)
        )
        await session.commit()  # persist the family revocation even though we are about to fail
        raise _unauthorized("Refresh token has been revoked.")
    if row.expires_at <= now:
        raise _unauthorized("Refresh token expired.")
    row.revoked_at = now
    return await _issue(session, row.user_id, row.family_id, ip, ua)


async def logout(session: AsyncSession, *, refresh_token: str) -> None:
    row = (
        await session.execute(select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(refresh_token)))
    ).scalar_one_or_none()
    if row is not None:
        await session.execute(
            update(RefreshToken).where(RefreshToken.family_id == row.family_id, RefreshToken.revoked_at.is_(None)).values(revoked_at=datetime.now(UTC))
        )


async def memberships(session: AsyncSession, user_id: uuid.UUID) -> dict[str, list[dict[str, str]]]:
    # Firm names are tenant data (RLS); fetch them via GET /firms/{id} with a tenant context.
    firms = (
        await session.execute(
            select(FirmMembership.firm_id, FirmMembership.role).where(
                FirmMembership.user_id == user_id, FirmMembership.status == "active"
            )
        )
    ).all()
    clients = (
        await session.execute(
            select(ClientMembership.client_id, ClientMembership.firm_id, ClientMembership.role).where(ClientMembership.user_id == user_id)
        )
    ).all()
    return {
        "firms": [{"firm_id": str(f), "role": r} for f, r in firms],
        "clients": [{"client_id": str(c), "firm_id": str(f), "role": r} for c, f, r in clients],
    }
