"""TOTP (RFC 6238) enrolment/verification with replay protection, and single-use recovery codes."""
import secrets
import time
import uuid
from datetime import UTC, datetime
from typing import Any, cast

import pyotp
from sqlalchemy import CursorResult, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import CryptoError, KeyProvider
from app.core.errors import ProblemError
from app.core.security import keyed_hash
from app.db.models.security import MfaRecoveryCode
from app.db.models.tenancy import User

STEP = 30
ISSUER = "Lygo"
RECOVERY_CODES = 10


def _secret_for(keys: KeyProvider, user: User) -> str:
    if not user.mfa_secret_enc:
        raise ProblemError(409, "totp_not_enrolled", "Not enrolled", "Authenticator app is not set up.")
    try:
        return keys.unwrap(user.mfa_secret_enc, user.id).decode()
    except CryptoError as exc:
        raise ProblemError(500, "mfa_unavailable", "MFA unavailable", "Your authenticator data cannot be read; contact support.") from exc


async def begin_enrolment(db: AsyncSession, keys: KeyProvider, user: User) -> tuple[str, str]:
    if user.mfa_totp_enabled_at is not None:
        raise ProblemError(409, "totp_already_enabled", "Already enabled", "Authenticator app is already set up. Disable it first to re-enrol.")
    secret = pyotp.random_base32()
    user.mfa_secret_enc = keys.wrap(secret.encode(), user.id)  # sealed with the master key, bound to this user
    await db.flush()
    return secret, pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name=ISSUER)


async def verify_totp(db: AsyncSession, keys: KeyProvider, user: User, code: str, *, now: float | None = None) -> bool:
    """Accept the code for the previous, current or next 30 s step, but never the same step twice (replay protection)."""
    code = code.strip().replace(" ", "")
    if not (code.isdigit() and len(code) == 6) or not user.mfa_secret_enc:
        return False
    totp = pyotp.TOTP(_secret_for(keys, user))
    current = int((now if now is not None else time.time()) // STEP)
    for step in (current - 1, current, current + 1):
        if secrets.compare_digest(totp.at(step * STEP), code):
            # atomic: only the first use of a step wins, even with concurrent requests
            won = cast(CursorResult[Any], await db.execute(update(User).where(User.id == user.id, User.mfa_totp_last_step < step).values(mfa_totp_last_step=step)))
            return bool(won.rowcount)
    return False


async def confirm_enrolment(db: AsyncSession, keys: KeyProvider, user: User, code: str) -> list[str]:
    if user.mfa_totp_enabled_at is not None:
        raise ProblemError(409, "totp_already_enabled", "Already enabled", "Authenticator app is already set up.")
    if not await verify_totp(db, keys, user, code):
        raise ProblemError(422, "invalid_code", "Invalid code", "That code is not correct. Check the time on your phone and try again.")
    user.mfa_totp_enabled_at = datetime.now(UTC)
    return await regenerate_recovery_codes(db, user.id)


def _new_code() -> str:
    raw = "".join(secrets.choice("abcdefghjkmnpqrstuvwxyz23456789") for _ in range(10))  # no look-alikes (0/o, 1/l/i)
    return f"{raw[:5]}-{raw[5:]}"


def _norm(code: str) -> str:
    return code.strip().lower().replace(" ", "").replace("-", "")


async def regenerate_recovery_codes(db: AsyncSession, user_id: uuid.UUID) -> list[str]:
    await db.execute(delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user_id))
    codes = [_new_code() for _ in range(RECOVERY_CODES)]
    for c in codes:
        db.add(MfaRecoveryCode(user_id=user_id, code_hash=keyed_hash("recovery", _norm(c))))
    await db.flush()
    return codes


async def use_recovery_code(db: AsyncSession, user_id: uuid.UUID, code: str) -> bool:
    h = keyed_hash("recovery", _norm(code))
    res = await db.execute(update(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user_id, MfaRecoveryCode.code_hash == h, MfaRecoveryCode.used_at.is_(None)).values(used_at=datetime.now(UTC)))
    return cast(CursorResult[Any], res).rowcount == 1


async def recovery_codes_remaining(db: AsyncSession, user_id: uuid.UUID) -> int:
    return int((await db.execute(select(func.count()).select_from(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user_id, MfaRecoveryCode.used_at.is_(None)))).scalar_one())


async def disable_totp(db: AsyncSession, user: User) -> None:
    user.mfa_secret_enc, user.mfa_totp_enabled_at, user.mfa_totp_last_step = None, None, 0
    await db.execute(delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user.id))
