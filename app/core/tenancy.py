"""Tenant context. See docs/03-multi-tenancy.md.

Every request/task runs inside a transaction that sets Postgres session variables used by RLS
policies. Never query tenant tables outside `tenant_session`.
"""
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class TenantContext:
    firm_id: UUID
    user_id: UUID
    role: str
    client_id: UUID | None = None  # set when the caller is a client user


@asynccontextmanager
async def tenant_session(session: AsyncSession, ctx: TenantContext) -> AsyncIterator[AsyncSession]:
    async with session.begin():
        await session.execute(
            text(
                "SELECT set_config('app.firm_id', :firm, true),"
                " set_config('app.client_id', :client, true),"
                " set_config('app.role', :role, true)"
            ),
            {"firm": str(ctx.firm_id), "client": str(ctx.client_id or ""), "role": ctx.role},
        )
        yield session
