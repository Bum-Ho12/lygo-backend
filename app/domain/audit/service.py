"""Audit trail. Append-only; every state-changing domain operation records an event."""
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import TenantContext
from app.db.models.audit import AuditEvent


async def record(
    session: AsyncSession,
    ctx: TenantContext,
    action: str,
    *,
    target: str | None = None,
    ip: str | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditEvent(firm_id=ctx.firm_id, actor_id=ctx.user_id, action=action, target=target, ip=ip, meta=meta or {})
    )
    await session.flush()
