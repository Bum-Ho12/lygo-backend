from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import UserId, client_ip, identity_session
from app.core.security import MIN_PASSWORD_LEN
from app.db.models.tenancy import User
from app.domain.users import service

router = APIRouter(tags=["auth"])
Db = Annotated[AsyncSession, Depends(identity_session)]


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=MIN_PASSWORD_LEN, max_length=256)
    name: str = Field(default="", max_length=200)

    @field_validator("password")
    @classmethod
    def not_trivial(cls, v: str) -> str:
        if len(set(v)) < 5:
            raise ValueError("password is too repetitive")
        return v


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


@router.post("/auth/login", response_model=TokenOut)
async def login(body: LoginIn, request: Request, db: Db, user_agent: Annotated[str | None, Header()] = None) -> TokenOut:
    pair = await service.login(db, email=body.email, password=body.password, ip=client_ip(request), ua=user_agent)
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


@router.get("/me", response_model=UserOut)
async def me(user_id: UserId, db: Db) -> UserOut:
    user = await db.get(User, user_id)
    assert user is not None
    return UserOut(id=str(user.id), email=user.email, name=user.name, locale=user.locale)


@router.get("/me/memberships")
async def my_memberships(user_id: UserId, db: Db) -> dict[str, list[dict[str, str]]]:
    return await service.memberships(db, user_id)
