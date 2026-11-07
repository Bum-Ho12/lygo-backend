"""Audit trail. Append-only; every state-changing domain operation records an event."""
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import insert
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
    # Plain INSERT without RETURNING: RETURNING would require SELECT visibility of the new row, which
    # client users (who also generate auditable events, e.g. uploads) intentionally lack.
    await session.execute(
        insert(AuditEvent.__table__)  # type: ignore[arg-type]
        .values(firm_id=ctx.firm_id, actor_id=ctx.user_id, action=action, target=target, ip=ip, at=datetime.now(UTC), meta=meta or {})
        .inline()  # no implicit RETURNING of the generated id
    )
