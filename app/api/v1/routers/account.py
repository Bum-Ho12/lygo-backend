"""Authenticated account security: phone verification, authenticator app (TOTP), SMS second factor, password change."""
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Claims, UserId, client_ip, identity_session
from app.api.v1.routers.auth import TokenOut, _strong
from app.core.crypto import KeyProvider, get_key_provider
from app.core.errors import ProblemError
from app.core.security import MIN_PASSWORD_LEN, hash_password, verify_password
from app.db.models.tenancy import User
from app.domain.users import accounts, mfa, otp, service, throttle
from app.integrations.email.base import EmailSender, get_email_sender
from app.integrations.sms.base import SmsProvider
from app.integrations.sms.factory import get_sms_provider
from app.integrations.sms.phone import InvalidPhone, mask, normalize_ke

router = APIRouter(prefix="/me", tags=["account"])
Db = Annotated[AsyncSession, Depends(identity_session)]
Keys = Annotated[KeyProvider, Depends(get_key_provider)]


class PhoneIn(BaseModel):
    phone: str = Field(min_length=7, max_length=20)


class CodeIn(BaseModel):
    code: str = Field(min_length=4, max_length=32)


class PasswordConfirm(BaseModel):
    password: str = Field(max_length=256)
    code: str | None = Field(default=None, max_length=32)  # current TOTP / recovery code when an authenticator is enabled


class SmsToggle(BaseModel):
    enabled: bool
    password: str = Field(max_length=256)


class ChangePasswordIn(BaseModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(min_length=MIN_PASSWORD_LEN, max_length=256)

    _check = field_validator("new_password")(_strong)


async def _user(db: AsyncSession, user_id: object) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise ProblemError(401, "unauthorized", "Unauthorized", "Unknown user.")
    return user


async def _reauth(db: AsyncSession, keys: KeyProvider, user: User, password: str, code: str | None) -> None:
    """Sensitive changes need the password and, when an authenticator is on, a current code (so a stolen session alone is not enough)."""
    if not verify_password(password, user.password_hash):
        raise ProblemError(403, "reauth_failed", "Confirmation failed", "Your password is not correct.")
    if user.mfa_totp_enabled_at is not None:
        ok = bool(code) and (await mfa.verify_totp(db, keys, user, code or "") or await mfa.use_recovery_code(db, user.id, code or ""))
        if not ok:
            await db.commit()  # keep the replay-protection / attempt state
            raise ProblemError(403, "reauth_failed", "Confirmation failed", "A current authenticator code is required.")


@router.get("/security")
async def security_summary(user_id: UserId, claims: Claims, db: Db) -> dict[str, object]:
    u = await _user(db, user_id)
    return {
        "email_verified": u.email_verified_at is not None, "phone": mask(u.phone) if u.phone else None, "phone_verified": u.phone_verified_at is not None,
        "totp_enabled": u.mfa_totp_enabled_at is not None, "sms_enabled": u.mfa_sms_enabled,
        "recovery_codes_remaining": await mfa.recovery_codes_remaining(db, u.id), "session_mfa": claims.mfa,
    }


# --- phone verification (also the prerequisite for SMS codes) ------------------------------------------------------
@router.post("/phone", status_code=202)
async def start_phone_verification(body: PhoneIn, user_id: UserId, request: Request, db: Db, sms: Annotated[SmsProvider | None, Depends(get_sms_provider)]) -> dict[str, str]:
    try:
        number = normalize_ke(body.phone)
    except InvalidPhone as exc:
        raise ProblemError(422, "invalid_phone", "Invalid phone number", "Enter a Kenyan mobile number, e.g. 0712 345 678.") from exc
    await otp.check_send_limits(db, user_id, client_ip(request))
    _, code = await otp.issue(db, user_id, "verify_phone", number)
    await db.commit()
    await otp.deliver(sms, number, code)
    return {"sent_to": mask(number)}


@router.post("/phone/verify")
async def verify_phone(body: CodeIn, user_id: UserId, db: Db, email: Annotated[EmailSender, Depends(get_email_sender)]) -> dict[str, str]:
    user = await _user(db, user_id)
    if secs := await throttle.remaining(db, throttle.key("otp-verify", str(user_id))):
        raise throttle.too_many(secs)
    number = await otp.verify(db, user.id, "verify_phone", body.code)
    if number is None:
        await throttle.hit(db, throttle.key("otp-verify", str(user_id)), limit=10, window=900, lock=900)
        await db.commit()
        raise ProblemError(422, "invalid_code", "Invalid code", "That code is not correct or has expired.")
    user.phone, user.phone_verified_at = number, datetime.now(UTC)
    await db.commit()
    await accounts.send_alert(email, user.email, "A new phone number was verified")
    return {"phone": mask(number), "status": "verified"}


@router.post("/mfa/sms")
async def toggle_sms_factor(body: SmsToggle, user_id: UserId, db: Db, email: Annotated[EmailSender, Depends(get_email_sender)]) -> dict[str, bool]:
    user = await _user(db, user_id)
    if not verify_password(body.password, user.password_hash):
        raise ProblemError(403, "reauth_failed", "Confirmation failed", "Your password is not correct.")
    if body.enabled and (user.phone_verified_at is None or not user.phone):
        raise ProblemError(409, "phone_not_verified", "Phone not verified", "Verify your phone number first.")
    user.mfa_sms_enabled = body.enabled
    await db.commit()
    await accounts.send_alert(email, user.email, "Two-factor authentication was turned on" if body.enabled else "Two-factor authentication was turned off")
    return {"sms_enabled": user.mfa_sms_enabled}


# --- authenticator app (TOTP) ---------------------------------------------------------------------------------------
@router.post("/mfa/totp/enroll")
async def totp_enroll(user_id: UserId, db: Db, keys: Keys) -> dict[str, str]:
    user = await _user(db, user_id)
    secret, uri = await mfa.begin_enrolment(db, keys, user)
    await db.commit()
    return {"secret": secret, "otpauth_uri": uri}


@router.post("/mfa/totp/confirm")
async def totp_confirm(body: CodeIn, user_id: UserId, db: Db, keys: Keys, email: Annotated[EmailSender, Depends(get_email_sender)]) -> dict[str, object]:
    """Activates the authenticator, returns the recovery codes ONCE, and upgrades this session to 'second factor verified'."""
    user = await _user(db, user_id)
    try:
        codes = await mfa.confirm_enrolment(db, keys, user, body.code)
    except ProblemError:
        await db.commit()
        raise
    await accounts.revoke_all_sessions(db, user.id)  # existing refresh tokens predate the second factor
    pair = await service.issue_tokens(db, user.id, None, None, None, mfa=True)
    await db.commit()
    await accounts.send_alert(email, user.email, "Two-factor authentication was turned on")
    return {"recovery_codes": codes, "access_token": pair.access_token, "refresh_token": pair.refresh_token, "token_type": "bearer"}


@router.post("/mfa/totp/disable", status_code=204)
async def totp_disable(body: PasswordConfirm, user_id: UserId, db: Db, keys: Keys, email: Annotated[EmailSender, Depends(get_email_sender)]) -> None:
    user = await _user(db, user_id)
    if user.mfa_totp_enabled_at is None:
        raise ProblemError(409, "totp_not_enrolled", "Not enrolled", "Authenticator app is not set up.")
    await _reauth(db, keys, user, body.password, body.code)
    await mfa.disable_totp(db, user)
    await accounts.revoke_all_sessions(db, user.id)
    await db.commit()
    await accounts.send_alert(email, user.email, "Two-factor authentication was turned off")


@router.post("/mfa/recovery-codes")
async def regenerate_recovery(body: PasswordConfirm, user_id: UserId, db: Db, keys: Keys, email: Annotated[EmailSender, Depends(get_email_sender)]) -> dict[str, list[str]]:
    user = await _user(db, user_id)
    if user.mfa_totp_enabled_at is None:
        raise ProblemError(409, "totp_not_enrolled", "Not enrolled", "Set up the authenticator app first.")
    await _reauth(db, keys, user, body.password, body.code)
    codes = await mfa.regenerate_recovery_codes(db, user.id)
    await db.commit()
    await accounts.send_alert(email, user.email, "Your recovery codes were regenerated")
    return {"recovery_codes": codes}


# --- password ------------------------------------------------------------------------------------------------------------------
@router.post("/password", response_model=TokenOut)
async def change_password(
    body: ChangePasswordIn, user_id: UserId, claims: Claims, db: Db, email: Annotated[EmailSender, Depends(get_email_sender)],
    user_agent: Annotated[str | None, Header()] = None,
) -> TokenOut:
    user = await _user(db, user_id)
    if not verify_password(body.current_password, user.password_hash):
        raise ProblemError(403, "reauth_failed", "Confirmation failed", "Your current password is not correct.")
    user.password_hash, user.password_changed_at = hash_password(body.new_password), datetime.now(UTC)
    await accounts.revoke_all_sessions(db, user.id)  # signs out every other device
    pair = await service.issue_tokens(db, user.id, None, None, user_agent, mfa=claims.mfa)  # this session continues, keeping its MFA status
    await db.commit()
    await accounts.send_alert(email, user.email, "Your password was changed")
    return TokenOut(**pair.__dict__)

