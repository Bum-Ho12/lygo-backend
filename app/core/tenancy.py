"""Tenant context. See docs/03-multi-tenancy.md.

Every request/task runs inside ONE explicit transaction that sets Postgres session variables used by
RLS policies (`SET LOCAL` semantics via set_config(..., true)), so nothing leaks across pooled
connections. Never query tenant tables outside `tenant_session` / `platform_session`.
"""
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

PLATFORM_ROLES = frozenset({"platform_admin", "platform_support"})


@dataclass(frozen=True)
class TenantContext:
    firm_id: UUID | None
    user_id: UUID
    role: str
    client_id: UUID | None = None  # set when the caller is a client user

    def __post_init__(self) -> None:
        if self.firm_id is None and self.role not in PLATFORM_ROLES:
            raise ValueError("firm_id is required for non-platform roles")
        if self.client_id is not None and self.firm_id is None:
            raise ValueError("client_id requires firm_id")


async def _apply(session: AsyncSession, ctx: TenantContext) -> None:
    await session.execute(
        text(
            "SELECT set_config('app.firm_id', :firm, true),"
            " set_config('app.client_id', :client, true),"
            " set_config('app.role', :role, true),"
            " set_config('app.user_id', :user, true)"
        ),
        {
            "firm": str(ctx.firm_id or ""),
            "client": str(ctx.client_id or ""),
            "role": ctx.role,
            "user": str(ctx.user_id),
        },
    )


@asynccontextmanager
async def tenant_session(
    factory: async_sessionmaker[AsyncSession], ctx: TenantContext
) -> AsyncIterator[AsyncSession]:
    """Open a session + transaction with the tenant context applied. Commits on success."""
    async with factory() as session, session.begin():
        await _apply(session, ctx)
        yield session
