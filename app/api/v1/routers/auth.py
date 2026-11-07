from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Request
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import UserId, client_ip, identity_session
from app.core.crypto import KeyProvider, get_key_provider
from app.core.errors import ProblemError
from app.core.security import MIN_PASSWORD_LEN, InvalidToken, decode_mfa_token
from app.db.models.tenancy import User
from app.domain.users import accounts, otp, service, throttle
from app.integrations.email.base import EmailSender, get_email_sender
from app.integrations.sms.base import SmsProvider
from app.integrations.sms.factory import get_sms_provider
from app.integrations.sms.phone import mask

router = APIRouter(tags=["auth"])
Db = Annotated[AsyncSession, Depends(identity_session)]


def _strong(v: str) -> str:
    if len(set(v)) < 5:
        raise ValueError("password is too repetitive")
    return v


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=MIN_PASSWORD_LEN, max_length=256)
    name: str = Field(default="", max_length=200)

    _check = field_validator("password")(_strong)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=256)


class RefreshIn(BaseModel):
    refresh_token: str


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str
    expires_in: int


class LoginOut(BaseModel):
    """Either tokens (no second factor on the account) or `mfa_required` with a short-lived `mfa_token`."""

    access_token: str | None = None
    refresh_token: str | None = None
    token_type: str | None = None
    expires_in: int | None = None
    mfa_required: bool = False
    mfa_token: str | None = None
    methods: list[str] = []


class MfaSendIn(BaseModel):
    mfa_token: str


class MfaVerifyIn(BaseModel):
    mfa_token: str
    method: Literal["totp", "sms", "recovery"]
    code: str = Field(min_length=4, max_length=32)


class ForgotIn(BaseModel):
    email: EmailStr


class ResetIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    new_password: str = Field(min_length=MIN_PASSWORD_LEN, max_length=256)

    _check = field_validator("new_password")(_strong)


class UserOut(BaseModel):
    id: str
    email: str
    name: str
    locale: str


@router.post("/auth/register", status_code=201, response_model=UserOut)
async def register(body: RegisterIn, db: Db) -> UserOut:
    user = await service.register_user(db, email=body.email, password=body.password, name=body.name)
    await db.commit()
    return UserOut(id=str(user.id), email=user.email, name=user.name, locale=user.locale)


@router.post("/auth/login", response_model=LoginOut)
async def login(body: LoginIn, request: Request, db: Db, user_agent: Annotated[str | None, Header()] = None) -> LoginOut:
    res = await service.login(db, email=body.email, password=body.password, ip=client_ip(request), ua=user_agent)
    await db.commit()
    if isinstance(res, service.MfaChallenge):
        return LoginOut(mfa_required=True, mfa_token=res.mfa_token, methods=res.methods)
    return LoginOut(**res.__dict__)


@router.post("/auth/mfa/sms/send", status_code=202)
async def mfa_sms_send(body: MfaSendIn, request: Request, db: Db, sms: Annotated[SmsProvider | None, Depends(get_sms_provider)]) -> dict[str, str]:
    """Send the login code to the user's VERIFIED phone only: the destination is never taken from the request."""
    try:
        uid = decode_mfa_token(body.mfa_token)
    except InvalidToken as exc:
        raise ProblemError(401, "unauthorized", "Unauthorized", "Your sign-in session expired. Please sign in again.") from exc
    user = await db.get(User, uid)
    if user is None or "sms" not in service.available_methods(user) or not user.phone:
        raise ProblemError(409, "sms_not_available", "SMS not available", "Text-message verification is not set up for this account.")
    await otp.check_send_limits(db, user.id, client_ip(request))
    _, code = await otp.issue(db, user.id, "login", user.phone)
    await db.commit()
    await otp.deliver(sms, user.phone, code)
    return {"sent_to": mask(user.phone)}


@router.post("/auth/mfa/verify", response_model=TokenOut)
async def mfa_verify(
    body: MfaVerifyIn, request: Request, db: Db, keys: Annotated[KeyProvider, Depends(get_key_provider)], user_agent: Annotated[str | None, Header()] = None
) -> TokenOut:
    pair = await service.complete_mfa(db, keys, mfa_token=body.mfa_token, method=body.method, code=body.code, ip=client_ip(request), ua=user_agent)
    await db.commit()
    return TokenOut(**pair.__dict__)


@router.post("/auth/refresh", response_model=TokenOut)
async def refresh(body: RefreshIn, request: Request, db: Db, user_agent: Annotated[str | None, Header()] = None) -> TokenOut:
    pair = await service.refresh(db, refresh_token=body.refresh_token, ip=client_ip(request), ua=user_agent)
    await db.commit()
    return TokenOut(**pair.__dict__)


@router.post("/auth/logout", status_code=204)
async def logout(body: RefreshIn, db: Db) -> None:
    await service.logout(db, refresh_token=body.refresh_token)
    await db.commit()


@router.post("/auth/password/forgot", status_code=202)
async def forgot_password(
    body: ForgotIn, request: Request, db: Db, background: BackgroundTasks, email: Annotated[EmailSender, Depends(get_email_sender)]
) -> dict[str, str]:
    """Always answers the same way (no account enumeration). Work and email delivery happen after the response."""
    ip = client_ip(request)
    ke, ki = throttle.key("forgot-email", body.email), throttle.key("forgot-ip", ip or "unknown")
    allowed = not await throttle.remaining(db, ke, ki)
    if allowed:
        await throttle.hit(db, ke, limit=3, window=3600, lock=3600)
        await throttle.hit(db, ki, limit=20, window=3600, lock=3600)
        created = await accounts.create_reset(db, body.email, ip)
        await db.commit()
        if created:
            background.add_task(accounts.send_reset_email, email, created[0], created[1])
    return {"status": "If an account exists for that address, a reset link has been sent."}


@router.post("/auth/password/reset", status_code=204)
async def reset_password(body: ResetIn, db: Db, email: Annotated[EmailSender, Depends(get_email_sender)]) -> None:
    user = await accounts.redeem_reset(db, body.token, body.new_password)
    await db.commit()
    await accounts.send_alert(email, user.email, "Your password was changed")


@router.get("/me", response_model=UserOut)
async def me(user_id: UserId, db: Db) -> UserOut:
    user = await db.get(User, user_id)
    assert user is not None
    return UserOut(id=str(user.id), email=user.email, name=user.name, locale=user.locale)


@router.get("/me/memberships")
async def my_memberships(user_id: UserId, db: Db) -> dict[str, list[dict[str, str]]]:
    return await service.memberships(db, user_id)

