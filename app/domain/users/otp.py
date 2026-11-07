"""SMS one-time codes for phone verification and as a login second factor.

Security properties: 6 digits from a CSPRNG; only a keyed hash is stored; single use; 5-minute TTL; 5 attempts per code;
a new code invalidates earlier ones; codes are only ever sent to a number the user has already verified (login) or is
verifying right now (verify_phone). Sends are rate limited per user and per IP to blunt SMS-pumping fraud and cost abuse.
"""
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ProblemError
from app.core.security import keyed_hash
from app.db.models.security import OtpChallenge
from app.domain.users import throttle
from app.integrations.sms.base import (
    SmsAuthError,
    SmsError,
    SmsProvider,
    SmsRejected,
    SmsTransientError,
    SmsUncertain,
)
from app.integrations.sms.phone import mask
from app.integrations.sms.templates import render

log = structlog.get_logger()
CODE_DIGITS = 6


def _hash(challenge_id: uuid.UUID, code: str) -> str:
    return keyed_hash("otp", f"{challenge_id}:{code}")


async def issue(db: AsyncSession, user_id: uuid.UUID, purpose: str, destination: str) -> tuple[uuid.UUID, str]:
    now = datetime.now(UTC)
    await db.execute(update(OtpChallenge).where(OtpChallenge.user_id == user_id, OtpChallenge.purpose == purpose, OtpChallenge.consumed_at.is_(None)).values(consumed_at=now))
    cid, code = uuid.uuid4(), f"{secrets.randbelow(10**CODE_DIGITS):0{CODE_DIGITS}d}"
    db.add(OtpChallenge(id=cid, user_id=user_id, purpose=purpose, destination=destination, code_hash=_hash(cid, code), expires_at=now + timedelta(seconds=settings.otp_ttl_seconds)))
    await db.flush()
    return cid, code


async def check_send_limits(db: AsyncSession, user_id: uuid.UUID, ip: str | None) -> None:
    """Raise 429 if this user/IP already requested too many codes. Counts the request; commits so the count sticks."""
    ku, ki = throttle.key("otp-send-user", str(user_id)), throttle.key("otp-send-ip", ip or "unknown")
    if secs := await throttle.remaining(db, ku, ki):
        raise throttle.too_many(secs)
    u = await throttle.hit(db, ku, limit=settings.otp_sends_per_window, window=600, lock=600)
    i = await throttle.hit(db, ki, limit=settings.otp_sends_per_ip_hour, window=3600, lock=3600)
    await db.commit()  # the request that crosses the limit is still served; the next one is refused until the lock expires
    _ = (u, i)


async def deliver(provider: SmsProvider | None, destination: str, code: str) -> None:
    """Send the code. Never retried blindly (the provider has no idempotency key); failures surface as 503."""
    if provider is None:
        raise ProblemError(503, "sms_unavailable", "SMS unavailable", "Text-message codes are not available right now. Use another method.")
    _, text_body = render("otp", {"code": code, "minutes": str(settings.otp_ttl_seconds // 60)})
    try:
        result = await provider.send(destination, text_body, sender_id=settings.thrivewave_sender_id or None, callback_url=None)
        log.info("otp.sms_accepted", to=mask(destination), transaction_id=result.provider_message_id, recipients=result.recipients)
    except SmsUncertain:
        log.warning("otp.sms_uncertain", to=mask(destination))  # may still arrive; the user can request another (rate limited)
    except SmsAuthError as exc:
        log.error("otp.sms_auth_failed", detail="Thrivewave key rejected or expired; rotate THRIVEWAVE_API_KEY")
        raise ProblemError(503, "sms_unavailable", "SMS unavailable", "Text-message codes are not available right now. Use another method.") from exc
    except (SmsTransientError, SmsRejected, SmsError) as exc:
        raise ProblemError(503, "sms_unavailable", "SMS unavailable", "We couldn't send the code. Please try again shortly.") from exc


async def verify(db: AsyncSession, user_id: uuid.UUID, purpose: str, code: str) -> str | None:
    """Return the challenge's destination on success, else None. The caller must commit either way (attempts are counted)."""
    now = datetime.now(UTC)
    ch = (
        await db.execute(
            select(OtpChallenge)
            .where(OtpChallenge.user_id == user_id, OtpChallenge.purpose == purpose, OtpChallenge.consumed_at.is_(None), OtpChallenge.expires_at > now)
            .order_by(OtpChallenge.created_at.desc()).limit(1).with_for_update()
        )
    ).scalar_one_or_none()
    if ch is None:
        return None
    ch.attempts += 1
    if ch.attempts > settings.otp_max_attempts:
        ch.consumed_at = now  # burned: the attacker cannot keep guessing one code
        return None
    if not secrets.compare_digest(ch.code_hash, _hash(ch.id, code.strip())):
        return None
    ch.consumed_at = now
    return ch.destination
