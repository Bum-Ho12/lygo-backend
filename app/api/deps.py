"""Request dependencies: identity session, authenticated user, tenant context, permissions."""
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.errors import Forbidden, NotFound, ProblemError
from app.core.permissions import FIRM_ROLES, has_permission
from app.core.security import AccessClaims, InvalidToken, decode_access_claims
from app.core.tenancy import TenantContext, tenant_session
from app.db.models.tenancy import ClientMembership, FirmMembership, User
from app.db.session import make_sessionmaker

_bearer = HTTPBearer(auto_error=False)


def get_factory() -> async_sessionmaker[AsyncSession]:
    """Overridable in tests (app.dependency_overrides)."""
    return make_sessionmaker()


Factory = Annotated[async_sessionmaker[AsyncSession], Depends(get_factory)]


async def identity_session(factory: Factory) -> AsyncIterator[AsyncSession]:
    """Session for identity tables (no tenant context). Handlers commit explicitly."""
    async with factory() as session:
        yield session


def client_ip(request: Request) -> str | None:
    """Real client address for rate limiting. Behind N trusted proxies the address is the N-th entry from the right of
    X-Forwarded-For (anything to its left is attacker-controlled). With no proxies configured the header is ignored."""
    hops = settings.trusted_proxy_hops
    if hops > 0:
        parts = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
        if len(parts) >= hops:
            return parts[-hops]
    return request.client.host if request.client else None


async def current_claims(creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]) -> AccessClaims:
    if creds is None:
        raise ProblemError(401, "unauthorized", "Unauthorized", "Authentication required.")
    try:
        return decode_access_claims(creds.credentials)
    except InvalidToken as exc:
        raise ProblemError(401, "unauthorized", "Unauthorized", "Invalid or expired token.") from exc


Claims = Annotated[AccessClaims, Depends(current_claims)]


async def current_user_id(claims: Claims) -> uuid.UUID:
    return claims.user_id


UserId = Annotated[uuid.UUID, Depends(current_user_id)]


def mfa_gate(claims: AccessClaims) -> None:
    if settings.mfa_required_for_firm_roles and not claims.mfa:
        raise ProblemError(403, "mfa_required", "Second factor required", "Set up and verify two-factor authentication to continue.")


async def firm_context(firm_id: uuid.UUID, claims: Claims, factory: Factory) -> TenantContext:
    """Resolve the caller's role in the path firm. Non-members get 404 so firm ids are not probeable."""
    user_id = claims.user_id
    async with factory() as s:
        user = await s.get(User, user_id)
        if user is None:
            raise ProblemError(401, "unauthorized", "Unauthorized", "Unknown user.")
        if user.platform_role:
            raise NotFound("firm")  # platform staff use /platform routes; no implicit tenant access
        fm = (
            await s.execute(
                select(FirmMembership.role).where(
                    FirmMembership.firm_id == firm_id, FirmMembership.user_id == user_id, FirmMembership.status == "active"
                )
            )
        ).scalar_one_or_none()
        if fm is not None:
            if fm in FIRM_ROLES:
                mfa_gate(claims)  # staff must have used a second factor (docs/07); client users may opt in
            return TenantContext(firm_id=firm_id, user_id=user_id, role=fm)
        cm = (
            await s.execute(
                select(ClientMembership.client_id, ClientMembership.role).where(
                    ClientMembership.firm_id == firm_id, ClientMembership.user_id == user_id
                )
            )
        ).first()
        if cm is not None:
            return TenantContext(firm_id=firm_id, user_id=user_id, role=cm.role, client_id=cm.client_id)
    raise NotFound("firm")


Ctx = Annotated[TenantContext, Depends(firm_context)]


def require(permission: str) -> Callable[[TenantContext], TenantContext]:
    def checker(ctx: Ctx) -> TenantContext:
        if not has_permission(ctx.role, permission):
            raise Forbidden(f"Missing permission: {permission}")
        return ctx

    return checker


async def tenant_db(ctx: Ctx, factory: Factory) -> AsyncIterator[AsyncSession]:
    """Transactional session with RLS context for the path firm; commits on success."""
    async with tenant_session(factory, ctx) as session:
        yield session
