"""Account security flows: password change/reset, phone verification, security summary, session revocation."""
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ProblemError
from app.core.security import hash_password, hash_refresh_token
from app.db.models.auth import RefreshToken
from app.db.models.security import PasswordResetToken
from app.db.models.tenancy import User
from app.integrations.email.base import EmailError, EmailSender
from app.integrations.email.templates import EmailTemplateError, link, render

log = structlog.get_logger()


async def revoke_all_sessions(db: AsyncSession, user_id: uuid.UUID) -> None:
    await db.execute(update(RefreshToken).where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)).values(revoked_at=datetime.now(UTC)))


async def send_alert(email: EmailSender, to: str, event: str) -> None:
    """Best-effort security notification; a delivery failure must never block the security action itself."""
    if not getattr(email, "delivers", True):
        return  # email-less mode: nothing to notify through
    try:
        await email.send(render("security_alert", to, {"event": event, "when": datetime.now(UTC).strftime("%Y-%m-%d %H:%M"), "link": link("forgot-password")}))
    except (EmailError, EmailTemplateError) as exc:
        log.warning("security_alert.email_failed", reason=str(exc)[:120])


async def create_reset(db: AsyncSession, email_addr: str, ip: str | None) -> tuple[str, str] | None:
    """Return (email, plaintext token) if the account exists. Callers must not reveal the difference to the requester."""
    user = (await db.execute(select(User).where(User.email == email_addr.strip().lower()))).scalar_one_or_none()
    if user is None:
        return None
    await db.execute(update(PasswordResetToken).where(PasswordResetToken.user_id == user.id, PasswordResetToken.used_at.is_(None)).values(used_at=datetime.now(UTC)))
    token = secrets.token_urlsafe(32)
    db.add(PasswordResetToken(user_id=user.id, token_hash=hash_refresh_token(token), ip=ip, expires_at=datetime.now(UTC) + timedelta(minutes=settings.password_reset_ttl_minutes)))
    await db.flush()
    return user.email, token


async def send_reset_email(email: EmailSender, to: str, token: str) -> None:
    if not getattr(email, "delivers", True):
        return
    try:
        await email.send(render("password_reset", to, {"minutes": str(settings.password_reset_ttl_minutes), "link": link("reset-password", token=token)}))
    except (EmailError, EmailTemplateError) as exc:
        log.warning("password_reset.email_failed", reason=str(exc)[:120])


async def redeem_reset(db: AsyncSession, token: str, new_password: str) -> User:
    """Single use. Changes the password and ends every session; it does NOT satisfy MFA: the next login still asks for the second factor."""
    row = (await db.execute(select(PasswordResetToken).where(PasswordResetToken.token_hash == hash_refresh_token(token)).with_for_update())).scalar_one_or_none()
    if row is None or row.used_at is not None or row.expires_at <= datetime.now(UTC):
        raise ProblemError(400, "invalid_reset", "Invalid or expired link", "This reset link is invalid, expired or already used. Request a new one.")
    user = await db.get(User, row.user_id)
    assert user is not None
    row.used_at = datetime.now(UTC)
    user.password_hash, user.password_changed_at = hash_password(new_password), datetime.now(UTC)
    await revoke_all_sessions(db, user.id)
    return user
